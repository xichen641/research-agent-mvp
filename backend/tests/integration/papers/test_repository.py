import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from research_agent.papers.domain import (
    DocumentStatus,
    PaperPersistenceError,
    VersionStatus,
)
from research_agent.papers.models import DocumentTaskModel
from research_agent.papers.repository import SqlPaperRepository
from research_agent.shared.ids import new_id
from research_agent.tasks.domain import TaskStage, TaskStatus
from research_agent.tasks.repository import SqlTaskRepository
from research_agent.tasks.repository import TaskPersistenceError


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_document_version_and_initial_task(
    db_session: AsyncSession,
) -> None:
    repository = SqlPaperRepository(db_session)

    created = await repository.create_document_with_version(
        title="Example",
        sha256="a" * 64,
        filename="example.pdf",
        source_object_key="papers/doc/source/version.pdf",
        parser_name="docling",
        parser_version="unparsed",
        pipeline_version="1",
    )
    await db_session.commit()

    loaded = await repository.get_document(created.document_id)
    assert loaded is not None
    assert loaded.status is DocumentStatus.PROCESSING
    assert loaded.current_version_id == created.version_id
    assert loaded.latest_task_id == created.task_id

    version = await repository.get_version(created.version_id)
    assert version is not None
    assert version.status is VersionStatus.PROCESSING

    task = await repository.get_task(created.task_id)
    assert task is not None
    assert task.stage is TaskStage.QUEUED
    assert task.status is TaskStatus.QUEUED
    assert task.progress_current == 0
    assert task.progress_total == len(TaskStage)

    duplicate_lookup = await repository.find_by_sha256("a" * 64)
    assert duplicate_lookup == loaded


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parse_artifacts_round_trip_and_latest_task_ordering(
    db_session: AsyncSession,
) -> None:
    papers = SqlPaperRepository(db_session)
    tasks = SqlTaskRepository(db_session)
    created = await papers.create_document_with_version(
        title="Parsed",
        sha256="b" * 64,
        filename="parsed.pdf",
        source_object_key="papers/doc/source/version.pdf",
        parser_name="docling",
        parser_version="unparsed",
        pipeline_version="1",
    )
    await db_session.flush()
    newest_task_id = new_id()
    db_session.add(
        DocumentTaskModel(
            id=newest_task_id,
            document_version_id=created.version_id,
            stage=TaskStage.VALIDATING,
            status=TaskStatus.RUNNING,
            progress_current=1,
            progress_total=len(TaskStage),
            retry_count=0,
            error_code=None,
            error_message=None,
            created_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
    )
    await papers.persist_parse_artifacts(
        created.version_id,
        parser_name="docling",
        parser_version="2.113.0",
        json_object_key="papers/doc/parsed/version/document.json",
        markdown_object_key="papers/doc/parsed/version/document.md",
        image_object_keys=(
            "papers/doc/images/version/one.png",
            "papers/doc/images/version/two.png",
        ),
        parse_quality={
            "total_pages": 2,
            "parsed_pages": 2,
            "body_characters": 250,
            "empty_pages": [],
            "section_count": 1,
            "table_count": 0,
            "image_count": 2,
            "warnings": [],
        },
        version_status=VersionStatus.PROCESSING,
        document_status=DocumentStatus.PROCESSING,
    )
    await papers.commit()

    version = await papers.get_version(created.version_id)
    assert version is not None
    assert version.parser_name == "docling"
    assert version.parser_version == "2.113.0"
    assert version.parsed_json_object_key == (
        "papers/doc/parsed/version/document.json"
    )
    assert version.parsed_markdown_object_key == (
        "papers/doc/parsed/version/document.md"
    )
    assert version.image_object_keys == (
        "papers/doc/images/version/one.png",
        "papers/doc/images/version/two.png",
    )
    assert version.parse_quality == {
        "total_pages": 2,
        "parsed_pages": 2,
        "body_characters": 250,
        "empty_pages": [],
        "section_count": 1,
        "table_count": 0,
        "image_count": 2,
        "warnings": [],
    }
    latest_task = await tasks.get_task_for_version(created.version_id)
    assert latest_task is not None
    assert latest_task.id == newest_task_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parse_artifact_migration_upgrade_downgrade_and_single_head(
    migrated_database_url: str,
) -> None:
    config = Config(str(Path(__file__).parents[3] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", migrated_database_url)
    expected_columns = {
        "parsed_json_object_key",
        "parsed_markdown_object_key",
        "image_object_keys",
        "parse_quality",
    }
    engine = create_async_engine(migrated_database_url)
    try:
        await asyncio.to_thread(command.upgrade, config, "head")
        async with engine.connect() as connection:
            upgraded_columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns(
                        "document_version"
                    )
                }
            )
        assert expected_columns <= upgraded_columns
        assert ScriptDirectory.from_config(config).get_heads() == [
            "0004_embeddings"
        ]

        await asyncio.to_thread(command.downgrade, config, "0001_papers_tasks")
        async with engine.connect() as connection:
            downgraded_columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns(
                        "document_version"
                    )
                }
            )
        assert expected_columns.isdisjoint(downgraded_columns)
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
        await engine.dispose()


class FailingGetSession:
    async def get(self, model: object, entity_id: object) -> object:
        del model, entity_id
        raise SQLAlchemyError("SELECT private_table token=driver-secret")

    async def execute(self, statement: object) -> object:
        del statement
        raise SQLAlchemyError("SELECT private_table token=driver-secret")

    async def commit(self) -> None:
        raise SQLAlchemyError("COMMIT token=driver-secret")

    async def rollback(self) -> None:
        raise SQLAlchemyError("ROLLBACK token=driver-secret")


class FailingAssignmentModel:
    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise SQLAlchemyError("UPDATE private_task token=assignment-secret")


class FailingAssignmentSession:
    async def get(self, model: object, entity_id: object) -> object:
        del model, entity_id
        return FailingAssignmentModel()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_version_translates_database_failure() -> None:
    repository = SqlPaperRepository(cast(AsyncSession, FailingGetSession()))

    with pytest.raises(PaperPersistenceError) as error:
        await repository.get_version(new_id())

    assert not isinstance(error.value, SQLAlchemyError)
    assert "private_table" not in str(error.value)
    assert "driver" not in str(error.value).lower()
    assert "secret" not in str(error.value).lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_paper_task_read_and_rollback_translate_database_failures() -> None:
    repository = SqlPaperRepository(cast(AsyncSession, FailingGetSession()))

    with pytest.raises(PaperPersistenceError) as read_error:
        await repository.get_task(new_id())
    with pytest.raises(PaperPersistenceError) as rollback_error:
        await repository.rollback()

    assert "private_table" not in str(read_error.value)
    assert "driver" not in str(read_error.value).lower()
    assert "secret" not in str(rollback_error.value).lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_task_repository_translates_all_database_failures() -> None:
    repository = SqlTaskRepository(cast(AsyncSession, FailingGetSession()))

    with pytest.raises(TaskPersistenceError) as read_error:
        await repository.get_task(new_id())
    with pytest.raises(TaskPersistenceError):
        await repository.get_task_for_version(new_id())
    with pytest.raises(TaskPersistenceError):
        await repository.update_task(
            new_id(),
            stage=TaskStage.PARSING,
            status=TaskStatus.FAILED,
            progress_current=2,
            progress_total=len(TaskStage),
        )
    with pytest.raises(TaskPersistenceError) as commit_error:
        await repository.commit()

    assert "private_table" not in str(read_error.value)
    assert "driver" not in str(read_error.value).lower()
    assert "secret" not in str(commit_error.value).lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_task_update_translates_orm_assignment_failure() -> None:
    repository = SqlTaskRepository(
        cast(AsyncSession, FailingAssignmentSession())
    )

    with pytest.raises(TaskPersistenceError) as error:
        await repository.update_task(
            new_id(),
            stage=TaskStage.PARSING,
            status=TaskStatus.FAILED,
            progress_current=2,
            progress_total=len(TaskStage),
        )

    assert "private_task" not in str(error.value)
    assert "secret" not in str(error.value).lower()
