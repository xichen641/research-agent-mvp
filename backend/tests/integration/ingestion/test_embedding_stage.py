from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.ingestion.models import ChunkDraft
from research_agent.ingestion.pipeline import IngestionPipeline
from research_agent.papers.domain import CreatedDocument, DocumentStatus, VersionStatus
from research_agent.papers.models import (
    DocumentModel,
    DocumentTaskModel,
    DocumentVersionModel,
)
from research_agent.papers.repository import SqlPaperRepository
from research_agent.providers.fake import FakeEmbeddingProvider
from research_agent.retrieval.models import (
    ChunkEmbeddingModel,
    EmbeddingSpaceModel,
)
from research_agent.retrieval.repository import SqlEmbeddingRepository
from research_agent.shared.errors import AppError
from research_agent.tasks.domain import TaskStage, TaskStatus
from research_agent.tasks.repository import SqlTaskRepository


class UnusedParser:
    name = "unused"
    version = "1"

    def parse(self, source: Path):
        del source
        raise AssertionError("an EMBEDDING-stage retry must not parse again")


class UnusedStorage:
    def get(self, *, key: str) -> BinaryIO:
        del key
        return BytesIO()

    def put(self, *, key: str, stream: BinaryIO, content_type: str) -> None:
        del key, stream, content_type

    def delete(self, *, key: str) -> None:
        del key

    def presigned_get(self, *, key: str, expires: timedelta) -> str:
        del key, expires
        return "https://example.test"


class TransactionObservingEmbeddings(FakeEmbeddingProvider):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(dimension=1536)
        self._session = session
        self.batches: list[list[str]] = []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        assert not self._session.in_transaction()
        self.batches.append(list(texts))
        return await super().embed_documents(texts)


class FailSecondBatchOnceEmbeddings(TransactionObservingEmbeddings):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self._failed = False

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if len(self.batches) == 1 and not self._failed:
            assert not self._session.in_transaction()
            self.batches.append(list(texts))
            self._failed = True
            raise RuntimeError("provider secret must not escape")
        return await super().embed_documents(texts)


def draft(index: int, content: str, content_type: str = "text") -> ChunkDraft:
    return ChunkDraft(
        chunk_index=index,
        section_path=("Methods",),
        content=content,
        content_type=content_type,
        page_start=1,
        page_end=1,
        element_ids=(f"element-{index}",),
        token_count=len(content.split()),
    )


async def seed_embedding_stage(
    session: AsyncSession,
) -> tuple[SqlPaperRepository, SqlTaskRepository, CreatedDocument]:
    papers = SqlPaperRepository(session)
    tasks = SqlTaskRepository(session)
    created = await papers.create_document_with_version(
        title="Embedding paper",
        sha256="e" * 64,
        filename="paper.pdf",
        source_object_key="papers/source.pdf",
        parser_name="docling",
        parser_version="2.113.0",
        pipeline_version="pipeline-v1",
    )
    await papers.replace_chunks(
        created.version_id,
        "pipeline-v1",
        [
            draft(0, "first body chunk"),
            draft(1, "references only", "reference"),
            draft(2, "second body chunk", "table"),
        ],
    )
    await tasks.update_task(
        created.task_id,
        stage=TaskStage.EMBEDDING,
        status=TaskStatus.RUNNING,
        progress_current=5,
        progress_total=len(TaskStage),
    )
    await papers.commit()
    return papers, tasks, created


@pytest.mark.integration
def test_embedding_migration_is_single_head() -> None:
    config = Config(str(Path(__file__).parents[3] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["0009_durable_chat_runs"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_embedding_schema_has_vector_hnsw_and_cascading_foreign_keys(
    db_session: AsyncSession,
) -> None:
    connection = await db_session.connection()
    columns, indexes, foreign_keys = await connection.run_sync(
        lambda sync_connection: (
            inspect(sync_connection).get_columns("chunk_embedding"),
            inspect(sync_connection).get_indexes("chunk_embedding"),
            inspect(sync_connection).get_foreign_keys("chunk_embedding"),
        )
    )

    embedding = next(column for column in columns if column["name"] == "embedding")
    assert "VECTOR(1536)" in str(embedding["type"]).upper()
    hnsw = next(
        index for index in indexes if index["name"] == "ix_chunk_embedding_hnsw"
    )
    dialect_options = hnsw.get("dialect_options", {})
    assert dialect_options["postgresql_using"] == "hnsw"
    assert dialect_options["postgresql_ops"] == {"embedding": "vector_cosine_ops"}
    assert {foreign_key["referred_table"] for foreign_key in foreign_keys} == {
        "document_chunk",
        "embedding_space",
    }
    assert all(
        foreign_key.get("options", {}).get("ondelete") == "CASCADE"
        for foreign_key in foreign_keys
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_embedding_stage_is_idempotent_excludes_references_and_activates_ready(
    db_session: AsyncSession,
) -> None:
    papers, tasks, created = await seed_embedding_stage(db_session)
    embeddings = SqlEmbeddingRepository(db_session)
    provider = TransactionObservingEmbeddings(db_session)
    pipeline = IngestionPipeline(
        UnusedParser(),
        UnusedStorage(),
        papers,
        tasks,
        embedding_provider=provider,
        embedding_repository=embeddings,
        embedding_batch_size=1,
    )

    await pipeline.embed_and_activate(created.version_id)
    await pipeline.embed_and_activate(created.version_id)

    assert provider.batches == [["first body chunk"], ["second body chunk"]]
    assert (
        await db_session.scalar(select(func.count()).select_from(EmbeddingSpaceModel))
        == 1
    )
    assert (
        await db_session.scalar(select(func.count()).select_from(ChunkEmbeddingModel))
        == 2
    )
    document = await db_session.get(DocumentModel, created.document_id)
    version = await db_session.get(DocumentVersionModel, created.version_id)
    task = await db_session.get(DocumentTaskModel, created.task_id)
    space = await db_session.scalar(select(EmbeddingSpaceModel))
    assert document is not None and document.status == DocumentStatus.READY
    assert document.current_version_id == created.version_id
    assert version is not None and version.status == VersionStatus.READY
    assert task is not None and task.status == TaskStatus.READY
    assert task.stage == TaskStage.EMBEDDING
    assert task.error_code is None and task.error_message is None
    assert space is not None and space.active is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_embedding_stage_restarts_from_first_missing_batch_after_provider_failure(
    db_session: AsyncSession,
) -> None:
    papers, tasks, created = await seed_embedding_stage(db_session)
    embeddings = SqlEmbeddingRepository(db_session)
    provider = FailSecondBatchOnceEmbeddings(db_session)
    pipeline = IngestionPipeline(
        UnusedParser(),
        UnusedStorage(),
        papers,
        tasks,
        embedding_provider=provider,
        embedding_repository=embeddings,
        embedding_batch_size=1,
    )

    with pytest.raises(AppError) as captured:
        await pipeline.embed_and_activate(created.version_id)

    assert captured.value.code == "EMBEDDING_PROVIDER_FAILED"
    assert captured.value.message == (
        "Embedding provider could not process document chunks"
    )
    assert (
        await db_session.scalar(select(func.count()).select_from(ChunkEmbeddingModel))
        == 1
    )
    failed_task = await db_session.get(DocumentTaskModel, created.task_id)
    assert failed_task is not None and failed_task.status == TaskStatus.FAILED
    assert failed_task.error_code == "EMBEDDING_PROVIDER_FAILED"

    await pipeline.embed_and_activate(created.version_id)

    assert provider.batches == [
        ["first body chunk"],
        ["second body chunk"],
        ["second body chunk"],
    ]
    assert (
        await db_session.scalar(select(func.count()).select_from(ChunkEmbeddingModel))
        == 2
    )
    recovered_task = await db_session.get(DocumentTaskModel, created.task_id)
    assert recovered_task is not None and recovered_task.status == TaskStatus.READY
