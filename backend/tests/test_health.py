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
