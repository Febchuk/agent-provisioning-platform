"""Shared test fixtures: an isolated in-memory SQLite DB per test."""
import pytest
from sqlmodel import Session, SQLModel, create_engine


@pytest.fixture()
def session():
    # A file-backed in-memory DB (not ":memory:") so the same connection pool
    # serves all sessions created against this engine within the test, while
    # staying fully isolated between tests and requiring no disk cleanup.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=__import__("sqlalchemy.pool", fromlist=["StaticPool"]).StaticPool,
    )
    from app import models  # noqa: F401  (register table metadata)

    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    SQLModel.metadata.drop_all(engine)
