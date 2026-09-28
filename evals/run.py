"""Run a vertical's golden set end to end and gate on the scores.

    uv run python -m evals.run [--vertical northwind] [--min-answer 0.90] [--min-escalation 0.95]
                               [--summary evals-summary.md] [--only id-or-category,...] [--limit N]

Every case runs in its own conversation against a freshly seeded throwaway database, through the
real agent and the configured model. Exit code 1 when a threshold is missed or anything leaks.
Thresholds on the command line can only raise the vertical's own bar, never lower it.
"""

import argparse
import asyncio
import json
import logging
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.agent.core import Agent, AgentReply
from app.agent.llm import LLMClient
from app.agent.routes import build_llm
from app.config import Settings, get_settings
from app.db import init_db, make_engine
from app.handoff.models import Channel
from app.knowledge_base.embed import Embedder, FastEmbedder
from app.knowledge_base.search import KnowledgeBase
from app.seed import seed_all
from app.verticals.config import VerticalConfig, load_vertical
from evals.cases import Case, load_cases
from evals.judge import build_judge, judge_answer
from evals.score import (
    CaseResult,
    Totals,
    forbidden_terms,
    load_order_facts,
    reply_text,
    score_case,
    totals,
)

log = logging.getLogger("evals")


@dataclass
class Report:
    vertical: str
    model: str
    started: str
    seconds: float
    min_answer: float
    min_escalation: float
    totals: Totals
    gate_failures: list[str]
    results: list[CaseResult]

    @property
    def passed(self) -> bool:
        return not self.gate_failures


async def run_eval(
    cases: list[Case],
    vertical: VerticalConfig,
    llm: LLMClient,
    settings: Settings,
    *,
    judge: LLMClient | None = None,
    embedder: Embedder | None = None,
    concurrency: int = 5,
    min_answer: float | None = None,
    min_escalation: float | None = None,
    model_name: str = "",
) -> Report:
    started = time.monotonic()
    min_answer = max(min_answer or 0.0, vertical.evals.min_answer)
    min_escalation = max(min_escalation or 0.0, vertical.evals.min_escalation)

    with tempfile.TemporaryDirectory() as tmp:
        engine = make_engine(f"sqlite:///{Path(tmp) / 'eval.db'}")
        init_db(engine)
        make_session = sessionmaker(engine, expire_on_commit=False)
        with make_session() as session:
            seed_all(session, embedder, vertical)
            kb = KnowledgeBase.load(session, embedder)
            orders = load_order_facts(session)

        gate = asyncio.Semaphore(concurrency)

        async def one(case: Case) -> CaseResult:
            async with gate:
                with make_session() as session:
                    agent = Agent(
                        session=session, kb=kb, llm=llm, settings=settings, vertical=vertical
                    )
                    cid = f"eval-{uuid.uuid4().hex[:12]}"
                    replies: list[AgentReply] = []
                    try:
                        for turn in case.turns:
                            replies.append(
                                await agent.handle_message(cid, Channel.webchat, turn, "eval")
                            )
                    except Exception as exc:  # noqa: BLE001 - one broken case must not stop the run
                        log.warning("%s errored: %s", case.id, exc.__class__.__name__)
                        return CaseResult(
                            case.id, case.category, case.expect.is_escalation, False,
                            error=f"{exc.__class__.__name__}: {exc}",
                            failures=[f"error: {exc.__class__.__name__}"],
                        )  # fmt: skip
                    all_text = "\n".join(reply_text(r) for r in replies)
                    result = score_case(
                        case, replies, forbidden_terms(case.turns, orders), all_text
                    )
                    final = replies[-1]
                    if judge is not None and result.passed and final.kind == "answer":
                        faithful, verdict = await judge_answer(
                            judge, case.turns[-1], final.text, Path(vertical.docs_dir),
                            list(final.sources),
                        )  # fmt: skip
                        result.judge = verdict
                        if not faithful:
                            result.passed = False
                            result.failures.append(f"judge: {verdict}")
                    log.info("%s %s", "PASS" if result.passed else "FAIL", case.id)
                    return result

        results = list(await asyncio.gather(*(one(c) for c in cases)))
        engine.dispose()

    t = totals(results)
    return Report(
        vertical=vertical.id,
        model=model_name,
        started=datetime.now(UTC).isoformat(timespec="seconds"),
        seconds=round(time.monotonic() - started, 1),
        min_answer=min_answer,
        min_escalation=min_escalation,
        totals=t,
        gate_failures=t.gate(min_answer, min_escalation),
        results=results,
    )


def summary_markdown(report: Report, max_failures: int = 40) -> str:
    t = report.totals
    verdict = "✅ Evals passed" if report.passed else "❌ Evals failed"

    def mark(ok: bool) -> str:
        return "✅" if ok else "❌"

    lines = [
        f"## {verdict} — `{report.vertical}`",
        "",
        f"Model `{report.model or 'unknown'}` · {len(report.results)} cases · {report.seconds}s",
        "",
        "| Metric | Score | Bar | |",
        "|---|---|---|---|",
        f"| Answer correctness | {t.answer:.1%} ({t.answer_n} cases) | ≥ {report.min_answer:.0%} "
        f"| {mark(t.answer >= report.min_answer)} |",
        f"| Escalation accuracy | {t.escalation:.1%} ({t.escalation_n} cases) "
        f"| ≥ {report.min_escalation:.0%} | {mark(t.escalation >= report.min_escalation)} |",
        f"| Cross-customer leaks | {t.leaks} | 0 | {mark(t.leaks == 0)} |",
    ]
    if t.errors:
        lines += ["", f"{t.errors} case(s) errored and count as failures."]
    failed = [r for r in report.results if not r.passed]
    if failed:
        lines += ["", "<details><summary>Failures</summary>", "",
                  "| Case | Got | Why |", "|---|---|---|"]  # fmt: skip
        for r in failed[:max_failures]:
            got = r.kind + (f" ({r.reason})" if r.reason else "")
            why = "; ".join(r.failures).replace("|", "\\|")
            lines.append(f"| `{r.id}` | {got} | {why} |")
        if len(failed) > max_failures:
            lines.append(f"| … | | {len(failed) - max_failures} more in the results file |")
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def _select(cases: list[Case], only: str, limit: int) -> list[Case]:
    if only:
        wanted = {w.strip() for w in only.split(",") if w.strip()}
        cases = [c for c in cases if c.id in wanted or c.category in wanted]
    return cases[:limit] if limit else cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a vertical's golden set.")
    parser.add_argument("--vertical", default="")
    parser.add_argument("--min-answer", type=float, default=None)
    parser.add_argument("--min-escalation", type=float, default=None)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--only", default="", help="comma-separated case ids or categories")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--summary", default="", help="write a markdown summary here")
    parser.add_argument("--out", default="evals/results")
    parser.add_argument("--no-judge", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    settings = get_settings()
    vertical = load_vertical(args.vertical or settings.vertical)
    cases = _select(load_cases(vertical.evals.path), args.only, args.limit)
    if not cases:
        log.error("No cases selected from %s", vertical.evals.path)
        return 2
    llm = build_llm(settings)
    judge = None if args.no_judge else build_judge(settings)
    try:
        embedder: Embedder | None = FastEmbedder(settings.embedding_model)
    except Exception as exc:  # noqa: BLE001 - keyword search still works
        log.warning("Embedding model unavailable (%s); keyword search only.", exc)
        embedder = None
    model = settings.anthropic_model if settings.llm_provider == "anthropic" else settings.llm_model

    log.info("Running %d cases for %s on %s", len(cases), vertical.id, model)
    report = asyncio.run(
        run_eval(
            cases, vertical, llm, settings,
            judge=judge, embedder=embedder, concurrency=args.concurrency,
            min_answer=args.min_answer, min_escalation=args.min_escalation, model_name=model,
        )
    )  # fmt: skip

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"{vertical.id}-{stamp}.json"
    path.write_text(json.dumps(asdict(report), indent=2, default=str), encoding="utf-8")
    summary = summary_markdown(report)
    if args.summary:
        Path(args.summary).write_text(summary, encoding="utf-8")
    print(summary)
    log.info("Results: %s", path)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
