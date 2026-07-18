from typing import Protocol

from langchain_openai import OpenAIEmbeddings
from pydantic import SecretStr

from research_agent.config import Settings


class AsyncEmbeddingClient(Protocol):
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def aembed_query(self, text: str) -> list[float]: ...


class OpenAICompatibleEmbeddingProvider:
    """Keep LangChain types behind the project-owned embedding contract."""

    def __init__(
        self,
        *,
        client: AsyncEmbeddingClient,
        model_id: str,
        dimension: int,
    ) -> None:
        if dimension <= 0:
            raise ValueError("embedding dimension must be positive")
        self._client = client
        self.model_id = model_id
        self.dimension = dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = await self._client.aembed_documents(texts)
        if len(vectors) != len(texts):
            raise ValueError("embedding response count does not match input count")
        self._validate_dimensions(vectors)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        vector = await self._client.aembed_query(text)
        self._validate_dimensions([vector])
        return vector

    def _validate_dimensions(self, vectors: list[list[float]]) -> None:
        if any(len(vector) != self.dimension for vector in vectors):
            raise ValueError("embedding dimension does not match active space")


def build_openai_embedding_provider(
    settings: Settings,
) -> OpenAICompatibleEmbeddingProvider:
    client = OpenAIEmbeddings(
        api_key=(
            SecretStr(settings.embedding_api_key)
            if settings.embedding_api_key
            else None
        ),
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimension,
    )
    return OpenAICompatibleEmbeddingProvider(
        client=client,
        model_id=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
