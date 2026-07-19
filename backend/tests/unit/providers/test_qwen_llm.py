from unittest.mock import AsyncMock

import httpx
from pydantic import BaseModel
import pytest

from research_agent.config import Settings
from research_agent.providers.protocols import ChatTurn
from research_agent.providers.qwen_llm import QwenLLMProvider, build_qwen_llm
from research_agent.shared.errors import AppError


class StructuredResult(BaseModel):
    label: str
    count: int


def response(status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("POST", "https://dashscope.example/chat/completions"),
    )


@pytest.mark.asyncio
async def test_qwen_adapter_requests_json_and_validates_typed_output() -> None:
    client = AsyncMock()
    client.post.return_value = response(
        200,
        {
            "choices": [
                {"message": {"role": "assistant", "content": '{"label":"ok","count":2}'}}
            ]
        },
    )
    provider = QwenLLMProvider(
        client=client,
        base_url="https://dashscope.example/compatible-mode/v1",
        api_key="secret-key",
        model_id="qwen-plus",
    )

    result = await provider.generate_structured(
        operation="example",
        messages=[ChatTurn(role="user", content="Return a result")],
        output_type=StructuredResult,
    )

    assert result == StructuredResult(label="ok", count=2)
    call = client.post.await_args
    assert call.args[0].endswith("/chat/completions")
    assert call.kwargs["headers"] == {"Authorization": "Bearer secret-key"}
    payload = call.kwargs["json"]
    assert payload["model"] == "qwen-plus"
    assert payload["response_format"] == {"type": "json_object"}
    assert "JSON" in payload["messages"][0]["content"]
    assert "StructuredResult" in payload["messages"][0]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [408, 429, 500, 503])
async def test_qwen_adapter_sanitizes_retryable_failures(status_code: int) -> None:
    client = AsyncMock()
    client.post.return_value = response(status_code, {"message": "private provider detail"})
    provider = QwenLLMProvider(
        client=client,
        base_url="https://dashscope.example/compatible-mode/v1",
        api_key="secret-key",
        model_id="qwen-plus",
    )

    with pytest.raises(AppError) as captured:
        await provider.generate_structured(
            operation="example",
            messages=[ChatTurn(role="user", content="secret paper text")],
            output_type=StructuredResult,
        )

    assert captured.value.code == "LLM_UNAVAILABLE"
    assert captured.value.retryable is True
    assert "private" not in str(captured.value)
    assert "secret paper" not in str(captured.value)


@pytest.mark.asyncio
async def test_qwen_adapter_rejects_malformed_structured_response() -> None:
    client = AsyncMock()
    client.post.return_value = response(
        200,
        {"choices": [{"message": {"content": '{"label":"missing count"}'}}]},
    )
    provider = QwenLLMProvider(
        client=client,
        base_url="https://dashscope.example/compatible-mode/v1",
        api_key="secret-key",
        model_id="qwen-plus",
    )

    with pytest.raises(AppError) as captured:
        await provider.generate_structured(
            operation="example",
            messages=[ChatTurn(role="user", content="Return a result")],
            output_type=StructuredResult,
        )

    assert captured.value.code == "LLM_INVALID_RESPONSE"
    assert captured.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize("first_status", [429, 503])
async def test_qwen_adapter_retries_one_transient_failure(first_status: int) -> None:
    client = AsyncMock()
    client.post.side_effect = [
        response(first_status, {"message": "temporary private detail"}),
        response(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"label":"recovered","count":1}'
                        }
                    }
                ]
            },
        ),
    ]
    provider = QwenLLMProvider(
        client=client,
        base_url="https://dashscope.example/compatible-mode/v1",
        api_key="secret-key",
        model_id="qwen3.7-plus",
    )

    result = await provider.generate_structured(
        operation="example",
        messages=[ChatTurn(role="user", content="Return a result")],
        output_type=StructuredResult,
    )

    assert result.label == "recovered"
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_qwen_adapter_retries_one_invalid_structured_response() -> None:
    client = AsyncMock()
    client.post.side_effect = [
        response(
            200,
            {"choices": [{"message": {"content": '{"label":"missing count"}'}}]},
        ),
        response(
            200,
            {
                "choices": [
                    {"message": {"content": '{"label":"repaired","count":2}'}}
                ]
            },
        ),
    ]
    provider = QwenLLMProvider(
        client=client,
        base_url="https://dashscope.example/compatible-mode/v1",
        api_key="secret-key",
        model_id="qwen3.7-plus",
    )

    result = await provider.generate_structured(
        operation="example",
        messages=[ChatTurn(role="user", content="Return a result")],
        output_type=StructuredResult,
    )

    assert result.label == "repaired"
    assert client.post.await_count == 2


def test_builder_reuses_dashscope_key_when_dedicated_llm_key_is_blank() -> None:
    settings = Settings(
        llm_api_key="",
        embedding_api_key="shared-dashscope-key",
        llm_base_url="https://dashscope.example/compatible-mode/v1",
        llm_model="qwen-plus",
    )

    provider = build_qwen_llm(settings, client=AsyncMock())

    assert provider.model_id == "qwen-plus"
