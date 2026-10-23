from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.chat.diagnostics import (
    RunDiagnosticsReader,
    RuntimeConfiguration,
    read_run_diagnostics,
    runtime_configuration,
)
from research_agent.chat.domain import (
    ChatMessageRecord,
    ChatRole,
    ChatRunRecord,
    ChatRunStatus,
    ChatSessionRecord,
    RetrievalMode,
    VerifiedAnswer,
)
from research_agent.chat.repository import SqlChatRepository
from research_agent.chat.stream import ChatStream, PersistedChatStream, answer_payload
from research_agent.config import get_settings
from research_agent.papers.routes import get_db_session
from research_agent.retrieval.domain import RetrievalScope
from research_agent.shared.errors import AppError

router = APIRouter(prefix="/chat")


class ChatCommands(Protocol):
    async def create_session(
        self, *, title: str, scope: RetrievalScope
    ) -> ChatSessionRecord: ...

    async def start(
        self,
        *,
        session_id: UUID,
        question: str,
        retrieval_mode: RetrievalMode = "auto",
    ) -> UUID: ...


class ChatHistory(Protocol):
    async def get_session(self, session_id: UUID) -> ChatSessionRecord | None: ...

    async def list_sessions(self, *, limit: int = 100) -> list[ChatSessionRecord]: ...

    async def list_messages(self, session_id: UUID) -> list[ChatMessageRecord]: ...

    async def get_latest_run(self, session_id: UUID) -> ChatRunRecord | None: ...


class CreateChatSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    document_ids: list[UUID] = Field(default_factory=list, max_length=100)


class ChatSessionResponse(BaseModel):
    id: UUID
    title: str
    document_ids: list[UUID]
    research_space_id: UUID | None = None


class CreateChatMessageRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20_000)
    retrieval_mode: RetrievalMode = "auto"


class ChatRunAcceptedResponse(BaseModel):
    run_id: UUID
    stream_url: str


class ChatHistoryMessageResponse(BaseModel):
    id: UUID
    role: ChatRole
    content: dict[str, object]
    created_at: datetime


class ChatHistoryResponse(BaseModel):
    items: list[ChatHistoryMessageResponse]


class ChatSessionSummaryResponse(ChatSessionResponse):
    created_at: datetime
    updated_at: datetime


class ChatSessionListResponse(BaseModel):
    items: list[ChatSessionSummaryResponse]


class ChatSessionActivityResponse(BaseModel):
    session_id: UUID
    run_id: UUID | None = None
    status: ChatRunStatus | Literal["IDLE"]
    error_code: str | None = None
    stream_url: str | None = None


def get_chat_stream_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ChatStream:
    return PersistedChatStream(SqlChatRepository(session))


def get_chat_history(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ChatHistory:
    return SqlChatRepository(session)


def get_chat_diagnostics(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RunDiagnosticsReader:
    return SqlChatRepository(session)


@router.get("/evaluation/config", response_model=RuntimeConfiguration)
async def get_evaluation_configuration() -> RuntimeConfiguration:
    return runtime_configuration(get_settings())


@router.get("/runs/{run_id}/diagnostics")
async def get_run_diagnostics(
    run_id: UUID,
    reader: Annotated[RunDiagnosticsReader, Depends(get_chat_diagnostics)],
) -> dict[str, object]:
    return await read_run_diagnostics(reader, run_id)


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


@router.get("/sessions", response_model=ChatSessionListResponse)
async def list_chat_sessions(
    history: Annotated[ChatHistory, Depends(get_chat_history)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ChatSessionListResponse:
    sessions = await history.list_sessions(limit=limit)
    return ChatSessionListResponse(
        items=[
            ChatSessionSummaryResponse(
                id=session.id,
                title=session.title,
                document_ids=list(session.scope.document_ids),
                research_space_id=session.research_space_id,
                created_at=session.created_at,
                updated_at=session.updated_at,
            )
            for session in sessions
        ]
    )


@router.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_chat_session(
    session_id: UUID, history: Annotated[ChatHistory, Depends(get_chat_history)]
) -> ChatSessionResponse:
    record = await history.get_session(session_id)
    if record is None:
        raise AppError("CHAT_SESSION_NOT_FOUND", "Chat session was not found")
    return ChatSessionResponse(
        id=record.id,
        title=record.title,
        document_ids=list(record.scope.document_ids),
        research_space_id=record.research_space_id,
    )


@router.get(
    "/sessions/{session_id}/messages",
    response_model=ChatHistoryResponse,
)
async def list_chat_messages(
    session_id: UUID,
    history: Annotated[ChatHistory, Depends(get_chat_history)],
) -> ChatHistoryResponse:
    if await history.get_session(session_id) is None:
        raise AppError("CHAT_SESSION_NOT_FOUND", "Chat session was not found")
    records = await history.list_messages(session_id)
    return ChatHistoryResponse(items=[_history_message(item) for item in records])


@router.get(
    "/sessions/{session_id}/activity",
    response_model=ChatSessionActivityResponse,
)
async def get_chat_session_activity(
    session_id: UUID,
    history: Annotated[ChatHistory, Depends(get_chat_history)],
) -> ChatSessionActivityResponse:
    if await history.get_session(session_id) is None:
        raise AppError("CHAT_SESSION_NOT_FOUND", "Chat session was not found")
    run = await history.get_latest_run(session_id)
    if run is None:
        return ChatSessionActivityResponse(session_id=session_id, status="IDLE")
    return ChatSessionActivityResponse(
        session_id=session_id,
        run_id=run.id,
        status=run.status,
        error_code=run.error_code,
        stream_url=f"/api/v1/chat/runs/{run.id}/stream",
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
    run_id = await commands.start(
        session_id=session_id,
        question=body.question,
        retrieval_mode=body.retrieval_mode,
    )
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
    return _streaming_response(service.iter_sse(run_id, after_sequence=after_sequence))


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


def _history_message(record: ChatMessageRecord) -> ChatHistoryMessageResponse:
    if record.role == "user":
        text = record.content.get("text")
        if not isinstance(text, str):
            raise AppError("CHAT_HISTORY_INVALID", "Stored chat history is invalid")
        content: dict[str, object] = {"text": text}
    else:
        try:
            answer = VerifiedAnswer.model_validate(record.content)
        except ValueError:
            raise AppError(
                "CHAT_HISTORY_INVALID", "Stored chat history is invalid"
            ) from None
        content = answer_payload(answer)
    return ChatHistoryMessageResponse(
        id=record.id,
        role=record.role,
        content=content,
        created_at=record.created_at,
    )
