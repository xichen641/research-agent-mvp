from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ChatTurn:
    role: str
    content: str
    category: str = "recent"


@dataclass(frozen=True, slots=True)
class RerankItem:
    id: str
    text: str
    score: float = 0.0


class LLMProvider(Protocol):
    async def generate_structured(
        self,
        *,
        operation: str,
        messages: list[ChatTurn],
        output_type: type[OutputT],
    ) -> OutputT: ...


class EmbeddingProvider(Protocol):
    model_id: str
    dimension: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class RerankerProvider(Protocol):
    model_id: str

    async def rerank(
        self,
        *,
        query: str,
        items: list[RerankItem],
        top_n: int,
    ) -> list[RerankItem]: ...
