from unittest.mock import AsyncMock

import httpx
import pytest

from research_agent.providers.protocols import RerankItem
from research_agent.providers.qwen_reranker import QwenRerankerProvider
from research_agent.shared.errors import AppError


def items() -> list[RerankItem]:
    return [
        RerankItem(id="a", text="first"),
        RerankItem(id="b", text="second"),
    ]


def response(status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("POST", "https://workspace.example/reranks"),
    )


@pytest.mark.asyncio
async def test_adapter_uses_qwen_compatible_api_and_preserves_ids() -> None:
    client = AsyncMock()
    client.post.return_value = response(
        200,
        {
            "results": [
                {"index": 1, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.4},
            ]
        },
    )
    provider = QwenRerankerProvider(
        client=client,
        base_url="https://workspace.example",
        api_key="secret-key",
        model_id="qwen3-rerank",
    )

    result = await provider.rerank(query="question", items=items(), top_n=2)

    assert result == [
        RerankItem(id="b", text="second", score=0.9),
        RerankItem(id="a", text="first", score=0.4),
    ]
    client.post.assert_awaited_once_with(
        "https://workspace.example/compatible-api/v1/reranks",
        headers={"Authorization": "Bearer secret-key"},
        json={
            "model": "qwen3-rerank",
            "query": "question",
            "documents": ["first", "second"],
            "top_n": 2,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [-1, 2])
async def test_adapter_rejects_out_of_range_provider_indices(index: int) -> None:
    client = AsyncMock()
    client.post.return_value = response(
        200,
        {"results": [{"index": index, "relevance_score": 0.9}]},
    )
    provider = QwenRerankerProvider(
        client=client,
        base_url="https://workspace.example",
        api_key="secret-key",
        model_id="qwen3-rerank",
    )

    with pytest.raises(AppError) as captured:
        await provider.rerank(query="question", items=items(), top_n=1)

    assert captured.value.code == "RERANKER_INVALID_RESPONSE"
    assert captured.value.retryable is False


@pytest.mark.asyncio
async def test_adapter_rejects_missing_or_duplicate_provider_results() -> None:
    client = AsyncMock()
    provider = QwenRerankerProvider(
        client=client,
        base_url="https://workspace.example",
        api_key="secret-key",
        model_id="qwen3-rerank",
    )
    client.post.return_value = response(200, {"results": []})

    with pytest.raises(AppError) as missing:
        await provider.rerank(query="question", items=items(), top_n=1)
    assert missing.value.code == "RERANKER_INVALID_RESPONSE"

    client.post.return_value = response(
        200,
        {
            "results": [
                {"index": 0, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.8},
            ]
        },
    )
    with pytest.raises(AppError) as duplicate:
        await provider.rerank(query="question", items=items(), top_n=2)
    assert duplicate.value.code == "RERANKER_INVALID_RESPONSE"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [408, 429, 500, 503])
async def test_adapter_sanitizes_retryable_provider_failures(
    status_code: int,
) -> None:
    client = AsyncMock()
    client.post.return_value = response(
        status_code,
        {"message": "secret rate detail"},
    )
    provider = QwenRerankerProvider(
        client=client,
        base_url="https://workspace.example",
        api_key="secret-key",
        model_id="qwen3-rerank",
    )

    with pytest.raises(AppError) as captured:
        await provider.rerank(query="question", items=items(), top_n=1)

    assert captured.value.code == "RERANKER_UNAVAILABLE"
    assert captured.value.message == "Reranker is temporarily unavailable"
    assert captured.value.retryable is True
    assert "secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_adapter_sanitizes_non_retryable_provider_failure() -> None:
    client = AsyncMock()
    client.post.return_value = response(
        400,
        {"message": "secret request content"},
    )
    provider = QwenRerankerProvider(
        client=client,
        base_url="https://workspace.example",
        api_key="secret-key",
        model_id="qwen3-rerank",
    )

    with pytest.raises(AppError) as captured:
        await provider.rerank(query="question", items=items(), top_n=1)

    assert captured.value.code == "RERANKER_REQUEST_FAILED"
    assert captured.value.message == "Reranker request could not be completed"
    assert captured.value.retryable is False
    assert "secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_adapter_sanitizes_transport_failure() -> None:
    client = AsyncMock()
    client.post.side_effect = httpx.ConnectError("secret host detail")
    provider = QwenRerankerProvider(
        client=client,
        base_url="https://workspace.example",
        api_key="secret-key",
        model_id="qwen3-rerank",
    )

    with pytest.raises(AppError) as captured:
        await provider.rerank(query="question", items=items(), top_n=1)

    assert captured.value.code == "RERANKER_UNAVAILABLE"
    assert captured.value.retryable is True
    assert "secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_adapter_rejects_malformed_success_response() -> None:
    client = AsyncMock()
    client.post.return_value = response(200, {"results": "not-a-list"})
    provider = QwenRerankerProvider(
        client=client,
        base_url="https://workspace.example",
        api_key="secret-key",
        model_id="qwen3-rerank",
    )

    with pytest.raises(AppError) as captured:
        await provider.rerank(query="question", items=items(), top_n=1)

    assert captured.value.code == "RERANKER_INVALID_RESPONSE"


def test_adapter_rejects_insecure_or_blank_configuration() -> None:
    client = AsyncMock()

    with pytest.raises(ValueError, match="https"):
        QwenRerankerProvider(
            client=client,
            base_url="workspace.example",
            api_key="secret-key",
            model_id="qwen3-rerank",
        )
    with pytest.raises(ValueError, match="api_key"):
        QwenRerankerProvider(
            client=client,
            base_url="https://workspace.example",
            api_key="",
            model_id="qwen3-rerank",
        )
