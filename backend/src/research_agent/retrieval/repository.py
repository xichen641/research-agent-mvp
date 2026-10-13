import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.ingestion.models import DocumentChunkModel
from research_agent.ingestion.chunker import lexical_text
from research_agent.papers.domain import DocumentStatus, VersionStatus
from research_agent.papers.models import (
    DocumentModel,
    DocumentTaskModel,
    DocumentVersionModel,
)
from research_agent.retrieval.models import (
    ChunkEmbeddingModel,
    EmbeddingSpaceModel,
)
from research_agent.retrieval.domain import RetrievalCandidate, RetrievalQuery
from research_agent.shared.ids import EntityId, new_id
from research_agent.tasks.domain import TaskStage, TaskStatus


class EmbeddingPersistenceError(Exception):
    def __init__(self) -> None:
        super().__init__("embedding persistence failed")


class RetrievalPersistenceError(Exception):
    def __init__(self) -> None:
        super().__init__("retrieval persistence failed")


class IncompleteEmbeddingsError(Exception):
    pass


class EmbeddingSpaceConflictError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class EmbeddingSpace:
    id: EntityId
    model_id: str
    dimension: int
    pipeline_version: str
    active: bool


@dataclass(frozen=True, slots=True)
class PendingChunk:
    id: EntityId
    content: str


class SqlEmbeddingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_space(
        self,
        *,
        model_id: str,
        dimension: int,
        pipeline_version: str,
    ) -> EmbeddingSpace:
        if dimension != 1536:
            raise ValueError("the MVP embedding schema requires 1536 dimensions")
        try:
            statement = (
                insert(EmbeddingSpaceModel)
                .values(
                    id=new_id(),
                    model_id=model_id,
                    dimension=dimension,
                    pipeline_version=pipeline_version,
                    active=False,
                )
                .on_conflict_do_nothing(constraint="uq_embedding_space_identity")
            )
            await self._session.execute(statement)
            model = await self._session.scalar(
                select(EmbeddingSpaceModel).where(
                    EmbeddingSpaceModel.model_id == model_id,
                    EmbeddingSpaceModel.dimension == dimension,
                    EmbeddingSpaceModel.pipeline_version == pipeline_version,
                )
            )
            if model is None:
                raise EmbeddingPersistenceError()
            return _embedding_space(model)
        except EmbeddingPersistenceError:
            raise
        except SQLAlchemyError as error:
            raise EmbeddingPersistenceError() from error

    async def list_pending_chunks(
        self,
        *,
        version_id: EntityId,
        pipeline_version: str,
        embedding_space_id: EntityId,
        limit: int,
    ) -> list[PendingChunk]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        try:
            statement = (
                select(DocumentChunkModel.id, DocumentChunkModel.content)
                .outerjoin(
                    ChunkEmbeddingModel,
                    and_(
                        ChunkEmbeddingModel.chunk_id == DocumentChunkModel.id,
                        ChunkEmbeddingModel.embedding_space_id == embedding_space_id,
                    ),
                )
                .where(
                    DocumentChunkModel.document_version_id == version_id,
                    DocumentChunkModel.pipeline_version == pipeline_version,
                    DocumentChunkModel.content_type != "reference",
                    ChunkEmbeddingModel.chunk_id.is_(None),
                )
                .order_by(DocumentChunkModel.chunk_index)
                .limit(limit)
            )
            rows = (await self._session.execute(statement)).all()
            return [PendingChunk(id=row.id, content=row.content) for row in rows]
        except SQLAlchemyError as error:
            raise EmbeddingPersistenceError() from error

    async def upsert_embeddings(
        self,
        *,
        embedding_space_id: EntityId,
        chunks: list[PendingChunk],
        vectors: list[list[float]],
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunk and embedding counts must match")
        if any(len(vector) != 1536 for vector in vectors):
            raise ValueError("all embeddings must contain 1536 dimensions")
        if not chunks:
            return
        try:
            statement = insert(ChunkEmbeddingModel).values(
                [
                    {
                        "chunk_id": chunk.id,
                        "embedding_space_id": embedding_space_id,
                        "embedding": vector,
                    }
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ]
            )
            await self._session.execute(
                statement.on_conflict_do_update(
                    index_elements=["chunk_id", "embedding_space_id"],
                    set_={"embedding": statement.excluded.embedding},
                )
            )
        except SQLAlchemyError as error:
            raise EmbeddingPersistenceError() from error

    async def activate_if_complete(
        self,
        *,
        version_id: EntityId,
        task_id: EntityId,
        pipeline_version: str,
        embedding_space_id: EntityId,
    ) -> None:
        try:
            version = await self._session.scalar(
                select(DocumentVersionModel)
                .where(DocumentVersionModel.id == version_id)
                .with_for_update()
            )
            if version is None:
                raise IncompleteEmbeddingsError("document version does not exist")
            document = await self._session.scalar(
                select(DocumentModel)
                .where(DocumentModel.id == version.document_id)
                .with_for_update()
            )
            task = await self._session.scalar(
                select(DocumentTaskModel)
                .where(DocumentTaskModel.id == task_id)
                .with_for_update()
            )
            space = await self._session.scalar(
                select(EmbeddingSpaceModel)
                .where(EmbeddingSpaceModel.id == embedding_space_id)
                .with_for_update()
            )
            if document is None or task is None or space is None:
                raise IncompleteEmbeddingsError("embedding state does not exist")

            eligible = await self._session.scalar(
                select(func.count())
                .select_from(DocumentChunkModel)
                .where(
                    DocumentChunkModel.document_version_id == version_id,
                    DocumentChunkModel.pipeline_version == pipeline_version,
                    DocumentChunkModel.content_type != "reference",
                )
            )
            embedded = await self._session.scalar(
                select(func.count())
                .select_from(DocumentChunkModel)
                .join(
                    ChunkEmbeddingModel,
                    and_(
                        ChunkEmbeddingModel.chunk_id == DocumentChunkModel.id,
                        ChunkEmbeddingModel.embedding_space_id == embedding_space_id,
                    ),
                )
                .where(
                    DocumentChunkModel.document_version_id == version_id,
                    DocumentChunkModel.pipeline_version == pipeline_version,
                    DocumentChunkModel.content_type != "reference",
                )
            )
            if eligible != embedded:
                raise IncompleteEmbeddingsError("document embeddings are incomplete")

            active_spaces = (
                await self._session.scalars(
                    select(EmbeddingSpaceModel)
                    .where(EmbeddingSpaceModel.active.is_(True))
                    .with_for_update()
                )
            ).all()
            if any(active.id != embedding_space_id for active in active_spaces):
                raise EmbeddingSpaceConflictError(
                    "another embedding space is already active"
                )

            # Completeness validation and all externally visible state changes
            # share this transaction; readers cannot observe a partial switch.
            # 完整性校验与所有外部可见状态共用此事务，读取方不会看到半完成切换。
            space.active = True
            version.status = VersionStatus.READY
            document.status = DocumentStatus.READY
            document.current_version_id = version_id
            task.stage = TaskStage.EMBEDDING
            task.status = TaskStatus.READY
            task.progress_current = list(TaskStage).index(TaskStage.EMBEDDING) + 1
            task.progress_total = len(TaskStage)
            task.error_code = None
            task.error_message = None
        except (IncompleteEmbeddingsError, EmbeddingSpaceConflictError):
            raise
        except SQLAlchemyError as error:
            raise EmbeddingPersistenceError() from error

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except SQLAlchemyError as error:
            raise EmbeddingPersistenceError() from error

    async def rollback(self) -> None:
        try:
            await self._session.rollback()
        except SQLAlchemyError as error:
            raise EmbeddingPersistenceError() from error


def _embedding_space(model: EmbeddingSpaceModel) -> EmbeddingSpace:
    return EmbeddingSpace(
        id=model.id,
        model_id=model.model_id,
        dimension=model.dimension,
        pipeline_version=model.pipeline_version,
        active=model.active,
    )


class SqlHybridRetrievalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._search_lock = asyncio.Lock()

    async def lexical_search(
        self,
        query: RetrievalQuery,
        *,
        limit: int,
    ) -> list[RetrievalCandidate]:
        _validate_limit(limit)
        query_pairs = [
            (
                func.websearch_to_tsquery("english", lexical_query.strip()),
                func.plainto_tsquery("simple", lexical_text(lexical_query)),
            )
            for lexical_query in query.lexical_queries
        ]
        scores = [
            rank_expression
            for english_query, chinese_query in query_pairs
            for rank_expression in (
                func.ts_rank_cd(DocumentChunkModel.search_vector_en, english_query),
                func.ts_rank_cd(DocumentChunkModel.search_vector_zh, chinese_query),
            )
        ]
        score = func.greatest(*scores)
        matches = [
            match_expression
            for english_query, chinese_query in query_pairs
            for match_expression in (
                DocumentChunkModel.search_vector_en.op("@@")(english_query),
                DocumentChunkModel.search_vector_zh.op("@@")(chinese_query),
            )
        ]
        rank = (
            func.row_number()
            .over(order_by=(score.desc(), DocumentChunkModel.id))
            .label("rank")
        )
        statement = (
            _candidate_select(rank)
            .where(or_(*matches))
            .order_by(score.desc(), DocumentChunkModel.id)
            .limit(limit)
        )
        statement = _apply_scope(statement, query)
        try:
            # RetrievalService starts both recalls together; one AsyncSession
            # must serialize its actual SQL executions.
            # RetrievalService 会同时启动两路召回；同一 AsyncSession 必须串行执行 SQL。
            async with self._search_lock:
                rows = (await self._session.execute(statement)).all()
        except SQLAlchemyError as error:
            raise RetrievalPersistenceError() from error
        return [_candidate(row, lexical_rank=int(row.rank)) for row in rows]

    async def vector_search(
        self,
        query: RetrievalQuery,
        query_vector: list[float],
        *,
        limit: int,
    ) -> list[RetrievalCandidate]:
        _validate_limit(limit)
        if len(query_vector) != 1536:
            raise ValueError("query vector must contain 1536 dimensions")
        distance = ChunkEmbeddingModel.embedding.cosine_distance(query_vector)
        rank = (
            func.row_number()
            .over(order_by=(distance, DocumentChunkModel.id))
            .label("rank")
        )
        statement = (
            _candidate_select(rank)
            .join(
                ChunkEmbeddingModel,
                ChunkEmbeddingModel.chunk_id == DocumentChunkModel.id,
            )
            .join(
                EmbeddingSpaceModel,
                EmbeddingSpaceModel.id == ChunkEmbeddingModel.embedding_space_id,
            )
            .where(
                EmbeddingSpaceModel.active.is_(True),
                EmbeddingSpaceModel.pipeline_version
                == DocumentChunkModel.pipeline_version,
            )
            .order_by(distance, DocumentChunkModel.id)
            .limit(limit)
        )
        statement = _apply_scope(statement, query)
        try:
            async with self._search_lock:
                rows = (await self._session.execute(statement)).all()
        except SQLAlchemyError as error:
            raise RetrievalPersistenceError() from error
        return [_candidate(row, vector_rank=int(row.rank)) for row in rows]


def _candidate_select(rank: Any) -> Any:
    return (
        select(
            DocumentChunkModel.id.label("chunk_id"),
            DocumentModel.id.label("document_id"),
            DocumentVersionModel.id.label("document_version_id"),
            DocumentModel.title,
            DocumentChunkModel.section_path,
            DocumentChunkModel.content,
            DocumentChunkModel.content_type,
            DocumentChunkModel.page_start,
            DocumentChunkModel.page_end,
            rank,
        )
        .select_from(DocumentChunkModel)
        .join(
            DocumentVersionModel,
            DocumentVersionModel.id == DocumentChunkModel.document_version_id,
        )
        .join(
            DocumentModel,
            DocumentModel.id == DocumentVersionModel.document_id,
        )
        .where(
            DocumentVersionModel.status == VersionStatus.READY,
            DocumentModel.status == DocumentStatus.READY,
            DocumentModel.current_version_id == DocumentVersionModel.id,
            DocumentChunkModel.content_type != "reference",
        )
    )


def _apply_scope(statement: Any, query: RetrievalQuery) -> Any:
    if query.scope.document_ids:
        return statement.where(DocumentModel.id.in_(query.scope.document_ids))
    return statement


def _candidate(
    row: Any,
    *,
    lexical_rank: int | None = None,
    vector_rank: int | None = None,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=row.chunk_id,
        document_id=row.document_id,
        document_version_id=row.document_version_id,
        title=row.title,
        section_path=tuple(row.section_path),
        content=row.content,
        content_type=row.content_type,
        page_start=row.page_start,
        page_end=row.page_end,
        lexical_rank=lexical_rank,
        vector_rank=vector_rank,
    )


def _validate_limit(limit: int) -> None:
    if limit <= 0:
        raise ValueError("retrieval limit must be positive")
