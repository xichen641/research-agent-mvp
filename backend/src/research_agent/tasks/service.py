from research_agent.shared.errors import AppError
from research_agent.shared.ids import EntityId
from research_agent.tasks.domain import TaskRecord, TaskStatus
from research_agent.tasks.repository import (
    TaskNotRetryableError,
    TaskPersistenceError,
    TaskRepository,
)


RETRYABLE_ERROR_CODES = frozenset(
    {
        "VALIDATION_FAILED",
        "NORMALIZATION_FAILED",
        "CHUNK_PERSISTENCE_FAILED",
        "EMBEDDING_PERSISTENCE_FAILED",
        "EMBEDDING_PROVIDER_FAILED",
        "EMBEDDING_VERIFICATION_FAILED",
        "PARSE_DOWNLOAD_FAILED",
        "PARSE_PARSER_FAILED",
        "PARSE_UPLOAD_FAILED",
        "PARSE_PERSISTENCE_FAILED",
        "PARSE_FAILURE_RECOVERY_FAILED",
        "INGESTION_FAILURE_RECOVERY_FAILED",
    }
)


def is_retryable(task: TaskRecord) -> bool:
    return (
        task.status is TaskStatus.FAILED
        and task.error_code in RETRYABLE_ERROR_CODES
    )


class TaskService:
    def __init__(self, repository: TaskRepository) -> None:
        self._repository = repository

    async def get_task(self, task_id: EntityId) -> TaskRecord:
        try:
            task = await self._repository.get_task(task_id)
        except TaskPersistenceError:
            raise AppError(
                code="TASK_PERSISTENCE_ERROR",
                message="Task state could not be loaded",
                retryable=True,
            ) from None
        if task is None:
            raise AppError(code="TASK_NOT_FOUND", message="Task was not found")
        return task

    async def retry_task(self, task_id: EntityId) -> TaskRecord:
        try:
            task = await self._repository.retry_task(
                task_id, retryable_error_codes=RETRYABLE_ERROR_CODES
            )
        except TaskNotRetryableError:
            raise AppError(
                code="TASK_NOT_RETRYABLE",
                message="Task is not eligible for retry",
            ) from None
        except TaskPersistenceError:
            await self._rollback_safely()
            raise AppError(
                code="TASK_PERSISTENCE_ERROR",
                message="Task retry could not be persisted",
                retryable=True,
            ) from None
        if task is None:
            raise AppError(code="TASK_NOT_FOUND", message="Task was not found")
        return task

    async def _rollback_safely(self) -> None:
        try:
            await self._repository.rollback()
        except TaskPersistenceError:
            return
