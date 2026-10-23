import json
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import BaseModel
from research_agent.providers.protocols import ChatTurn
from research_agent.providers.qwen_llm import QwenLLMProvider
from research_agent.providers.telemetry import ModelCallObservation, response_metadata


class Result(BaseModel):
    value: str


def reply(content: str, *, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "model": "actual-model-alias",
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            "choices": [{"message": {"content": content}}],
            "private_detail": "provider-secret",
        },
    )


@pytest.mark.asyncio
async def test_observations_cover_retries_without_retaining_content() -> None:
    client = AsyncMock()
    client.post.side_effect = [
        reply('{"wrong":"private model answer"}'),
        reply('{"value":"ok"}'),
    ]
    records: list[ModelCallObservation] = []

    async def observe(item: ModelCallObservation) -> None:
        records.append(item)

    provider = QwenLLMProvider(
        client=client,
        base_url="https://provider.example/v1",
        api_key="secret-key",
        model_id="configured-model",
        observer=observe,
    )
    result = await provider.generate_structured(
        operation="test",
        messages=[ChatTurn(role="user", content="private paper text")],
        output_type=Result,
    )
    assert result.value == "ok"
    assert [item.outcome for item in records] == ["invalid_response", "completed"]
    assert [item.attempt for item in records] == [1, 2]
    assert sum(item.total_tokens or 0 for item in records) == 30
    assert records[0].request_sha256 != records[1].request_sha256
    assert all(
        item.requested_model == "configured-model"
        and item.response_model == "actual-model-alias"
        for item in records
    )
    encoded = json.dumps([item.model_dump(mode="json") for item in records])
    assert not any(
        secret in encoded
        for secret in (
            "secret-key",
            "private paper text",
            "private model answer",
            "provider-secret",
        )
    )


@pytest.mark.asyncio
async def test_observer_failure_does_not_repeat_a_successful_model_request() -> None:
    client = AsyncMock()
    client.post.return_value = reply('{"value":"ok"}')
    observer = AsyncMock(side_effect=RuntimeError("private measurement failure"))
    provider = QwenLLMProvider(
        client=client,
        base_url="https://provider.example/v1",
        api_key="secret-key",
        model_id="configured-model",
        observer=observer,
    )
    result = await provider.generate_structured(
        operation="test", messages=[], output_type=Result
    )
    assert result.value == "ok"
    assert client.post.await_count == 1


@pytest.mark.parametrize("value", [True, -1, 2.5, "10", None])
def test_missing_or_invalid_usage_is_not_zero_or_an_estimate(value: object) -> None:
    model, usage = response_metadata(
        {"model": "alias", "usage": {"prompt_tokens": value}}
    )
    assert model == "alias"
    assert usage == {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }
