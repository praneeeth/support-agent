"""Hybrid search (BM25 + vectors, reciprocal-rank fusion) over ingested chunks."""

import re
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.knowledge_base.embed import Embedder, from_blob
from app.knowledge_base.models import KbChunk

_STOPWORDS = frozenset(
    (  # noqa: SIM905 - a word string is easier to maintain than a 50-item list
        "a an and are as at be by can do does for from how i if in is it its my of on or our so "
        "that the their them there this to was we what when where which who will with you your "
        "me am have has had any"
    ).split()
)

RRF_K = 60


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
    score: float  # relevance in [0, 1]; semantic similarity when vectors are available
    source_path: str


class Searcher(Protocol):
    def search(self, query: str, k: int = 5) -> list[Hit]: ...


class KnowledgeBase:
    """In-memory index built from the database. Rebuild after ingest.

    Uses hybrid ranking when an embedder is given and every chunk has a stored vector
    from that embedder; otherwise falls back to BM25 only.
    """

    def __init__(self, chunks: list[KbChunk], embedder: Embedder | None = None) -> None:
        self._chunks = chunks
        corpus = [_index_tokens(c) for c in chunks]
        self._bm25 = BM25Okapi(corpus) if corpus else None
        self._embedder: Embedder | None = None
        self._matrix: np.ndarray | None = None
        if embedder is not None and chunks and all(c.embedding for c in chunks):
            self._embedder = embedder
            self._matrix = np.stack([from_blob(c.embedding or b"") for c in chunks])

    @classmethod
    def load(cls, session: Session, embedder: Embedder | None = None) -> "KnowledgeBase":
        chunks = list(
            session.scalars(
                select(KbChunk).options(joinedload(KbChunk.document)).order_by(KbChunk.id)
            )
        )
        return cls(chunks, embedder)

    @property
    def hybrid(self) -> bool:
        return self._matrix is not None

    def search(self, query: str, k: int = 5) -> list[Hit]:
        tokens = tokenize(query)
        if self._bm25 is None or (not tokens and not query.strip()):
            return []
        bm25 = self._bm25.get_scores(tokens) if tokens else np.zeros(len(self._chunks))

        if self._matrix is None or self._embedder is None:
            ranked = [i for i in np.argsort(-bm25)[:k] if bm25[i] > 0]
            return [self._hit(int(i), _saturate(float(bm25[i]))) for i in ranked]

        sims = self._matrix @ self._embedder.embed_query(query)
        fused = _rrf([np.argsort(-bm25), np.argsort(-sims)], only_positive=[bm25, None])
        top = sorted(fused, key=lambda i: fused[i], reverse=True)[:k]
        return [self._hit(i, max(0.0, min(1.0, float(sims[i])))) for i in top]

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


def _rrf(rankings: list[np.ndarray], only_positive: list[np.ndarray | None]) -> dict[int, float]:
    """Reciprocal-rank fusion. A ranking with a score array only contributes positive scores."""
    fused: dict[int, float] = {}
    for order, scores in zip(rankings, only_positive, strict=True):
        for rank, idx in enumerate(order):
            i = int(idx)
            if scores is not None and scores[i] <= 0:
                continue
            fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
    return fused


def _index_tokens(chunk: KbChunk) -> list[str]:
    """Body tokens plus the title/heading line counted twice (simple field boost)."""
    header, _, body = chunk.text.partition("\n")
    return tokenize(header) * 2 + tokenize(body)


def _saturate(bm25: float, k: float = 8.0) -> float:
    """Map an unbounded BM25 score into [0, 1)."""
    return bm25 / (bm25 + k) if bm25 > 0 else 0.0
