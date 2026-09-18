from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.knowledge_base.embed import HashingEmbedder
from app.knowledge_base.ingest import CATALOG_PREFIX, ingest_catalog, ingest_docs
from app.knowledge_base.models import KbDocument
from app.knowledge_base.search import KnowledgeBase
from app.orders.models import Product
from app.orders.seed import seed_store
from app.seed import seed_all


def _catalog_count(session: Session) -> int:
    q = select(func.count(KbDocument.id)).where(KbDocument.source_path.like(f"{CATALOG_PREFIX}%"))
    return session.scalar(q) or 0


def test_catalog_has_one_doc_per_product(session: Session) -> None:
    seed_store(session)
    stats = ingest_catalog(session)
    assert stats.documents_written == 50
    assert _catalog_count(session) == 50


def test_catalog_chunk_contents(session: Session) -> None:
    seed_store(session)
    ingest_catalog(session)
    doc = session.scalar(
        select(KbDocument).where(KbDocument.source_path == f"{CATALOG_PREFIX}NW-SKU-0001")
    )
    assert doc is not None
    text = doc.chunks[0].text
    assert "NW-SKU-0001" in text and "₹" in text
    assert "stock" not in text.lower()  # live stock comes from the product tool, never the KB


def test_price_change_reingests_only_that_product(session: Session) -> None:
    seed_store(session)
    ingest_catalog(session)
    product = session.scalar(select(Product).where(Product.sku == "NW-SKU-0003"))
    assert product is not None
    product.price = 1234.0
    session.commit()
    stats = ingest_catalog(session)
    assert stats.updated_paths == [f"{CATALOG_PREFIX}NW-SKU-0003"]


def test_doc_ingest_does_not_delete_catalog(session: Session) -> None:
    seed_store(session)
    ingest_catalog(session)
    ingest_docs(session, "data/docs")
    assert _catalog_count(session) == 50


def test_product_name_query_returns_product_first(session: Session) -> None:
    seed_store(session)
    ingest_docs(session, "data/docs", embedder=HashingEmbedder())
    ingest_catalog(session, embedder=HashingEmbedder())
    kb = KnowledgeBase.load(session, HashingEmbedder())
    product = session.scalar(select(Product).where(Product.sku == "NW-SKU-0033"))
    assert product is not None
    hits = kb.search(f"how much is the {product.name}")
    assert hits[0].source_path == f"{CATALOG_PREFIX}NW-SKU-0033"


def test_seed_all_is_idempotent(session: Session) -> None:
    first = seed_all(session, embedder=HashingEmbedder())
    second = seed_all(session, embedder=HashingEmbedder())
    assert first.documents_written > 50
    assert second.documents_written == 0
    assert second.documents_deleted == 0
