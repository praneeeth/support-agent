"""LLM-as-judge for one question only: is this answer faithful to the documents it cited?

Deterministic checks decide everything else. The judge reads the cited documents in full and must
reply with a verdict word first, so the parse is a string check, not an interpretation.
"""

from pathlib import Path

from app.agent.llm import AnthropicLLM, LLMClient, LLMError
from app.agent.openai_compat import OpenAICompatLLM
from app.config import Settings

JUDGE_PROMPT = """You check a customer-support answer against the documents it was based on.

Reply on one line: FAITHFUL or UNFAITHFUL, then " - " and one sentence of reason.
- FAITHFUL: every factual claim in the answer (numbers, dates, prices, conditions, yes/no) is \
stated in or directly follows from the documents. Leaving things out is fine.
- UNFAITHFUL: any claim is missing from, or contradicts, the documents."""


def build_judge(settings: Settings) -> LLMClient | None:
    """None when no judge is configured: the run then uses deterministic checks only."""
    if not settings.judge_provider:
        return None
    if not settings.judge_model:
        raise ValueError("JUDGE_MODEL is not set")
    if settings.judge_provider == "anthropic":
        key = settings.judge_api_key or settings.anthropic_api_key
        return AnthropicLLM(api_key=key, model=settings.judge_model)
    if settings.judge_provider == "openai_compatible":
        return OpenAICompatLLM(
            base_url=settings.judge_base_url or settings.llm_base_url,
            model=settings.judge_model,
            api_key=settings.judge_api_key or settings.llm_api_key,
        )
    raise ValueError(f"Unknown JUDGE_PROVIDER {settings.judge_provider!r}")


async def judge_answer(
    judge: LLMClient, question: str, answer: str, docs_dir: Path, sources: list[str]
) -> tuple[bool, str]:
    """(faithful, verdict line). A judge error counts as not faithful, with the reason."""
    documents = []
    for name in sources:
        path = docs_dir / name
        if path.is_file():
            documents.append(f"<document name={name!r}>\n{path.read_text(encoding='utf-8')}\n"
                             "</document>")  # fmt: skip
    if not documents:
        return True, "SKIPPED - answer came from a tool, not a document"
    content = (
        "\n\n".join(documents) + f"\n\n<question>{question}</question>\n<answer>{answer}</answer>"
    )
    try:
        response = await judge.complete(
            system=JUDGE_PROMPT,
            messages=[{"role": "user", "content": content}],
            max_tokens=4000,  # room for models that think before answering
        )
    except LLMError as exc:
        return False, f"JUDGE ERROR - {exc}"
    verdict = response.text.strip().splitlines()[0] if response.text.strip() else ""
    return verdict.upper().startswith("FAITHFUL"), verdict or "EMPTY - judge returned no text"
