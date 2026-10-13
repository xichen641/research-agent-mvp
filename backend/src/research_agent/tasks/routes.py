from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.papers.routes import get_db_session
from research_agent.shared.ids import EntityId
from research_agent.tasks.domain import TaskRecord
from research_agent.tasks.repository import SqlTaskRepository
from research_agent.tasks.service import TaskService, is_retryable


router = APIRouter()


class TaskResponse(BaseModel):
    id: EntityId
    document_version_id: EntityId
    stage: str
    status: str
    progress_current: int
    progress_total: int
    retry_count: int
    error_code: str | None
    error_message: str | None
    retryable: bool


def get_task_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TaskService:
    return TaskService(SqlTaskRepository(session))


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: EntityId,
    service: Annotated[TaskService, Depends(get_task_service)],
) -> TaskResponse:
    return _response(await service.get_task(task_id))


@router.post("/tasks/{task_id}/retry", status_code=202, response_model=TaskResponse)
async def retry_task(
    task_id: EntityId,
    service: Annotated[TaskService, Depends(get_task_service)],
) -> TaskResponse:
    return _response(await service.retry_task(task_id))


def _response(task: TaskRecord) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        document_version_id=task.document_version_id,
        stage=task.stage,
        status=task.status,
        progress_current=task.progress_current,
        progress_total=task.progress_total,
        retry_count=task.retry_count,
        error_code=task.error_code,
        error_message=task.error_message,
        retryable=is_retryable(task),
    )
