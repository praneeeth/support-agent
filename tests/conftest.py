from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.db import init_db, make_engine


@pytest.fixture
def session() -> Iterator[Session]:
    engine = make_engine("sqlite://")
    init_db(engine)
    with sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
