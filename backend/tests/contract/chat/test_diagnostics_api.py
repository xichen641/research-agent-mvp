from uuid import uuid4

import httpx
import pytest
from research_agent.chat import routes
from research_agent.chat.routes import get_chat_diagnostics
from research_agent.config import Settings
from research_agent.main import create_app


class EmptyReader:
    async def get_run(self, run_id):
        return None

    async def list_events(self, run_id, *, after_sequence=0):
        return []


@pytest.mark.asyncio
async def test_configuration_api_never_serializes_provider_credentials(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: Settings(
            llm_api_key="private-api-key",
            llm_base_url="https://user:private-password@provider.example/v1",
        ),
    )
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/chat/evaluation/config")
    assert response.status_code == 200
    assert response.json()["provider_host"] == "provider.example"
    assert "private-api-key" not in response.text
    assert "private-password" not in response.text
    assert response.json()["capabilities"]["research_spaces"] is False
    assert response.json()["capabilities"]["cross_session_research_state"] is False


@pytest.mark.asyncio
async def test_diagnostics_for_missing_run_is_not_found() -> None:
    app = create_app()
    app.dependency_overrides[get_chat_diagnostics] = lambda: EmptyReader()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/v1/chat/runs/{uuid4()}/diagnostics")
    assert response.status_code == 404
    assert response.json()["code"] == "CHAT_RUN_NOT_FOUND"
