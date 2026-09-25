"""Hybrid-search mechanics with the deterministic HashingEmbedder (no model download)."""

import time
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.knowledge_base.embed import HashingEmbedder, from_blob
from app.knowledge_base.ingest import embed_missing, ingest_docs
from app.knowledge_base.models import KbChunk
from app.knowledge_base.search import KnowledgeBase

DOCS = Path("verticals/northwind/docs")


@pytest.fixture
def kb(session: Session) -> KnowledgeBase:
    ingest_docs(session, DOCS, embedder=HashingEmbedder())
    return KnowledgeBase.load(session, HashingEmbedder())


def test_every_chunk_gets_a_normalised_vector(session: Session) -> None:
    ingest_docs(session, DOCS, embedder=HashingEmbedder())
    missing = session.scalar(select(func.count(KbChunk.id)).where(KbChunk.embedding.is_(None)))
    assert missing == 0
    chunk = session.scalars(select(KbChunk)).first()
    assert chunk is not None and chunk.embedding is not None
    assert abs(float((from_blob(chunk.embedding) ** 2).sum()) - 1.0) < 1e-5


def test_embed_missing_backfills(session: Session) -> None:
    ingest_docs(session, DOCS)  # no embedder
    assert embed_missing(session, HashingEmbedder()) > 0
    assert embed_missing(session, HashingEmbedder()) == 0


def test_hybrid_enabled_only_when_all_vectors_present(session: Session) -> None:
    ingest_docs(session, DOCS)
    assert not KnowledgeBase.load(session, HashingEmbedder()).hybrid
    embed_missing(session, HashingEmbedder())
    assert KnowledgeBase.load(session, HashingEmbedder()).hybrid
    assert not KnowledgeBase.load(session).hybrid


def test_scores_in_unit_interval_and_sorted_by_fusion(kb: KnowledgeBase) -> None:
    hits = kb.search("return window 30 days")
    assert hits and all(0.0 <= h.score <= 1.0 for h in hits)
    assert hits[0].source_path == "returns-policy.md"


def test_k_limits_results(kb: KnowledgeBase) -> None:
    assert len(kb.search("shipping", k=3)) == 3


def test_empty_query(kb: KnowledgeBase) -> None:
    assert kb.search("") == []


def test_search_latency_p95_under_50ms(kb: KnowledgeBase) -> None:
    timings = []
    for q in ["shipping cost", "gift card validity", "cast iron rust", "queen bed size"] * 10:
        t = time.perf_counter()
        kb.search(q)
        timings.append(time.perf_counter() - t)
    timings.sort()
    assert timings[int(len(timings) * 0.95) - 1] < 0.050
