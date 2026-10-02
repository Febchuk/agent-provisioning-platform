"""FastAPI entrypoint (specs/03-chat-and-deploy.md endpoint table).

Only `GET /health` exists in this phase. Everything else — agents, versions,
chat, evals, improver endpoints — is built in later phases.

The SQLite/SQLModel engine (specs/01-data-model.md) is wired up at startup so
`/health` and future phases have a working DB connection, per T0.1's data
model requirement.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db
from app.sandbox import docker_available

# SB-5: detected once at startup (a Docker daemon ping), not re-checked per
# request. app/sandbox.py's docker_available() is the single source of truth
# for this check; main.py only reads the result.
_sandbox_mode = {"value": "local-unsafe"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _sandbox_mode["value"] = "docker" if docker_available() else "local-unsafe"
    yield


app = FastAPI(title="Agent Provisioning Platform", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    """GET /health — {ok, sandbox_mode} (specs/03 endpoint table; SB-5).

    sandbox_mode is "docker" when the Docker daemon was reachable at startup,
    else "local-unsafe" (LocalSandbox fallback).
    """
    return {"ok": True, "sandbox_mode": _sandbox_mode["value"]}
