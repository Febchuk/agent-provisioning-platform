"""T0.1 — /health endpoint shape per specs/03-chat-and-deploy.md and SB-5.

v2 (IS-2): {ok, sandbox_backend, isolated} replaces v1's {ok, sandbox_mode}.
"""
from fastapi.testclient import TestClient

from app.main import app


def test_health_ok():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "ok" in body
    assert body["ok"] is True
    assert "sandbox_backend" in body
    assert "isolated" in body


def test_fallback_mode_reported():
    """IS-2: sandbox_backend is one of the three real backends, set from
    SANDBOX_BACKEND at startup (default "local" when unset, never the v1
    Phase 1 "not-configured" placeholder); isolated matches it.
    """
    client = TestClient(app)
    with client:  # triggers the lifespan context (startup) so detection runs
        resp = client.get("/health")
    body = resp.json()
    assert body["sandbox_backend"] in ("provider", "docker", "local")
    assert body["isolated"] == (body["sandbox_backend"] in ("provider", "docker"))
