"""SQLite engine/session setup (specs/01-data-model.md: "SQLite via SQLModel").

Default DB file lives at `backend/data/app.db` (gitignored). Override with
the `DATABASE_URL` env var (tests use `sqlite://` in-memory).
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _default_sqlite_url() -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DATA_DIR / 'app.db'}"


DATABASE_URL = os.environ.get("DATABASE_URL", _default_sqlite_url())

connect_args = {"check_same_thread": False}
engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)


def init_db() -> None:
    """Create all tables. Safe to call multiple times (no-op if they exist)."""
    # Import models so their table metadata is registered on SQLModel.metadata
    # before create_all is called.
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
