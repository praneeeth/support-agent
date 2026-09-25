"""One-command setup: create tables, seed the demo store, ingest docs and catalog.

Usage: uv run python -m app.seed [--vertical northwind]
"""

import argparse
import logging
import os

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_engine, get_session, init_db
from app.knowledge_base.embed import Embedder, FastEmbedder
from app.knowledge_base.ingest import IngestStats, clear_catalog, ingest_catalog, ingest_docs
from app.orders.seed import seed_store
from app.verticals.config import VerticalConfig, get_vertical

# Tools that read the demo store. A vertical with none of them gets no store and no catalog.
STORE_TOOLS = frozenset({"get_order_status", "get_product"})

log = logging.getLogger(__name__)


def seed_all(
    session: Session, embedder: Embedder | None = None, vertical: VerticalConfig | None = None
) -> IngestStats:
    settings = get_settings()
    vertical = vertical or get_vertical()
    if STORE_TOOLS & set(vertical.tools):
        seed_store(session, seed=settings.seed)
    docs = ingest_docs(session, settings.docs_dir or vertical.docs_dir, embedder=embedder)
    if "get_product" in vertical.tools:
        catalog = ingest_catalog(session, embedder=embedder)
    else:
        catalog = clear_catalog(session)
    return IngestStats(
        documents_written=docs.documents_written + catalog.documents_written,
        documents_deleted=docs.documents_deleted + catalog.documents_deleted,
        chunks_written=docs.chunks_written + catalog.chunks_written,
        updated_paths=docs.updated_paths + catalog.updated_paths,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed the database for one vertical.")
    parser.add_argument(
        "--vertical",
        default="",
        help="Which business to seed (default: the VERTICAL setting, else northwind).",
    )
    args = parser.parse_args(argv)
    if args.vertical:
        # Chosen here rather than read from the environment, so one command can seed any vertical.
        os.environ["VERTICAL"] = args.vertical
        get_settings.cache_clear()
        get_vertical.cache_clear()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = get_vertical()
    log.info("Seeding %s (%s)", config.business.name, config.id)
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
