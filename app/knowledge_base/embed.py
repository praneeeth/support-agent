"""Text embedders. All return L2-normalised float32 vectors."""

import hashlib
from typing import Protocol, cast

import numpy as np
import numpy.typing as npt

Vectors = npt.NDArray[np.float32]


class Embedder(Protocol):
    name: str

    def embed_documents(self, texts: list[str]) -> Vectors: ...
    def embed_query(self, text: str) -> Vectors: ...


def _normalise(m: Vectors) -> Vectors:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return cast(Vectors, (m / norms).astype(np.float32))


class FastEmbedder:
    """Local ONNX model via fastembed (default BAAI/bge-small-en-v1.5, 384 dims)."""

    def __init__(self, model_name: str) -> None:
        from fastembed import TextEmbedding  # imported lazily: model download on first use

        self.name = model_name
        self._model = TextEmbedding(model_name)

    def embed_documents(self, texts: list[str]) -> Vectors:
        return _normalise(np.array(list(self._model.passage_embed(texts)), dtype=np.float32))

    def embed_query(self, text: str) -> Vectors:
        vectors = _normalise(np.array(list(self._model.query_embed([text])), dtype=np.float32))
        return cast(Vectors, vectors[0])


class HashingEmbedder:
    """Deterministic bag-of-words hashing embedder for tests. No model, no network."""

    name = "hashing-256"

    def __init__(self, dims: int = 256) -> None:
        self.dims = dims

    def _vec(self, text: str) -> Vectors:
        from app.knowledge_base.search import tokenize

        v = np.zeros(self.dims, dtype=np.float32)
        for tok in tokenize(text):
            h = int(hashlib.md5(tok.encode(), usedforsecurity=False).hexdigest(), 16)
            v[h % self.dims] += 1.0
        return v

    def embed_documents(self, texts: list[str]) -> Vectors:
        return _normalise(np.stack([self._vec(t) for t in texts]))

    def embed_query(self, text: str) -> Vectors:
        vectors = _normalise(self._vec(text)[None, :])
        return cast(Vectors, vectors[0])


def to_blob(v: Vectors) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def from_blob(b: bytes) -> Vectors:
    return np.frombuffer(b, dtype=np.float32)
