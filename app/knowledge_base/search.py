"""Search over ingested chunks."""

import re
from dataclasses import dataclass
from typing import Protocol

from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.knowledge_base.models import KbChunk

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "my",
        "of",
        "on",
        "or",
        "our",
        "so",
        "that",
        "the",
        "their",
        "them",
        "there",
        "this",
        "to",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "you",
        "your",
        "me",
        "am",
        "have",
        "has",
        "had",
        "any",
    ]
)


def tokenize(text: str) -> list[str]:
    tokens = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in _STOPWORDS:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]  # crude plural folding: returns -> return
        tokens.append(w)
    return tokens


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    doc_title: str
    heading: str
    text: str
    score: float  # relevance in [0, 1]
    source_path: str


class Searcher(Protocol):
    def search(self, query: str, k: int = 5) -> list[Hit]: ...


class KnowledgeBase:
    """In-memory index built from the database. Rebuild after ingest."""

    def __init__(self, chunks: list[KbChunk]) -> None:
        self._chunks = chunks
        corpus = [_index_tokens(c) for c in chunks]
        self._bm25 = BM25Okapi(corpus) if corpus else None

    @classmethod
    def load(cls, session: Session) -> "KnowledgeBase":
        chunks = list(
            session.scalars(
                select(KbChunk).options(joinedload(KbChunk.document)).order_by(KbChunk.id)
            )
        )
        return cls(chunks)

    def search(self, query: str, k: int = 5) -> list[Hit]:
        tokens = tokenize(query)
        if not tokens or self._bm25 is None:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [self._hit(i, _saturate(float(scores[i]))) for i in ranked if scores[i] > 0]

    def _hit(self, i: int, score: float) -> Hit:
        c = self._chunks[i]
        return Hit(
            chunk_id=c.id,
            doc_title=c.document.title,
            heading=c.heading,
            text=c.text,
            score=round(score, 4),
            source_path=c.document.source_path,
        )


def _index_tokens(chunk: KbChunk) -> list[str]:
    """Body tokens plus the title/heading line counted twice (simple field boost)."""
    header, _, body = chunk.text.partition("\n")
    return tokenize(header) * 2 + tokenize(body)


def _saturate(bm25: float, k: float = 8.0) -> float:
    """Map an unbounded BM25 score into [0, 1)."""
    return bm25 / (bm25 + k) if bm25 > 0 else 0.0
