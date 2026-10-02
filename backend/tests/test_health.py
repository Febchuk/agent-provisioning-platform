"""T0.1 — /health endpoint shape per specs/03-chat-and-deploy.md and SB-5."""
from fastapi.testclient import TestClient

from app.main import app


def test_health_ok():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "ok" in body
    assert body["ok"] is True
    assert "sandbox_mode" in body


def test_fallback_mode_reported():
    """SB-5: sandbox_mode is one of the two real modes, set by real Docker
    detection at startup (never the Phase 1 "not-configured" placeholder).
    """
    client = TestClient(app)
    with client:  # triggers the lifespan context (startup) so detection runs
        resp = client.get("/health")
    assert resp.json()["sandbox_mode"] in ("docker", "local-unsafe")
