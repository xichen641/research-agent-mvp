from pathlib import Path
from datetime import timedelta
from io import BytesIO
from typing import BinaryIO, cast
from uuid import UUID

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.base import Executable

from research_agent.ingestion.chunker import StructuralChunker
from research_agent.ingestion.contracts import ParsedDocument, ParsedElement
from research_agent.ingestion.models import (
    ChunkDraft,
    ChunkingConfig,
    DocumentChunkModel,
)
from research_agent.ingestion.pipeline import IngestionPipeline
from research_agent.papers.domain import (
    DocumentVersionRecord,
    PaperPersistenceError,
    VersionStatus,
)
from research_agent.papers.repository import SqlPaperRepository
from research_agent.shared.errors import AppError
from research_agent.tasks.domain import TaskRecord, TaskStage, TaskStatus


def draft(index: int, content: str) -> ChunkDraft:
    return ChunkDraft(
        chunk_index=index,
        section_path=("Methods",),
        content=content,
        content_type="text",
        page_start=1,
        page_end=2,
        element_ids=(f"element-{index}",),
        token_count=len(content.split()),
    )


async def create_version(repository: SqlPaperRepository, suffix: str = "a"):
    created = await repository.create_document_with_version(
        title="Chunked paper",
        sha256=suffix * 64,
        filename="paper.pdf",
        source_object_key="papers/source.pdf",
        parser_name="docling",
        parser_version="2.113.0",
        pipeline_version="pipeline-v1",
    )
    return created.version_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_replace_chunks_preserves_ids_and_deletes_stale_indexes(
    db_session: AsyncSession,
) -> None:
    repository = SqlPaperRepository(db_session)
    version_id = await create_version(repository)
    await repository.replace_chunks(
        version_id,
        "pipeline-v1",
        [draft(0, "alpha beta"), draft(1, "gamma"), draft(2, "stale")],
    )
    await repository.commit()

    original = (
        await db_session.scalars(
            select(DocumentChunkModel)
            .where(DocumentChunkModel.document_version_id == version_id)
            .order_by(DocumentChunkModel.chunk_index)
            .execution_options(populate_existing=True)
        )
    ).all()
    original_ids = [row.id for row in original]

    await repository.replace_chunks(
        version_id,
        "pipeline-v1",
        [draft(0, "alpha revised"), draft(1, "gamma revised")],
    )
    await repository.commit()

    replaced = (
        await db_session.scalars(
            select(DocumentChunkModel)
            .where(DocumentChunkModel.document_version_id == version_id)
            .order_by(DocumentChunkModel.chunk_index)
            .execution_options(populate_existing=True)
        )
    ).all()
    assert [row.id for row in replaced] == original_ids[:2]
    assert [row.chunk_index for row in replaced] == [0, 1]
    assert [row.content for row in replaced] == ["alpha revised", "gamma revised"]
    assert [row.lexical_text for row in replaced] == [
        "alpha revised",
        "gamma revised",
    ]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_replace_chunks_rejects_non_contiguous_indexes_before_db_work(
    db_session: AsyncSession,
) -> None:
    repository = SqlPaperRepository(db_session)
    version_id = await create_version(repository, "b")

    with pytest.raises(ValueError, match="contiguous"):
        await repository.replace_chunks(
            version_id,
            "pipeline-v1",
            [draft(0, "first"), draft(2, "third")],
        )

    assert (await db_session.scalar(select(DocumentChunkModel.id).limit(1))) is None


class FailSecondExecuteSession:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.execute_calls = 0

    async def execute(self, statement: Executable):
        self.execute_calls += 1
        if self.execute_calls == 2:
            raise SQLAlchemyError("DELETE private chunks token=secret")
        return await self._session.execute(statement)

    async def rollback(self) -> None:
        await self._session.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failed_replacement_rolls_back_partial_upsert_and_preserves_old_set(
    db_session: AsyncSession,
) -> None:
    seed_repository = SqlPaperRepository(db_session)
    version_id = await create_version(seed_repository, "c")
    await seed_repository.replace_chunks(
        version_id,
        "pipeline-v1",
        [draft(0, "original zero"), draft(1, "original one")],
    )
    await seed_repository.commit()
    original = (
        await db_session.scalars(
            select(DocumentChunkModel)
            .where(DocumentChunkModel.document_version_id == version_id)
            .order_by(DocumentChunkModel.chunk_index)
        )
    ).all()
    original_state = [(row.id, row.content) for row in original]

    failing_session = FailSecondExecuteSession(db_session)
    failing_repository = SqlPaperRepository(cast(AsyncSession, failing_session))
    with pytest.raises(PaperPersistenceError) as raised:
        await failing_repository.replace_chunks(
            version_id,
            "pipeline-v1",
            [draft(0, "revised zero")],
        )
    await failing_repository.rollback()

    preserved = (
        await db_session.scalars(
            select(DocumentChunkModel)
            .where(DocumentChunkModel.document_version_id == version_id)
            .order_by(DocumentChunkModel.chunk_index)
            .execution_options(populate_existing=True)
        )
    ).all()
    assert [(row.id, row.content) for row in preserved] == original_state
    assert "private" not in str(raised.value).lower()
    assert "secret" not in str(raised.value).lower()


@pytest.mark.integration
def test_chunk_migration_has_generated_vectors_indexes_and_single_head(
    migrated_database_url: str,
) -> None:
    del migrated_database_url
    config = Config(str(Path(__file__).parents[3] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["0003_chunks"]


@pytest.mark.integration
def test_alembic_schema_has_no_drift(migrated_database_url: str) -> None:
    config = Config(str(Path(__file__).parents[3] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", migrated_database_url)

    command.check(config)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chunk_schema_matches_search_contract(
    db_session: AsyncSession,
) -> None:
    connection = await db_session.connection()
    columns, indexes, unique_constraints, foreign_keys = await connection.run_sync(
        lambda sync_connection: (
            inspect(sync_connection).get_columns("document_chunk"),
            inspect(sync_connection).get_indexes("document_chunk"),
            inspect(sync_connection).get_unique_constraints("document_chunk"),
            inspect(sync_connection).get_foreign_keys("document_chunk"),
        )
    )
    by_name = {column["name"]: column for column in columns}
    search_en = by_name["search_vector_en"].get("computed")
    search_zh = by_name["search_vector_zh"].get("computed")

    assert search_en is not None
    assert search_en.get("persisted") is True
    assert "to_tsvector('english'" in search_en["sqltext"]
    assert search_zh is not None
    assert search_zh.get("persisted") is True
    assert "to_tsvector('simple'" in search_zh["sqltext"]
    assert {index["name"] for index in indexes} >= {
        "ix_chunk_search_en",
        "ix_chunk_search_zh",
    }
    assert any(
        constraint["column_names"]
        == ["document_version_id", "chunk_index", "pipeline_version"]
        for constraint in unique_constraints
    )
    assert any(
        foreign_key["referred_table"] == "document_version"
        and foreign_key.get("options", {}).get("ondelete") == "CASCADE"
        for foreign_key in foreign_keys
    )


DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000011")
VERSION_ID = UUID("00000000-0000-0000-0000-000000000012")
TASK_ID = UUID("00000000-0000-0000-0000-000000000013")


class MemoryStorage:
    def get(self, *, key: str) -> BinaryIO:
        assert key == "papers/source.pdf"
        return BytesIO(b"%PDF source")

    def put(self, *, key: str, stream: BinaryIO, content_type: str) -> None:
        del key, stream, content_type

    def delete(self, *, key: str) -> None:
        del key

    def presigned_get(self, *, key: str, expires: timedelta) -> str:
        del key, expires
        return "https://example.test"


class CountingParser:
    name = "fake-parser"
    version = "1.0"

    def __init__(self, parsed: ParsedDocument) -> None:
        self.parsed = parsed
        self.calls = 0

    def parse(self, source: Path) -> ParsedDocument:
        assert source.read_bytes() == b"%PDF source"
        self.calls += 1
        return self.parsed


class PipelinePapers:
    def __init__(self) -> None:
        self.version = DocumentVersionRecord(
            id=VERSION_ID,
            document_id=DOCUMENT_ID,
            filename="paper.pdf",
            source_object_key="papers/source.pdf",
            parser_name="docling",
            parser_version="unparsed",
            pipeline_version="pipeline-v1",
            status=VersionStatus.PROCESSING,
        )
        self.persisted_chunks: list[list[ChunkDraft]] = []
        self.failed: list[UUID] = []
        self.commits = 0
        self.rollbacks = 0

    async def get_version(self, version_id: UUID) -> DocumentVersionRecord | None:
        return self.version if version_id == VERSION_ID else None

    async def persist_parse_artifacts(self, version_id: UUID, **values: object) -> None:
        del version_id, values

    async def mark_parse_failed(self, version_id: UUID) -> None:
        self.failed.append(version_id)

    async def replace_chunks(
        self,
        version_id: UUID,
        pipeline_version: str,
        chunks: list[ChunkDraft],
    ) -> None:
        assert version_id == VERSION_ID
        assert pipeline_version == "pipeline-v1"
        self.persisted_chunks.append(chunks)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class PipelineTasks:
    def __init__(self, *, stage: TaskStage = TaskStage.QUEUED) -> None:
        self.task = TaskRecord(
            id=TASK_ID,
            document_version_id=VERSION_ID,
            stage=stage,
            status=TaskStatus.RUNNING,
            progress_current=0,
            progress_total=len(TaskStage),
            retry_count=0,
            error_code="old",
            error_message="old",
        )
        self.updates: list[dict[str, object]] = []
        self.fail_stage: TaskStage | None = None

    async def get_task_for_version(self, version_id: UUID) -> TaskRecord | None:
        return self.task if version_id == VERSION_ID else None

    async def update_task(self, task_id: UUID, **values: object) -> None:
        assert task_id == TASK_ID
        if (
            values["stage"] is self.fail_stage
            and values["status"] is TaskStatus.RUNNING
        ):
            raise RuntimeError("private task SQL token=secret")
        self.updates.append(values)


def pipeline_document(*, low_quality: bool = False) -> ParsedDocument:
    text = "short" if low_quality else "word " * 100
    return ParsedDocument(
        markdown=text,
        json_bytes=b"{}",
        elements=[ParsedElement("body", "text", text, ("Methods",), 1, None)],
        images=[],
        page_count=1,
    )


def pipeline(
    parser: CountingParser,
    papers: PipelinePapers,
    tasks: PipelineTasks,
    chunker: StructuralChunker | None = None,
) -> IngestionPipeline:
    return IngestionPipeline(
        parser,
        MemoryStorage(),
        papers,
        tasks,
        chunker
        or StructuralChunker(
            ChunkingConfig(target_tokens=200, overlap_tokens=20),
            token_counter=lambda text: len(text.split()),
        ),
    )


@pytest.mark.asyncio
async def test_run_reuses_parsed_document_and_stops_at_embedding() -> None:
    parser = CountingParser(pipeline_document())
    papers = PipelinePapers()
    tasks = PipelineTasks()

    await pipeline(parser, papers, tasks).run(VERSION_ID)

    assert parser.calls == 1
    assert len(papers.persisted_chunks) == 1
    assert papers.persisted_chunks[0][0].element_ids == ("body",)
    assert [update["stage"] for update in tasks.updates] == [
        TaskStage.VALIDATING,
        TaskStage.PARSING,
        TaskStage.NORMALIZING,
        TaskStage.CHUNKING,
        TaskStage.EMBEDDING,
    ]
    assert [update["progress_current"] for update in tasks.updates] == [1, 2, 3, 4, 5]
    assert all(update["status"] is TaskStatus.RUNNING for update in tasks.updates)
    assert tasks.updates[-1]["error_code"] is None
    assert tasks.updates[-1]["error_message"] is None


@pytest.mark.asyncio
async def test_run_stops_review_document_before_chunking() -> None:
    parser = CountingParser(pipeline_document(low_quality=True))
    papers = PipelinePapers()
    tasks = PipelineTasks()

    await pipeline(parser, papers, tasks).run(VERSION_ID)

    assert parser.calls == 1
    assert papers.persisted_chunks == []
    assert [update["stage"] for update in tasks.updates] == [
        TaskStage.VALIDATING,
        TaskStage.PARSING,
        TaskStage.PARSING,
    ]
    assert tasks.updates[-1]["status"] is TaskStatus.FAILED
    assert tasks.updates[-1]["error_code"] == "PARSE_QUALITY_REVIEW"


@pytest.mark.asyncio
async def test_run_at_embedding_is_noop_and_preserves_chunks() -> None:
    parser = CountingParser(pipeline_document())
    papers = PipelinePapers()
    papers.persisted_chunks = [[draft(0, "existing")]]
    tasks = PipelineTasks(stage=TaskStage.EMBEDDING)

    await pipeline(parser, papers, tasks).run(VERSION_ID)

    assert parser.calls == 0
    assert papers.persisted_chunks == [[draft(0, "existing")]]
    assert tasks.updates == []
    assert papers.commits == 0


class FailingChunker(StructuralChunker):
    def chunk(self, elements):
        del elements
        raise RuntimeError("confidential paper text token=secret")


@pytest.mark.asyncio
async def test_chunking_failure_rolls_back_and_persists_sanitized_stage() -> None:
    parser = CountingParser(pipeline_document())
    papers = PipelinePapers()
    tasks = PipelineTasks()

    with pytest.raises(AppError) as raised:
        await pipeline(parser, papers, tasks, FailingChunker()).run(VERSION_ID)

    assert raised.value.code == "CHUNKING_FAILED"
    assert raised.value.message == "Document chunks could not be created"
    assert "secret" not in str(raised.value).lower()
    assert papers.rollbacks == 1
    assert papers.failed == [VERSION_ID]
    assert tasks.updates[-1]["stage"] is TaskStage.CHUNKING
    assert tasks.updates[-1]["status"] is TaskStatus.FAILED
    assert tasks.updates[-1]["progress_current"] == 4


@pytest.mark.asyncio
async def test_normalizing_state_failure_is_sanitized_at_actual_stage() -> None:
    parser = CountingParser(pipeline_document())
    papers = PipelinePapers()
    tasks = PipelineTasks()
    tasks.fail_stage = TaskStage.NORMALIZING

    with pytest.raises(AppError) as raised:
        await pipeline(parser, papers, tasks).run(VERSION_ID)

    assert raised.value.code == "NORMALIZATION_FAILED"
    assert raised.value.message == "Document could not be normalized"
    assert "private" not in str(raised.value).lower()
    assert tasks.updates[-1]["stage"] is TaskStage.NORMALIZING
    assert tasks.updates[-1]["status"] is TaskStatus.FAILED
    assert tasks.updates[-1]["progress_current"] == 3


class FailingChunkPapers(PipelinePapers):
    async def replace_chunks(
        self,
        version_id: UUID,
        pipeline_version: str,
        chunks: list[ChunkDraft],
    ) -> None:
        del version_id, pipeline_version, chunks
        raise RuntimeError("INSERT private chunks token=secret")


@pytest.mark.asyncio
async def test_chunk_persistence_failure_is_sanitized_and_recovered() -> None:
    parser = CountingParser(pipeline_document())
    papers = FailingChunkPapers()
    tasks = PipelineTasks()

    with pytest.raises(AppError) as raised:
        await pipeline(parser, papers, tasks).run(VERSION_ID)

    assert raised.value.code == "CHUNK_PERSISTENCE_FAILED"
    assert raised.value.message == "Document chunks could not be persisted"
    assert "private" not in str(raised.value).lower()
    assert papers.rollbacks == 1
    assert papers.failed == [VERSION_ID]
    assert tasks.updates[-1]["stage"] is TaskStage.CHUNKING
    assert tasks.updates[-1]["status"] is TaskStatus.FAILED
