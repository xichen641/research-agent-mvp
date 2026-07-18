from typing import Protocol, cast, runtime_checkable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.chat.domain import (
    ChatMessageRecord,
    ChatRole,
    ChatRunEventRecord,
    ChatRunRecord,
    ChatRunStatus,
    ChatSessionRecord,
    RetrievalScope,
    VerifiedAnswer,
)
from research_agent.chat.models import (
    ChatMessageModel,
    ChatRunEventModel,
    ChatRunModel,
    ChatSessionModel,
)
from research_agent.shared.errors import AppError
from research_agent.shared.ids import new_id


class ChatSessionNotFoundError(LookupError):
    pass


class ChatPersistenceError(AppError):
    def __init__(self) -> None:
        super().__init__(
            "CHAT_PERSISTENCE_FAILED",
            "Chat state could not be persisted",
            retryable=True,
        )


@runtime_checkable
class ChatRepository(Protocol):
    async def create_session(
        self, *, title: str, scope: RetrievalScope
    ) -> ChatSessionRecord: ...

    async def get_session(self, session_id: UUID) -> ChatSessionRecord | None: ...

    async def start_run(self, *, session_id: UUID, question: str) -> ChatRunRecord: ...

    async def record_event(
        self, *, run_id: UUID, event_type: str, payload: dict[str, object]
    ) -> ChatRunEventRecord: ...

    async def complete_run(
        self, *, run_id: UUID, answer: VerifiedAnswer
    ) -> ChatMessageRecord: ...

    async def fail_run(self, *, run_id: UUID, error_code: str) -> None: ...

    async def list_messages(self, session_id: UUID) -> list[ChatMessageRecord]: ...

    async def get_run(self, run_id: UUID) -> ChatRunRecord | None: ...

    async def list_events(self, run_id: UUID) -> list[ChatRunEventRecord]: ...


class SqlChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_session(
        self, *, title: str, scope: RetrievalScope
    ) -> ChatSessionRecord:
        if not title.strip():
            raise ValueError("chat title must not be blank")
        model = ChatSessionModel(
            id=new_id(),
            title=title.strip(),
            scope={"document_ids": [str(item) for item in scope.document_ids]},
        )
        try:
            self._session.add(model)
            await self._session.commit()
            await self._session.refresh(model)
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return _session_record(model)

    async def get_session(self, session_id: UUID) -> ChatSessionRecord | None:
        try:
            model = await self._session.get(ChatSessionModel, session_id)
        except SQLAlchemyError:
            raise ChatPersistenceError() from None
        return None if model is None else _session_record(model)

    async def start_run(self, *, session_id: UUID, question: str) -> ChatRunRecord:
        if not question.strip():
            raise ValueError("question must not be blank")
        run = ChatRunModel(
            id=new_id(),
            session_id=session_id,
            status="RUNNING",
            error_code=None,
            completed_at=None,
        )
        message = ChatMessageModel(
            id=new_id(),
            session_id=session_id,
            role="user",
            content={"text": question.strip()},
        )
        event = ChatRunEventModel(
            run_id=run.id,
            sequence=1,
            event_type="run_started",
            payload={},
        )
        try:
            if await self._session.get(ChatSessionModel, session_id) is None:
                raise ChatSessionNotFoundError(f"chat session {session_id} does not exist")
            self._session.add_all([run, message])
            # Flush the parent row before the event because no ORM relationship is
            # needed solely to communicate the database foreign-key dependency.
            # 先刷新父记录再写事件；无需仅为表达数据库外键而引入 ORM 关系。
            await self._session.flush()
            self._session.add(event)
            await self._session.commit()
            await self._session.refresh(run)
        except ChatSessionNotFoundError:
            await self._session.rollback()
            raise
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return _run_record(run)

    async def record_event(
        self, *, run_id: UUID, event_type: str, payload: dict[str, object]
    ) -> ChatRunEventRecord:
        if not event_type.strip():
            raise ValueError("event type must not be blank")
        try:
            await self._lock_run(run_id)
            sequence = await self._next_event_sequence(run_id)
            event = ChatRunEventModel(
                run_id=run_id,
                sequence=sequence,
                event_type=event_type,
                payload=payload,
            )
            self._session.add(event)
            await self._session.commit()
            await self._session.refresh(event)
        except ChatSessionNotFoundError:
            await self._session.rollback()
            raise
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return _event_record(event)

    async def complete_run(
        self, *, run_id: UUID, answer: VerifiedAnswer
    ) -> ChatMessageRecord:
        try:
            run = await self._lock_run(run_id)
            if run.status != "RUNNING":
                raise AppError("CHAT_RUN_FINALIZED", "Chat run is already finalized")
            sequence = await self._next_event_sequence(run_id)
            message = ChatMessageModel(
                id=new_id(),
                session_id=run.session_id,
                role="assistant",
                content=answer.model_dump(mode="json"),
            )
            run.status = "SUCCEEDED"
            run.error_code = None
            run.completed_at = func.now()
            event = ChatRunEventModel(
                run_id=run_id,
                sequence=sequence,
                event_type="run_completed",
                payload={"message_id": str(message.id)},
            )
            self._session.add_all([message, event])
            await self._session.commit()
            await self._session.refresh(message)
        except (ChatSessionNotFoundError, AppError):
            await self._session.rollback()
            raise
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return _message_record(message)

    async def fail_run(self, *, run_id: UUID, error_code: str) -> None:
        try:
            run = await self._lock_run(run_id)
            if run.status != "RUNNING":
                await self._session.rollback()
                return
            sequence = await self._next_event_sequence(run_id)
            run.status = "FAILED"
            run.error_code = error_code
            run.completed_at = func.now()
            self._session.add(
                ChatRunEventModel(
                    run_id=run_id,
                    sequence=sequence,
                    event_type="run_failed",
                    payload={"error_code": error_code},
                )
            )
            await self._session.commit()
        except ChatSessionNotFoundError:
            await self._session.rollback()
            raise
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None

    async def list_messages(self, session_id: UUID) -> list[ChatMessageRecord]:
        statement = (
            select(ChatMessageModel)
            .where(ChatMessageModel.session_id == session_id)
            .order_by(ChatMessageModel.created_at, ChatMessageModel.id)
        )
        try:
            models = (await self._session.scalars(statement)).all()
        except SQLAlchemyError:
            raise ChatPersistenceError() from None
        return [_message_record(model) for model in models]

    async def get_run(self, run_id: UUID) -> ChatRunRecord | None:
        try:
            model = await self._session.get(ChatRunModel, run_id)
        except SQLAlchemyError:
            raise ChatPersistenceError() from None
        return None if model is None else _run_record(model)

    async def list_events(self, run_id: UUID) -> list[ChatRunEventRecord]:
        statement = (
            select(ChatRunEventModel)
            .where(ChatRunEventModel.run_id == run_id)
            .order_by(ChatRunEventModel.sequence)
        )
        try:
            models = (await self._session.scalars(statement)).all()
        except SQLAlchemyError:
            raise ChatPersistenceError() from None
        return [_event_record(model) for model in models]

    async def _lock_run(self, run_id: UUID) -> ChatRunModel:
        statement = select(ChatRunModel).where(ChatRunModel.id == run_id).with_for_update()
        run = (await self._session.scalars(statement)).one_or_none()
        if run is None:
            raise ChatSessionNotFoundError(f"chat run {run_id} does not exist")
        return run

    async def _next_event_sequence(self, run_id: UUID) -> int:
        statement = select(func.coalesce(func.max(ChatRunEventModel.sequence), 0)).where(
            ChatRunEventModel.run_id == run_id
        )
        return int(await self._session.scalar(statement) or 0) + 1

    async def _rollback_after_error(self) -> None:
        try:
            await self._session.rollback()
        except SQLAlchemyError:
            pass


def _scope(value: dict[str, object]) -> RetrievalScope:
    raw_ids = value.get("document_ids", [])
    if not isinstance(raw_ids, list):
        raise ChatPersistenceError()
    return RetrievalScope(tuple(UUID(str(item)) for item in raw_ids))


def _session_record(model: ChatSessionModel) -> ChatSessionRecord:
    return ChatSessionRecord(
        id=model.id,
        title=model.title,
        scope=_scope(model.scope),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _message_record(model: ChatMessageModel) -> ChatMessageRecord:
    return ChatMessageRecord(
        id=model.id,
        session_id=model.session_id,
        role=cast(ChatRole, model.role),
        content=model.content,
        created_at=model.created_at,
    )


def _run_record(model: ChatRunModel) -> ChatRunRecord:
    return ChatRunRecord(
        id=model.id,
        session_id=model.session_id,
        status=cast(ChatRunStatus, model.status),
        error_code=model.error_code,
        created_at=model.created_at,
        completed_at=model.completed_at,
    )


def _event_record(model: ChatRunEventModel) -> ChatRunEventRecord:
    return ChatRunEventRecord(
        run_id=model.run_id,
        sequence=model.sequence,
        event_type=model.event_type,
        payload=model.payload,
        created_at=model.created_at,
    )
