from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.papers.domain import VersionStatus
from research_agent.papers.models import DocumentTaskModel, DocumentVersionModel
from research_agent.papers.repository import SqlPaperRepository
from research_agent.tasks.domain import TaskStage, TaskStatus
from research_agent.tasks.repository import SqlTaskRepository


def upgrade_ready_task_progress(connection: Connection) -> None:
    config = Config(str(Path(__file__).parents[3] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("0013_ready_task_progress")
    assert revision is not None
    with Operations.context(MigrationContext.configure(connection)):
        revision.module.upgrade()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "version_status",
        "task_status",
        "stage",
        "current",
        "total",
        "expected_stage",
        "expected_current",
    ),
    [
        pytest.param(
            VersionStatus.READY,
            TaskStatus.READY,
            TaskStage.EMBEDDING,
            6,
            8,
            TaskStage.VERIFYING,
            8,
            id="legacy-completed-task",
        ),
        pytest.param(
            VersionStatus.READY,
            TaskStatus.READY,
            TaskStage.VERIFYING,
            8,
            8,
            TaskStage.VERIFYING,
            8,
            id="already-correct-completed-task",
        ),
        pytest.param(
            VersionStatus.READY,
            TaskStatus.FAILED,
            TaskStage.EMBEDDING,
            6,
            8,
            TaskStage.EMBEDDING,
            6,
            id="failed-task",
        ),
        pytest.param(
            VersionStatus.READY,
            TaskStatus.RUNNING,
            TaskStage.EMBEDDING,
            6,
            8,
            TaskStage.EMBEDDING,
            6,
            id="running-task",
        ),
        pytest.param(
            VersionStatus.FAILED,
            TaskStatus.READY,
            TaskStage.EMBEDDING,
            6,
            8,
            TaskStage.EMBEDDING,
            6,
            id="version-not-ready",
        ),
        pytest.param(
            VersionStatus.READY,
            TaskStatus.READY,
            TaskStage.EMBEDDING,
            5,
            8,
            TaskStage.EMBEDDING,
            5,
            id="different-progress-current",
        ),
        pytest.param(
            VersionStatus.READY,
            TaskStatus.READY,
            TaskStage.EMBEDDING,
            6,
            10,
            TaskStage.EMBEDDING,
            6,
            id="different-progress-total",
        ),
        pytest.param(
            VersionStatus.READY,
            TaskStatus.READY,
            TaskStage.INDEXING,
            6,
            8,
            TaskStage.INDEXING,
            6,
            id="different-stage",
        ),
    ],
)
async def test_ready_progress_migration_repairs_only_legacy_successes_and_is_idempotent(
    db_session: AsyncSession,
    version_status: VersionStatus,
    task_status: TaskStatus,
    stage: TaskStage,
    current: int,
    total: int,
    expected_stage: TaskStage,
    expected_current: int,
) -> None:
    papers = SqlPaperRepository(db_session)
    created = await papers.create_document_with_version(
        title="Progress migration paper",
        sha256="f" * 64,
        filename="paper.pdf",
        source_object_key="papers/progress-migration.pdf",
        parser_name="test",
        parser_version="1",
        pipeline_version="pipeline-v1",
    )
    await SqlTaskRepository(db_session).update_task(
        created.task_id,
        stage=stage,
        status=task_status,
        progress_current=current,
        progress_total=total,
        error_code="EMBEDDING_PROVIDER_FAILED"
        if task_status is TaskStatus.FAILED
        else None,
        error_message="Provider unavailable"
        if task_status is TaskStatus.FAILED
        else None,
    )
    version = await db_session.get(DocumentVersionModel, created.version_id)
    task = await db_session.get(DocumentTaskModel, created.task_id)
    assert version is not None and task is not None
    version.status = version_status
    task.retry_count = 2
    errors = (task.error_code, task.error_message)
    await db_session.flush()

    connection = await db_session.connection()
    for _ in range(2):
        await connection.run_sync(upgrade_ready_task_progress)
        await db_session.refresh(task)
        await db_session.refresh(version)

        assert task.status == task_status
        assert task.stage == expected_stage
        assert task.progress_current == expected_current
        assert task.progress_total == total
        assert task.document_version_id == created.version_id
        assert task.retry_count == 2
        assert (task.error_code, task.error_message) == errors
        assert version.status == version_status
        assert version.source_object_key == "papers/progress-migration.pdf"
