import pytest
from pydantic import BaseModel

from research_agent.providers.fake import (
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakeRerankerProvider,
)
from research_agent.providers.protocols import ChatTurn, RerankItem


class ExampleOutput(BaseModel):
    label: str


@pytest.mark.asyncio
async def test_fake_llm_returns_registered_structured_output() -> None:
    provider = FakeLLMProvider({"example": {"label": "ok"}})

    result = await provider.generate_structured(
        operation="example",
        messages=[ChatTurn(role="user", content="question")],
        output_type=ExampleOutput,
    )

    assert result == ExampleOutput(label="ok")


@pytest.mark.asyncio
async def test_fake_embedding_is_deterministic() -> None:
    provider = FakeEmbeddingProvider(dimension=4)

    first = await provider.embed_query("same")
    second = await provider.embed_query("same")

    assert first == second
    assert len(first) == 4


@pytest.mark.asyncio
async def test_fake_embedding_supports_dimensions_larger_than_one_digest() -> None:
    provider = FakeEmbeddingProvider(dimension=33)

    result = await provider.embed_query("same")

    assert len(result) == 33


@pytest.mark.parametrize("dimension", [0, -1])
def test_fake_embedding_rejects_non_positive_dimension(dimension: int) -> None:
    with pytest.raises(ValueError, match="dimension must be positive"):
        FakeEmbeddingProvider(dimension=dimension)


@pytest.mark.asyncio
async def test_fake_reranker_orders_by_configured_score() -> None:
    provider = FakeRerankerProvider({"b": 0.9, "a": 0.2})

    ranked = await provider.rerank(
        query="q",
        items=[RerankItem(id="a", text="A"), RerankItem(id="b", text="B")],
        top_n=2,
    )

    assert [item.id for item in ranked] == ["b", "a"]
