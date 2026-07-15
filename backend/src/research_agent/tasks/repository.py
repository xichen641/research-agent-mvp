from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.papers.models import DocumentTaskModel
from research_agent.shared.ids import EntityId
from research_agent.tasks.domain import TaskRecord, TaskStage, TaskStatus


class TaskRecordNotFoundError(LookupError):
    pass


@runtime_checkable
class TaskRepository(Protocol):
    async def get_task(self, task_id: EntityId) -> TaskRecord | None: ...

    async def update_task(
        self,
        task_id: EntityId,
        *,
        stage: TaskStage,
        status: TaskStatus,
        progress_current: int,
        progress_total: int,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None: ...

    async def commit(self) -> None: ...


class SqlTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_task(self, task_id: EntityId) -> TaskRecord | None:
        model = await self._session.get(DocumentTaskModel, task_id)
        if model is None:
            return None
        return TaskRecord(
            id=model.id,
            document_version_id=model.document_version_id,
            stage=TaskStage(model.stage),
            status=TaskStatus(model.status),
            progress_current=model.progress_current,
            progress_total=model.progress_total,
            retry_count=model.retry_count,
            error_code=model.error_code,
            error_message=model.error_message,
        )

    async def update_task(
        self,
        task_id: EntityId,
        *,
        stage: TaskStage,
        status: TaskStatus,
        progress_current: int,
        progress_total: int,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        model = await self._session.get(DocumentTaskModel, task_id)
        if model is None:
            raise TaskRecordNotFoundError(f"task {task_id} does not exist")
        model.stage = stage
        model.status = status
        model.progress_current = progress_current
        model.progress_total = progress_total
        model.error_code = error_code
        model.error_message = error_message

    async def commit(self) -> None:
        await self._session.commit()


if TYPE_CHECKING:
    def _task_repository_conformance(repository: SqlTaskRepository) -> TaskRepository:
        return repository
