from fastapi.testclient import TestClient

from research_agent.main import create_app


def test_liveness_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
