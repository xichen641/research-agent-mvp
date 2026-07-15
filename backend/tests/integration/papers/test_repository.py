import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.papers.domain import DocumentStatus, VersionStatus
from research_agent.papers.repository import SqlPaperRepository
from research_agent.tasks.domain import TaskStage, TaskStatus


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
