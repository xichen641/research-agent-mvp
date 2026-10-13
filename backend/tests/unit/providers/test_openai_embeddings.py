from unittest.mock import AsyncMock
from unittest.mock import Mock
from typing import cast

import httpx
import pytest
from pydantic import SecretStr

from research_agent.config import Settings
from research_agent.providers import openai_embeddings
from research_agent.providers.openai_embeddings import (
    OpenAICompatibleEmbeddingProvider,
    build_openai_embedding_provider,
)


@pytest.mark.asyncio
async def test_adapter_maps_documents_and_query_without_leaking_client_types() -> None:
    client = AsyncMock()
    client.aembed_documents.return_value = [[0.1, 0.2], [0.3, 0.4]]
    client.aembed_query.return_value = [0.5, 0.6]
    provider = OpenAICompatibleEmbeddingProvider(
        client=client,
        model_id="test-model",
        dimension=2,
    )

    assert await provider.embed_documents(["first", "second"]) == [
        [0.1, 0.2],
        [0.3, 0.4],
    ]
    assert await provider.embed_query("question") == [0.5, 0.6]
    assert provider.model_id == "test-model"
    assert provider.dimension == 2
    client.aembed_documents.assert_awaited_once_with(["first", "second"])
    client.aembed_query.assert_awaited_once_with("question")


@pytest.mark.parametrize(
    "vectors",
    [
        pytest.param([[0.1, 0.2]], id="wrong-count"),
        pytest.param([[0.1, 0.2], [0.3]], id="wrong-dimension"),
    ],
)
@pytest.mark.asyncio
async def test_adapter_rejects_invalid_document_vectors(
    vectors: list[list[float]],
) -> None:
    client = AsyncMock()
    client.aembed_documents.return_value = vectors
    provider = OpenAICompatibleEmbeddingProvider(
        client=client,
        model_id="test-model",
        dimension=2,
    )

    with pytest.raises(ValueError, match="embedding"):
        await provider.embed_documents(["first", "second"])


@pytest.mark.asyncio
async def test_adapter_rejects_wrong_query_dimension() -> None:
    client = AsyncMock()
    client.aembed_query.return_value = [0.1]
    provider = OpenAICompatibleEmbeddingProvider(
        client=client,
        model_id="test-model",
        dimension=2,
    )

    with pytest.raises(ValueError, match="dimension"):
        await provider.embed_query("question")


def test_builder_keeps_openai_compatible_embedding_input_as_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Mock()
    constructor = Mock(return_value=client)
    monkeypatch.setattr(openai_embeddings, "OpenAIEmbeddings", constructor)
    settings = Settings(
        embedding_api_key="secret-key",
        embedding_base_url="https://dashscope.example/compatible-mode/v1",
        embedding_model="text-embedding-v4",
    )

    provider = build_openai_embedding_provider(settings)

    assert provider.model_id == "text-embedding-v4"
    constructor.assert_called_once_with(
        api_key=SecretStr("secret-key"),
        base_url="https://dashscope.example/compatible-mode/v1",
        model="text-embedding-v4",
        dimensions=1536,
        check_embedding_ctx_length=False,
    )


def test_builder_uses_caller_owned_async_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding_client = cast(httpx.AsyncClient, Mock())
    constructor = Mock(return_value=Mock())
    monkeypatch.setattr(openai_embeddings, "OpenAIEmbeddings", constructor)
    settings = Settings(
        embedding_api_key="secret-key",
        embedding_base_url="https://dashscope.example/compatible-mode/v1",
        embedding_model="text-embedding-v4",
    )

    build_openai_embedding_provider(
        settings,
        http_async_client=embedding_client,
    )

    constructor.assert_called_once_with(
        api_key=SecretStr("secret-key"),
        base_url="https://dashscope.example/compatible-mode/v1",
        model="text-embedding-v4",
        dimensions=1536,
        check_embedding_ctx_length=False,
        http_async_client=embedding_client,
    )
