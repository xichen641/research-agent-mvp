import hashlib

from pydantic import BaseModel

from research_agent.providers.protocols import (
    ChatTurn,
    OutputT,
    RerankItem,
)


_EMBEDDING_DOMAIN = b"research-agent-fake-embedding\0"


class FakeLLMProvider:
    def __init__(self, responses: dict[str, dict[str, object]]) -> None:
        self._responses = responses

    def set_response(
        self,
        operation: str,
        response: BaseModel | dict[str, object],
    ) -> None:
        self._responses[operation] = (
            response.model_dump() if isinstance(response, BaseModel) else response
        )

    async def generate_structured(
        self,
        *,
        operation: str,
        messages: list[ChatTurn],
        output_type: type[OutputT],
    ) -> OutputT:
        del messages
        return output_type.model_validate(self._responses[operation])


class FakeEmbeddingProvider:
    model_id = "fake-embedding"

    def __init__(self, dimension: int = 8) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.dimension = dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed_query(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        text_bytes = text.encode("utf-8")
        values: list[float] = []
        block_index = 0
        while len(values) < self.dimension:
            digest = hashlib.sha256(
                _EMBEDDING_DOMAIN
                + block_index.to_bytes(8, "big")
                + text_bytes
            ).digest()
            values.extend(byte / 255.0 for byte in digest)
            block_index += 1
        return values[: self.dimension]


class FakeRerankerProvider:
    model_id = "fake-reranker"

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores

    async def rerank(
        self,
        *,
        query: str,
        items: list[RerankItem],
        top_n: int,
    ) -> list[RerankItem]:
        del query
        scored = [
            RerankItem(id=item.id, text=item.text, score=self._scores.get(item.id, 0.0))
            for item in items
        ]
        return sorted(scored, key=lambda item: item.score, reverse=True)[:top_n]
