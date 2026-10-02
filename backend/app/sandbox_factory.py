"""Sandbox backend selection (specs-v2/specs/09-isolation.md IS-2, D-31;
specs-v2/specs/02-agent-runtime.md SB-6).

This is the ONLY place that reads `SANDBOX_BACKEND` and picks a concrete
`Sandbox` implementation. Callers (app/chat_runtime.py, app/evals.py,
app/main.py's health endpoint) depend only on this module's
`create_sandbox`/`resume_sandbox`/`get_sandbox_backend`, never importing
`LocalSandbox`/`DockerSandbox`/`ModalSandbox` directly for construction.

`SANDBOX_BACKEND` values: "provider" (ModalSandbox) | "docker" (DockerSandbox)
| "local" (LocalSandbox, default). "provider"/"docker" are isolated;
"local" is not (IS-2).
"""
from __future__ import annotations

import os
from typing import Literal

from app.sandbox import DockerSandbox, LocalSandbox, ModalSandbox, Sandbox

SandboxBackend = Literal["provider", "docker", "local"]

_VALID_BACKENDS = ("provider", "docker", "local")


def get_sandbox_backend() -> SandboxBackend:
    """IS-2: reads `SANDBOX_BACKEND` env, default `"local"`. An unrecognized
    value also falls back to `"local"` rather than raising — a misconfigured
    env var should degrade to the safe, always-available offline backend,
    not crash startup.
    """
    value = os.environ.get("SANDBOX_BACKEND", "local").strip().lower()
    if value not in _VALID_BACKENDS:
        return "local"
    return value  # type: ignore[return-value]


def is_isolated(backend: SandboxBackend) -> bool:
    """IS-2: `provider`/`docker` = true, `local` = false."""
    return backend in ("provider", "docker")


async def create_sandbox(workspace_ref: str, *, backend: SandboxBackend | None = None) -> Sandbox:
    """Cold start: dispatches to the selected backend's `create(workspace_ref)`.

    `LocalSandbox.create`/`DockerSandbox.create` are plain (sync)
    classmethods; `ModalSandbox.create` is a coroutine (every real Modal SDK
    call it makes is natively async) — awaited here so callers of THIS
    function always get a uniform `await create_sandbox(...)` regardless of
    backend.
    """
    backend = backend or get_sandbox_backend()
    if backend == "provider":
        return await ModalSandbox.create(workspace_ref)
    if backend == "docker":
        return DockerSandbox.create(workspace_ref)
    return LocalSandbox.create(workspace_ref)


async def resume_sandbox(workspace_ref: str, *, backend: SandboxBackend | None = None) -> Sandbox:
    """Reattach to an existing workspace: dispatches to the selected
    backend's `resume(workspace_ref)`. No file reseeding happens here or in
    any backend's `resume()` — only `create()` seeds from `version.files`.
    """
    backend = backend or get_sandbox_backend()
    if backend == "provider":
        return await ModalSandbox.resume(workspace_ref)
    if backend == "docker":
        return DockerSandbox.resume(workspace_ref)
    return LocalSandbox.resume(workspace_ref)
