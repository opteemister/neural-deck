# spec: project-initialization
"""Health endpoint tests."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_live_returns_ok() -> None:
    """The liveness endpoint returns a stable process status."""

    response = TestClient(app).get("/api/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
