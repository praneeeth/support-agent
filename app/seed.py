"""One-command setup: create tables, seed the demo store, ingest docs and catalog.

Usage: uv run python -m app.seed
"""

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_engine, get_session, init_db
from app.knowledge_base.embed import Embedder, FastEmbedder
from app.knowledge_base.ingest import IngestStats, ingest_catalog, ingest_docs
from app.orders.seed import seed_store
from app.verticals.config import docs_dir

log = logging.getLogger(__name__)


def seed_all(session: Session, embedder: Embedder | None = None) -> IngestStats:
    settings = get_settings()
    seed_store(session, seed=settings.seed)
    docs = ingest_docs(session, docs_dir(), embedder=embedder)
    catalog = ingest_catalog(session, embedder=embedder)
    return IngestStats(
        documents_written=docs.documents_written + catalog.documents_written,
        documents_deleted=docs.documents_deleted + catalog.documents_deleted,
        chunks_written=docs.chunks_written + catalog.chunks_written,
        updated_paths=docs.updated_paths + catalog.updated_paths,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    init_db(get_engine())
    try:
        embedder: Embedder | None = FastEmbedder(get_settings().embedding_model)
    except Exception as exc:  # noqa: BLE001 - search still works (keyword-only) without it
        log.warning("Embedding model unavailable (%s); ingesting without vectors.", exc)
        embedder = None
    session = next(get_session())
    stats = seed_all(session, embedder)
    log.info(
        "Seed complete: %d documents written, %d deleted, %d chunks.",
        stats.documents_written,
        stats.documents_deleted,
        stats.chunks_written,
    )


if __name__ == "__main__":
    main()
