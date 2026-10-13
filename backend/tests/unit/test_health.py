from fastapi.testclient import TestClient

from research_agent.health import ReadinessStates
from research_agent.main import create_app


def test_liveness_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_dependency_states_without_details() -> None:
    async def healthy() -> ReadinessStates:
        return {
            "postgres": "ok",
            "redis": "ok",
            "object_storage": "ok",
        }

    client = TestClient(create_app(readiness_probe=healthy))

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {
            "postgres": "ok",
            "redis": "ok",
            "object_storage": "ok",
        },
    }


def test_readiness_returns_503_when_a_dependency_is_unavailable() -> None:
    async def degraded() -> ReadinessStates:
        return {
            "postgres": "ok",
            "redis": "unavailable",
            "object_storage": "ok",
        }

    client = TestClient(create_app(readiness_probe=degraded))

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["dependencies"] == {
        "postgres": "ok",
        "redis": "unavailable",
        "object_storage": "ok",
    }
    assert "redis_url" not in response.text
    assert "error" not in response.text


def test_metrics_exposes_only_bounded_operational_labels() -> None:
    async def metrics() -> str:
        return (
            "# TYPE research_agent_outbox_pending gauge\n"
            "research_agent_outbox_pending 2\n"
        )

    client = TestClient(create_app(metrics_probe=metrics))

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "research_agent_outbox_pending 2" in response.text
    assert "DATABASE_URL" not in response.text
