"""FastAPI entrypoint (specs/03-chat-and-deploy.md endpoint table).

Only `GET /health` exists in this phase (T0.1). Everything else — agents,
versions, chat, evals, improver endpoints — is built in later phases.

The SQLite/SQLModel engine (specs/01-data-model.md) is wired up at startup so
`/health` and future phases have a working DB connection, per T0.1's data
model requirement.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Agent Provisioning Platform", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    """GET /health — {ok, sandbox_mode} (specs/03 endpoint table; SB-5).

    sandbox_mode is a placeholder for now: real Docker-availability detection
    (SB-5: "local-unsafe" when Docker is unavailable) lands with the sandbox
    in Phase 2 (M1). See DECISIONS.md.
    """
    return {"ok": True, "sandbox_mode": "not-configured"}
