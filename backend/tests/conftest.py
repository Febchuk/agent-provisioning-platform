"""Shared test fixtures: an isolated in-memory SQLite DB per test."""
import pytest
import pytest_asyncio
from sqlmodel import Session, SQLModel, create_engine


def _make_isolated_engine():
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
    return engine


@pytest.fixture()
def session():
    engine = _make_isolated_engine()
    with Session(engine) as s:
        yield s
    SQLModel.metadata.drop_all(engine)


@pytest_asyncio.fixture()
async def app_client(tmp_path, monkeypatch):
    """An httpx AsyncClient against the real FastAPI app (app.main.app), with:
      - an isolated in-memory SQLite DB (swapped into `app.db.engine` so every
        module that does `from app import db; db.engine` picks it up),
      - file uploads/template files redirected under `tmp_path` (so tests
        never touch the real `backend/data/` dir),
      - sandbox backend forced to "local" (LocalSandbox, no Docker/Modal) so
        API-contract tests never require Docker/Modal per specs/07 §1,
      - chat_runtime's in-memory run/sandbox state reset before and after.

    Does NOT touch the LLM — callers that exercise `POST
    /conversations/{id}/messages` must monkeypatch `app.chat_runtime.llm_factory`
    themselves with a FakeLLM-backed factory (scripted per test).
    """
    import httpx

    from app import chat_runtime, db, files as files_module
    from app.main import app

    engine = _make_isolated_engine()
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(files_module, "FILES_DIR", tmp_path / "files")

    chat_runtime.set_sandbox_mode("local")
    await chat_runtime.reset_state_for_tests()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await chat_runtime.reset_state_for_tests()
