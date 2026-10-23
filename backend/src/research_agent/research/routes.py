from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.chat.domain import ChatSessionRecord
from research_agent.chat.routes import ChatSessionResponse, ChatSessionSummaryResponse
from research_agent.papers.routes import get_db_session
from research_agent.research.domain import ResearchSpace, SpaceInput, SpaceUpdate
from research_agent.research.repository import SqlResearchRepository
from research_agent.shared.errors import AppError

router = APIRouter(prefix="/research/spaces", tags=["research"])


async def get_research_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AsyncIterator[SqlResearchRepository]:
    try:
        yield SqlResearchRepository(session)
    except SQLAlchemyError:
        await session.rollback()
        raise AppError(
            "RESEARCH_PERSISTENCE_FAILED",
            "Research state could not be persisted",
            retryable=True,
        ) from None


Repository = Annotated[SqlResearchRepository, Depends(get_research_repository)]


class SpaceSessionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(min_length=1, max_length=500)


def session_response(record: ChatSessionRecord) -> ChatSessionResponse:
    return ChatSessionResponse(
        id=record.id,
        title=record.title,
        document_ids=list(record.scope.document_ids),
        research_space_id=record.research_space_id,
    )


@router.get("")
async def list_spaces(
    repository: Repository, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> dict[str, list[ResearchSpace]]:
    return {"items": await repository.list_spaces(limit=limit)}


@router.post("", status_code=201, response_model=ResearchSpace)
async def create_space(body: SpaceInput, repository: Repository) -> ResearchSpace:
    return await repository.save(body)


@router.get("/{space_id}", response_model=ResearchSpace)
async def get_space(space_id: UUID, repository: Repository) -> ResearchSpace:
    return await repository.get(space_id)


@router.put("/{space_id}", response_model=ResearchSpace)
async def update_space(
    space_id: UUID, body: SpaceUpdate, repository: Repository
) -> ResearchSpace:
    return await repository.save(body, space_id=space_id)


@router.get("/{space_id}/sessions")
async def list_space_sessions(
    space_id: UUID, repository: Repository
) -> dict[str, list[ChatSessionSummaryResponse]]:
    records = await repository.sessions(space_id)
    return {
        "items": [
            ChatSessionSummaryResponse(
                **session_response(record).model_dump(),
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            for record in records
        ]
    }


@router.post(
    "/{space_id}/sessions", status_code=201, response_model=ChatSessionResponse
)
async def create_space_session(
    space_id: UUID, body: SpaceSessionInput, repository: Repository
) -> ChatSessionResponse:
    return session_response(await repository.create_session(space_id, title=body.title))


@router.post("/{space_id}/sessions/{session_id}", response_model=ChatSessionResponse)
async def assign_space_session(
    space_id: UUID, session_id: UUID, repository: Repository
) -> ChatSessionResponse:
    return session_response(await repository.assign_session(space_id, session_id))
