from dataclasses import dataclass

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.ingestion.models import DocumentChunkModel
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
from research_agent.shared.ids import EntityId, new_id
from research_agent.tasks.domain import TaskStage, TaskStatus


class EmbeddingPersistenceError(Exception):
    def __init__(self) -> None:
        super().__init__("embedding persistence failed")


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
                        ChunkEmbeddingModel.embedding_space_id
                        == embedding_space_id,
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
                        ChunkEmbeddingModel.embedding_space_id
                        == embedding_space_id,
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
