from collections.abc import AsyncIterator
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.chat.domain import ChatSessionRecord
from research_agent.chat.repository import SqlChatRepository
from research_agent.chat.stream import ChatStream, PersistedChatStream
from research_agent.papers.routes import get_db_session
from research_agent.retrieval.domain import RetrievalScope
from research_agent.shared.errors import AppError


router = APIRouter(prefix="/chat")


class ChatCommands(Protocol):
    async def create_session(
        self, *, title: str, scope: RetrievalScope
    ) -> ChatSessionRecord: ...

    async def start(self, *, session_id: UUID, question: str) -> UUID: ...


class CreateChatSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    document_ids: list[UUID] = Field(default_factory=list, max_length=100)


class ChatSessionResponse(BaseModel):
    id: UUID
    title: str
    document_ids: list[UUID]


class CreateChatMessageRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20_000)


class ChatRunAcceptedResponse(BaseModel):
    run_id: UUID
    stream_url: str


def get_chat_stream_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ChatStream:
    return PersistedChatStream(SqlChatRepository(session))


def get_chat_commands(request: Request) -> ChatCommands:
    commands = getattr(request.app.state, "chat_commands", None)
    if commands is None:
        raise AppError(
            "CHAT_NOT_CONFIGURED",
            "Chat generation is not configured",
            retryable=True,
        )
    return commands


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    response_model=ChatSessionResponse,
)
async def create_chat_session(
    body: CreateChatSessionRequest,
    commands: Annotated[ChatCommands, Depends(get_chat_commands)],
) -> ChatSessionResponse:
    try:
        scope = RetrievalScope(document_ids=tuple(body.document_ids))
    except ValueError:
        raise AppError(
            "INVALID_CHAT_SCOPE", "Chat document scope contains duplicates"
        ) from None
    session = await commands.create_session(title=body.title, scope=scope)
    return ChatSessionResponse(
        id=session.id,
        title=session.title,
        document_ids=list(session.scope.document_ids),
    )


@router.post(
    "/sessions/{session_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ChatRunAcceptedResponse,
)
async def create_chat_message(
    session_id: UUID,
    body: CreateChatMessageRequest,
    request: Request,
    commands: Annotated[ChatCommands, Depends(get_chat_commands)],
    stream: Annotated[ChatStream, Depends(get_chat_stream_service)],
) -> ChatRunAcceptedResponse | StreamingResponse:
    run_id = await commands.start(session_id=session_id, question=body.question)
    stream_url = f"/api/v1/chat/runs/{run_id}/stream"
    if "text/event-stream" not in request.headers.get("accept", ""):
        return ChatRunAcceptedResponse(run_id=run_id, stream_url=stream_url)
    await stream.prepare(run_id)
    return _streaming_response(stream.iter_sse(run_id))


@router.get("/runs/{run_id}/stream")
async def stream_chat_run(
    run_id: UUID,
    service: Annotated[ChatStream, Depends(get_chat_stream_service)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    after_sequence = _last_event_sequence(last_event_id)
    await service.prepare(run_id)
    return _streaming_response(
        service.iter_sse(run_id, after_sequence=after_sequence)
    )


def _streaming_response(content: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        content,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "x-vercel-ai-ui-message-stream": "v1",
        },
    )


def _last_event_sequence(value: str | None) -> int:
    if value is None:
        return 0
    try:
        sequence = int(value)
    except ValueError:
        raise AppError(
            "INVALID_LAST_EVENT_ID", "Last-Event-ID must be a non-negative integer"
        ) from None
    if sequence < 0:
        raise AppError(
            "INVALID_LAST_EVENT_ID", "Last-Event-ID must be a non-negative integer"
        )
    return sequence
