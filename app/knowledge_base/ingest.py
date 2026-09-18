"""Load markdown docs into kb_documents / kb_chunks. Idempotent per document hash."""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.knowledge_base.embed import Embedder, to_blob
from app.knowledge_base.models import KbChunk, KbDocument

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


@dataclass(frozen=True)
class ParsedDoc:
    title: str
    category: str
    body: str


@dataclass(frozen=True)
class Chunk:
    heading: str
    text: str


@dataclass
class IngestStats:
    documents_written: int = 0
    documents_deleted: int = 0
    chunks_written: int = 0
    updated_paths: list[str] = field(default_factory=list)


def parse_doc(raw: str) -> ParsedDoc:
    meta: dict[str, str] = {}
    body = raw
    m = _FRONT_MATTER.match(raw)
    if m:
        for line in m.group(1).splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
        body = raw[m.end() :]
    title = meta.get("title") or _first_h1(body) or "Untitled"
    return ParsedDoc(title=title, category=meta.get("category", "general"), body=body)


def _first_h1(body: str) -> str | None:
    m = re.search(r"^# (.+)$", body, re.MULTILINE)
    return m.group(1).strip() if m else None


def _sections(body: str) -> list[tuple[str, str]]:
    """Split on '## ' headings. Text before the first '##' becomes an 'Overview' section."""
    body = re.sub(r"^# .+\n", "", body, count=1, flags=re.MULTILINE)
    parts = re.split(r"^## (.+)$", body, flags=re.MULTILINE)
    sections: list[tuple[str, str]] = []
    if parts[0].strip():
        sections.append(("Overview", parts[0].strip()))
    for i in range(1, len(parts), 2):
        sections.append((parts[i].strip(), parts[i + 1].strip()))
    return sections


def chunk_markdown(
    title: str, body: str, max_words: int = 300, overlap_words: int = 50
) -> list[Chunk]:
    """One chunk per section; long sections split into overlapping word windows.

    max_words=300 is roughly 400 tokens for English prose.
    """
    chunks: list[Chunk] = []
    for heading, text in _sections(body):
        prefix = f"{title} — {heading}"
        words = text.split()
        if len(words) <= max_words:
            chunks.append(Chunk(heading, f"{prefix}\n{text}"))
            continue
        step = max_words - overlap_words
        for start in range(0, len(words), step):
            window = words[start : start + max_words]
            chunks.append(Chunk(heading, f"{prefix}\n" + " ".join(window)))
            if start + max_words >= len(words):
                break
    return chunks


def ingest_docs(
    session: Session, docs_dir: Path | str, embedder: Embedder | None = None
) -> IngestStats:
    docs_dir = Path(docs_dir)
    stats = IngestStats()
    existing = {d.source_path: d for d in session.scalars(select(KbDocument))}
    seen: set[str] = set()

    for path in sorted(docs_dir.glob("*.md")):
        rel = path.name
        seen.add(rel)
        raw = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(raw.encode()).hexdigest()
        doc = existing.get(rel)
        if doc is not None and doc.content_hash == digest:
            continue

        parsed = parse_doc(raw)
        if doc is None:
            doc = KbDocument(source_path=rel)
            session.add(doc)
        doc.title, doc.category, doc.content_hash = parsed.title, parsed.category, digest
        doc.chunks = [
            KbChunk(chunk_index=i, heading=c.heading, text=c.text)
            for i, c in enumerate(chunk_markdown(parsed.title, parsed.body))
        ]
        stats.documents_written += 1
        stats.chunks_written += len(doc.chunks)
        stats.updated_paths.append(rel)

    for rel, doc in existing.items():
        if rel not in seen:
            session.delete(doc)
            stats.documents_deleted += 1

    session.flush()
    if embedder is not None:
        embed_missing(session, embedder)
    session.commit()
    return stats


def embed_missing(session: Session, embedder: Embedder, batch_size: int = 64) -> int:
    """Embed every chunk that has no vector yet. Returns the number embedded."""
    todo = list(session.scalars(select(KbChunk).where(KbChunk.embedding.is_(None))))
    for start in range(0, len(todo), batch_size):
        batch = todo[start : start + batch_size]
        vectors = embedder.embed_documents([c.text for c in batch])
        for chunk, vec in zip(batch, vectors, strict=True):
            chunk.embedding = to_blob(vec)
    session.commit()
    return len(todo)
