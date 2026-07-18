from unittest.mock import AsyncMock

import pytest

from research_agent.providers.openai_embeddings import (
    OpenAICompatibleEmbeddingProvider,
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
