import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.papers.domain import CreatedDocument, DocumentStatus
from research_agent.papers.models import (
    DocumentModel,
    DocumentTaskModel,
    OutboxEventModel,
)
from research_agent.papers.repository import SqlPaperRepository
from research_agent.tasks.domain import TaskStage, TaskStatus
from research_agent.tasks.repository import SqlTaskRepository, TaskRecordNotFoundError
from research_agent.tasks.service import RETRYABLE_ERROR_CODES

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def seed_failed_paper(session: AsyncSession) -> CreatedDocument:
    created = await SqlPaperRepository(session).create_document_with_version(
        title="Deletable paper",
        sha256=uuid4().hex * 2,
        filename="paper.pdf",
        source_object_key="tests/deleted-paper.pdf",
        parser_name="test",
        parser_version="1",
        pipeline_version="1",
    )
    await SqlTaskRepository(session).update_task(
        created.task_id,
        stage=TaskStage.PARSING,
        status=TaskStatus.FAILED,
        progress_current=2,
        progress_total=8,
        error_code="PARSE_PARSER_FAILED",
        error_message="Parser unavailable",
    )
    document = await session.get(DocumentModel, created.document_id)
    assert document is not None
    document.status = DocumentStatus.FAILED
    await session.commit()
    return created


async def attempt_mutation(
    repository: SqlTaskRepository, created: CreatedDocument, mutation: str
) -> None:
    if mutation == "retry":
        assert (
            await repository.retry_task(
                created.task_id, retryable_error_codes=RETRYABLE_ERROR_CODES
            )
            is None
        )
    else:
        with pytest.raises(TaskRecordNotFoundError):
            await repository.update_task(
                created.task_id,
                stage=TaskStage.PARSING,
                status=TaskStatus.RUNNING,
                progress_current=2,
                progress_total=8,
            )


async def test_deleted_document_hides_task_reads_and_preserves_terminal_state(
    db_session: AsyncSession,
) -> None:
    created = await seed_failed_paper(db_session)
    repository = SqlTaskRepository(db_session)
    assert await repository.get_task(created.task_id) is not None
    assert await repository.get_task_for_version(created.version_id) is not None
    document = await db_session.get(DocumentModel, created.document_id)
    assert document is not None
    document.status = DocumentStatus.DELETED
    await db_session.commit()

    assert await repository.get_task(created.task_id) is None
    assert await repository.get_task_for_version(created.version_id) is None
    for mutation in ("retry", "worker-update"):
        await attempt_mutation(repository, created, mutation)
    task = await db_session.get(DocumentTaskModel, created.task_id)
    assert task is not None
    assert task.status == TaskStatus.FAILED
    assert task.retry_count == 0
    assert task.error_code == "PARSE_PARSER_FAILED"
    assert task.progress_current == 2
    assert (
        await db_session.scalar(select(func.count()).select_from(OutboxEventModel)) == 0
    )


@pytest.mark.parametrize("mutation", ["retry", "worker-update"])
async def test_task_mutation_waits_for_deletion_and_refreshes_stale_document(
    independent_session_factory: async_sessionmaker[AsyncSession],
    mutation: str,
) -> None:
    async with independent_session_factory() as seed:
        created = await seed_failed_paper(seed)

    async with (
        independent_session_factory() as deletion,
        independent_session_factory() as stale_worker,
    ):
        stale_document = await stale_worker.get(DocumentModel, created.document_id)
        stale_task = await stale_worker.get(DocumentTaskModel, created.task_id)
        assert stale_document is not None and stale_task is not None
        assert stale_document.status == DocumentStatus.FAILED
        await stale_worker.commit()
        document = await deletion.scalar(
            select(DocumentModel)
            .where(DocumentModel.id == created.document_id)
            .with_for_update()
        )
        assert document is not None
        document.status = DocumentStatus.DELETED
        await deletion.flush()

        pending = asyncio.create_task(
            attempt_mutation(SqlTaskRepository(stale_worker), created, mutation)
        )
        try:
            await asyncio.sleep(0.05)
            assert not pending.done()
            await deletion.commit()
            await asyncio.wait_for(pending, timeout=5)
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        assert stale_document.status == DocumentStatus.DELETED
        await stale_worker.rollback()

    async with independent_session_factory() as verification:
        document = await verification.get(DocumentModel, created.document_id)
        task = await verification.get(DocumentTaskModel, created.task_id)
        assert document is not None and document.status == DocumentStatus.DELETED
        assert task is not None and task.status == TaskStatus.FAILED
        assert task.retry_count == 0
        assert (
            await verification.scalar(
                select(func.count()).select_from(OutboxEventModel)
            )
            == 0
        )
