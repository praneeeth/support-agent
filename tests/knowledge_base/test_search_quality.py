"""Search quality with the real embedding model. Skipped when the model can't be loaded
(e.g. no network access to huggingface.co); runs in CI and on developer machines."""

import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.knowledge_base.ingest import ingest_docs
from app.knowledge_base.search import KnowledgeBase

pytestmark = pytest.mark.slow

QUERIES = json.loads(Path("tests/knowledge_base/queries.json").read_text())


@pytest.fixture(scope="module")
def embedder():  # type: ignore[no-untyped-def]
    try:
        from app.knowledge_base.embed import FastEmbedder

        return FastEmbedder(get_settings().embedding_model)
    except Exception as exc:  # noqa: BLE001 - any download/load failure means skip
        pytest.skip(f"embedding model unavailable: {exc.__class__.__name__}")


@pytest.fixture
def kb(session: Session, embedder) -> KnowledgeBase:  # type: ignore[no-untyped-def]
    ingest_docs(session, Path("data/docs"), embedder=embedder)
    kb = KnowledgeBase.load(session, embedder)
    assert kb.hybrid
    return kb


def test_expected_doc_in_top3_for_18_of_20(kb: KnowledgeBase) -> None:
    misses = []
    for q in QUERIES:
        top3 = [h.source_path.removesuffix(".md") for h in kb.search(q["q"], k=3)]
        if q["doc"] not in top3:
            misses.append((q["q"], q["doc"], top3))
    assert len(QUERIES) - len(misses) >= 18, misses


def test_paraphrase_returns_doc_first(kb: KnowledgeBase) -> None:
    assert kb.search("how long do returns take")[0].source_path == "returns-policy.md"


def test_off_topic_below_threshold(kb: KnowledgeBase) -> None:
    hits = kb.search("what's the weather in Paris")
    assert not hits or hits[0].score < get_settings().kb_min_score
