"""Load markdown docs into kb_documents / kb_chunks. Idempotent per document hash."""

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.knowledge_base.embed import Embedder, to_blob
from app.knowledge_base.models import KbChunk, KbDocument
from app.orders.models import Product

CATALOG_PREFIX = "catalog/"
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
    sources: list[tuple[str, str, str, str, list[Chunk]]] = []
    for path in sorted(docs_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        parsed = parse_doc(raw)
        sources.append(
            (path.name, _sha(raw), parsed.title, parsed.category,
             chunk_markdown(parsed.title, parsed.body))
        )  # fmt: skip
    return _sync(session, sources, owns=lambda p: not p.startswith(CATALOG_PREFIX),
                 embedder=embedder)  # fmt: skip


def ingest_catalog(session: Session, embedder: Embedder | None = None) -> IngestStats:
    """One document/chunk per product. Stock is deliberately excluded (it changes constantly;
    the agent gets live stock from the product tool)."""
    sources: list[tuple[str, str, str, str, list[Chunk]]] = []
    for p in session.scalars(select(Product).order_by(Product.sku)):
        text = (
            f"{p.name} — Product\n"
            f"{p.name} (SKU {p.sku}) is in our {p.category} collection and costs "
            f"₹{float(p.price):,.0f}. {p.description}"
        )
        sources.append(
            (f"{CATALOG_PREFIX}{p.sku}", _sha(text), p.name, "product", [Chunk("Product", text)])
        )
    return _sync(session, sources, owns=lambda p: p.startswith(CATALOG_PREFIX), embedder=embedder)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _sync(
    session: Session,
    sources: list[tuple[str, str, str, str, list[Chunk]]],
    owns: Callable[[str], bool],
    embedder: Embedder | None,
) -> IngestStats:
    """Make the stored documents under `owns` match `sources` exactly."""
    stats = IngestStats()
    existing = {
        d.source_path: d for d in session.scalars(select(KbDocument)) if owns(d.source_path)
    }
    seen: set[str] = set()
    for path, digest, title, category, chunks in sources:
        seen.add(path)
        doc = existing.get(path)
        if doc is not None and doc.content_hash == digest:
            continue
        if doc is None:
            doc = KbDocument(source_path=path)
            session.add(doc)
        doc.title, doc.category, doc.content_hash = title, category, digest
        doc.chunks = [
            KbChunk(chunk_index=i, heading=c.heading, text=c.text) for i, c in enumerate(chunks)
        ]
        stats.documents_written += 1
        stats.chunks_written += len(chunks)
        stats.updated_paths.append(path)

    for path, doc in existing.items():
        if path not in seen:
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
