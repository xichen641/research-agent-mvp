import asyncio
from dataclasses import replace
from typing import Protocol

from research_agent.providers.protocols import (
    EmbeddingProvider,
    RerankItem,
    RerankerProvider,
)
from research_agent.retrieval.domain import RetrievalCandidate, RetrievalQuery
from research_agent.retrieval.rrf import reciprocal_rank_fusion
from research_agent.shared.errors import AppError


class HybridRetrievalRepository(Protocol):
    async def lexical_search(
        self, query: RetrievalQuery, *, limit: int
    ) -> list[RetrievalCandidate]: ...

    async def vector_search(
        self,
        query: RetrievalQuery,
        query_vector: list[float],
        *,
        limit: int,
    ) -> list[RetrievalCandidate]: ...


class RetrievalService:
    def __init__(
        self,
        repository: HybridRetrievalRepository,
        embeddings: EmbeddingProvider,
        reranker: RerankerProvider,
    ) -> None:
        self._repository = repository
        self._embeddings = embeddings
        self._reranker = reranker

    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        lexical_limit: int = 40,
        vector_limit: int = 40,
        fused_limit: int = 25,
        final_limit: int = 10,
    ) -> list[RetrievalCandidate]:
        _validate_limits(lexical_limit, vector_limit, fused_limit, final_limit)
        query_vector = await self._embeddings.embed_query(query.semantic_query)
        lexical, vector = await asyncio.gather(
            self._repository.lexical_search(query, limit=lexical_limit),
            self._repository.vector_search(query, query_vector, limit=vector_limit),
        )
        fused = merge_candidates(
            reciprocal_rank_fusion(
                lexical=[str(item.chunk_id) for item in lexical],
                vector=[str(item.chunk_id) for item in vector],
            ),
            lexical,
            vector,
        )[:fused_limit]
        if not fused:
            return []
        reranked = await self._reranker.rerank(
            query=query.original,
            items=[
                RerankItem(id=str(item.chunk_id), text=item.content) for item in fused
            ],
            top_n=min(final_limit, len(fused)),
        )
        return apply_rerank_scores(fused, reranked)


def merge_candidates(
    fused: list[tuple[str, float]],
    lexical: list[RetrievalCandidate],
    vector: list[RetrievalCandidate],
) -> list[RetrievalCandidate]:
    lexical_by_id = {str(item.chunk_id): item for item in lexical}
    vector_by_id = {str(item.chunk_id): item for item in vector}
    merged: list[RetrievalCandidate] = []
    for item_id, fused_score in fused:
        lexical_item = lexical_by_id.get(item_id)
        vector_item = vector_by_id.get(item_id)
        base = lexical_item or vector_item
        if base is None:
            raise AppError(
                "RETRIEVAL_INVALID_FUSION",
                "Retrieval fusion returned an unknown candidate",
            )
        merged.append(
            replace(
                base,
                lexical_rank=(
                    None if lexical_item is None else lexical_item.lexical_rank
                ),
                vector_rank=(None if vector_item is None else vector_item.vector_rank),
                fused_score=fused_score,
            )
        )
    return merged


def apply_rerank_scores(
    candidates: list[RetrievalCandidate],
    reranked: list[RerankItem],
) -> list[RetrievalCandidate]:
    candidates_by_id = {str(item.chunk_id): item for item in candidates}
    seen: set[str] = set()
    result: list[RetrievalCandidate] = []
    for reranked_item in reranked:
        candidate = candidates_by_id.get(reranked_item.id)
        if candidate is None or reranked_item.id in seen:
            raise AppError(
                "RERANKER_INVALID_RESPONSE",
                "Reranker returned an invalid response",
            )
        seen.add(reranked_item.id)
        result.append(replace(candidate, rerank_score=reranked_item.score))
    return result


def _validate_limits(*limits: int) -> None:
    if any(limit <= 0 for limit in limits):
        raise ValueError("retrieval limits must be positive")
