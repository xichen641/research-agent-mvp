from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from research_agent.providers.fake import (
    FakeEmbeddingProvider,
    FakeRerankerProvider,
)
from research_agent.providers.protocols import RerankItem
from research_agent.retrieval.domain import (
    RetrievalCandidate,
    RetrievalQuery,
    RetrievalScope,
)
from research_agent.retrieval.service import RetrievalService
from research_agent.shared.errors import AppError


def candidate(
    chunk_id: UUID,
    *,
    lexical_rank: int | None = None,
    vector_rank: int | None = None,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        document_id=uuid4(),
        document_version_id=uuid4(),
        title="Paper",
        section_path=("Methods",),
        content=f"content-{chunk_id}",
        content_type="text",
        page_start=1,
        page_end=1,
        lexical_rank=lexical_rank,
        vector_rank=vector_rank,
    )


@dataclass
class RecordingRepository:
    lexical: list[RetrievalCandidate]
    vector: list[RetrievalCandidate]

    def __post_init__(self) -> None:
        self.vector_query: list[float] | None = None

    async def lexical_search(
        self, query: RetrievalQuery, *, limit: int
    ) -> list[RetrievalCandidate]:
        del query
        return self.lexical[:limit]

    async def vector_search(
        self,
        query: RetrievalQuery,
        query_vector: list[float],
        *,
        limit: int,
    ) -> list[RetrievalCandidate]:
        del query
        self.vector_query = query_vector
        return self.vector[:limit]


@pytest.mark.asyncio
async def test_service_fuses_candidates_and_returns_reranker_order() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    repository = RecordingRepository(
        lexical=[candidate(a, lexical_rank=1), candidate(b, lexical_rank=2)],
        vector=[candidate(b, vector_rank=1), candidate(c, vector_rank=2)],
    )
    embeddings = FakeEmbeddingProvider(dimension=1536)
    reranker = FakeRerankerProvider(scores={str(a): 0.1, str(b): 0.9, str(c): 0.8})
    service = RetrievalService(repository, embeddings, reranker)
    query = RetrievalQuery(
        original="question",
        lexical_queries=("question",),
        semantic_query="semantic question",
        scope=RetrievalScope(),
    )

    result = await service.retrieve(query, final_limit=2)

    assert [item.chunk_id for item in result] == [b, c]
    assert result[0].lexical_rank == 2
    assert result[0].vector_rank == 1
    assert result[0].fused_score == pytest.approx(1 / 62 + 1 / 61)
    assert result[0].rerank_score == 0.9
    assert result[1].lexical_rank is None
    assert result[1].vector_rank == 2
    assert repository.vector_query == await embeddings.embed_query(query.semantic_query)


@pytest.mark.asyncio
async def test_service_applies_a_workload_specific_default_final_limit() -> None:
    ids = [uuid4() for _ in range(3)]
    repository = RecordingRepository(
        lexical=[candidate(item, lexical_rank=index) for index, item in enumerate(ids, 1)],
        vector=[],
    )
    service = RetrievalService(
        repository,
        FakeEmbeddingProvider(dimension=1536),
        FakeRerankerProvider(
            scores={str(item): 1 - index / 10 for index, item in enumerate(ids)}
        ),
        default_final_limit=2,
    )
    query = RetrievalQuery("question", ("question",), "question", RetrievalScope())

    result = await service.retrieve(query)

    assert len(result) == 2


class UnknownIdReranker:
    model_id = "invalid"

    async def rerank(
        self,
        *,
        query: str,
        items: list[RerankItem],
        top_n: int,
    ) -> list[RerankItem]:
        del query, items, top_n
        return [RerankItem(id=str(uuid4()), text="unknown", score=1.0)]


@pytest.mark.asyncio
async def test_service_rejects_unknown_reranker_candidate() -> None:
    chunk_id = uuid4()
    repository = RecordingRepository(
        lexical=[candidate(chunk_id, lexical_rank=1)],
        vector=[],
    )
    service = RetrievalService(
        repository,
        FakeEmbeddingProvider(dimension=1536),
        UnknownIdReranker(),
    )
    query = RetrievalQuery("question", ("question",), "question", RetrievalScope())

    with pytest.raises(AppError, match="invalid response") as captured:
        await service.retrieve(query)

    assert captured.value.code == "RERANKER_INVALID_RESPONSE"
