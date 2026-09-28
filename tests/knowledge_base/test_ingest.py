from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.knowledge_base.ingest import chunk_markdown, ingest_docs, parse_doc
from app.knowledge_base.models import KbChunk, KbDocument
from app.knowledge_base.search import KnowledgeBase

DOCS = Path("verticals/northwind/docs")

SAMPLE = """---
title: Returns policy
category: returns
updated: 2026-09-01
---

# Returns policy

## Return window
You can return most items within 30 days of delivery.

## How to return
Returns are arranged by our support team.
"""


def test_parse_front_matter() -> None:
    doc = parse_doc(SAMPLE)
    assert doc.title == "Returns policy"
    assert doc.category == "returns"
    assert "## Return window" in doc.body


def test_chunk_by_heading() -> None:
    chunks = chunk_markdown("Returns policy", parse_doc(SAMPLE).body)
    assert [c.heading for c in chunks] == ["Return window", "How to return"]
    assert chunks[0].text.startswith("Returns policy — Return window")


def test_long_section_is_split_with_overlap() -> None:
    body = "## Big\n" + " ".join(f"w{i}" for i in range(900))
    chunks = chunk_markdown("T", body, max_words=300, overlap_words=40)
    assert len(chunks) >= 3
    first_words = chunks[0].text.split()
    second_words = chunks[1].text.split()
    assert first_words[-40:] == second_words[3 : 3 + 40]  # after "T — Big" prefix


def test_ingest_creates_chunks(session: Session) -> None:
    stats = ingest_docs(session, DOCS)
    assert stats.documents_written >= 30
    assert session.scalar(select(func.count(KbDocument.id))) >= 30
    assert session.scalar(select(func.count(KbChunk.id))) > stats.documents_written


def test_reingest_unchanged_writes_nothing(session: Session) -> None:
    ingest_docs(session, DOCS)
    stats = ingest_docs(session, DOCS)
    assert stats.documents_written == 0
    assert stats.chunks_written == 0


def test_editing_one_doc_rewrites_only_that_doc(session: Session, tmp_path: Path) -> None:
    for p in DOCS.glob("*.md"):
        (tmp_path / p.name).write_text(p.read_text())
    ingest_docs(session, tmp_path)
    target = tmp_path / "gift-cards.md"
    target.write_text(target.read_text() + "\n- Gift cards can be sent on a scheduled date.\n")
    stats = ingest_docs(session, tmp_path)
    assert stats.documents_written == 1
    assert stats.updated_paths == ["gift-cards.md"]


def test_removed_doc_is_deleted(session: Session, tmp_path: Path) -> None:
    for p in list(DOCS.glob("*.md"))[:3]:
        (tmp_path / p.name).write_text(p.read_text())
    ingest_docs(session, tmp_path)
    removed = sorted(tmp_path.glob("*.md"))[0]
    removed.unlink()
    stats = ingest_docs(session, tmp_path)
    assert stats.documents_deleted == 1
    paths = set(session.scalars(select(KbDocument.source_path)))
    assert removed.name not in paths


def test_keyword_search_returns_returns_doc_first(session: Session) -> None:
    # Keyword-only baseline. The paraphrased "how long do returns take" is asserted against
    # hybrid search in test_search_quality.py (BM25 alone ranks "as long as" text higher).
    ingest_docs(session, DOCS)
    kb = KnowledgeBase.load(session)
    hits = kb.search("return window for items")
    assert hits
    assert hits[0].source_path == "returns-policy.md"
    assert 0.0 <= hits[0].score <= 1.0


def test_search_empty_query(session: Session) -> None:
    ingest_docs(session, DOCS)
    assert KnowledgeBase.load(session).search("   ") == []
