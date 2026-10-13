from typing import TYPE_CHECKING, Protocol, runtime_checkable

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.papers.models import DocumentTaskModel, OutboxEventModel
from research_agent.shared.ids import EntityId, new_id
from research_agent.tasks.domain import TaskRecord, TaskStage, TaskStatus


class TaskRecordNotFoundError(LookupError):
    pass


class TaskPersistenceError(Exception):
    def __init__(self) -> None:
        super().__init__("task persistence failed")


class TaskNotRetryableError(Exception):
    pass


@runtime_checkable
class TaskRepository(Protocol):
    async def get_task(self, task_id: EntityId) -> TaskRecord | None: ...

    async def get_task_for_version(self, version_id: EntityId) -> TaskRecord | None: ...

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

    async def retry_task(
        self, task_id: EntityId, *, retryable_error_codes: frozenset[str]
    ) -> TaskRecord | None: ...

    async def rollback(self) -> None: ...


class SqlTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_task(self, task_id: EntityId) -> TaskRecord | None:
        try:
            model = await self._session.get(DocumentTaskModel, task_id)
        except SQLAlchemyError as error:
            raise TaskPersistenceError() from error
        if model is None:
            return None
        return _task_record(model)

    async def get_task_for_version(self, version_id: EntityId) -> TaskRecord | None:
        try:
            statement = (
                select(DocumentTaskModel)
                .where(DocumentTaskModel.document_version_id == version_id)
                .order_by(
                    DocumentTaskModel.created_at.desc(), DocumentTaskModel.id.desc()
                )
                .limit(1)
            )
            model = (await self._session.execute(statement)).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise TaskPersistenceError() from error
        return None if model is None else _task_record(model)

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
        try:
            model = await self._session.get(DocumentTaskModel, task_id)
            if model is None:
                raise TaskRecordNotFoundError("task does not exist")
            model.stage = stage
            model.status = status
            model.progress_current = progress_current
            model.progress_total = progress_total
            model.error_code = error_code
            model.error_message = error_message
        except SQLAlchemyError as error:
            raise TaskPersistenceError() from error

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except SQLAlchemyError as error:
            raise TaskPersistenceError() from error

    async def retry_task(
        self, task_id: EntityId, *, retryable_error_codes: frozenset[str]
    ) -> TaskRecord | None:
        try:
            statement = (
                select(DocumentTaskModel)
                .where(DocumentTaskModel.id == task_id)
                .with_for_update()
            )
            model = (await self._session.scalars(statement)).one_or_none()
            if model is None:
                return None
            if (
                model.status != TaskStatus.FAILED
                or model.error_code not in retryable_error_codes
            ):
                raise TaskNotRetryableError()
            model.status = TaskStatus.QUEUED
            model.retry_count += 1
            self._session.add(
                OutboxEventModel(
                    id=new_id(),
                    event_type="document.retry_requested",
                    payload={
                        "task_name": "research_agent.ingestion.process_document",
                        "queue": "paper_ingestion",
                        "kwargs": {"version_id": str(model.document_version_id)},
                    },
                    attempts=0,
                    available_at=datetime.now(UTC),
                    sent_at=None,
                )
            )
            await self._session.commit()
            return _task_record(model)
        except TaskNotRetryableError:
            raise
        except SQLAlchemyError as error:
            raise TaskPersistenceError() from error

    async def rollback(self) -> None:
        try:
            await self._session.rollback()
        except SQLAlchemyError as error:
            raise TaskPersistenceError() from error


def _task_record(model: DocumentTaskModel) -> TaskRecord:
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


if TYPE_CHECKING:

    def _task_repository_conformance(repository: SqlTaskRepository) -> TaskRepository:
        return repository
