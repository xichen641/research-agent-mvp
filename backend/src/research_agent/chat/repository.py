from datetime import UTC, datetime, timedelta
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
    ConversationMemoryRecord,
    RetrievalMode,
    RetrievalScope,
    VerifiedAnswer,
)
from research_agent.chat.models import (
    ChatMemoryModel,
    ChatMessageModel,
    ChatRunEventModel,
    ChatRunModel,
    ChatSessionModel,
)
from research_agent.papers.models import OutboxEventModel
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

    async def list_sessions(self, *, limit: int = 100) -> list[ChatSessionRecord]: ...

    async def start_run(
        self,
        *,
        session_id: UUID,
        question: str,
        scope: RetrievalScope | None = None,
        retrieval_mode: RetrievalMode = "auto",
        dispatch: bool = False,
    ) -> ChatRunRecord: ...

    async def claim_run(
        self, run_id: UUID, *, lease: timedelta
    ) -> ChatRunRecord | None: ...

    async def record_event(
        self, *, run_id: UUID, event_type: str, payload: dict[str, object]
    ) -> ChatRunEventRecord: ...

    async def complete_run(
        self, *, run_id: UUID, answer: VerifiedAnswer
    ) -> ChatMessageRecord: ...

    async def fail_run(self, *, run_id: UUID, error_code: str) -> None: ...

    async def list_messages(self, session_id: UUID) -> list[ChatMessageRecord]: ...

    async def get_message(self, message_id: UUID) -> ChatMessageRecord | None: ...

    async def get_run(self, run_id: UUID) -> ChatRunRecord | None: ...

    async def get_latest_run(self, session_id: UUID) -> ChatRunRecord | None: ...

    async def list_events(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> list[ChatRunEventRecord]: ...

    async def append_events_if_absent(
        self,
        *,
        run_id: UUID,
        marker_event_type: str,
        events: list[tuple[str, dict[str, object]]],
    ) -> bool: ...

    async def get_memory(self, session_id: UUID) -> ConversationMemoryRecord | None: ...

    async def save_memory(
        self,
        *,
        session_id: UUID,
        summary: str,
        durable_facts: tuple[str, ...],
        covered_message_count: int,
        expected_revision: int,
    ) -> ConversationMemoryRecord | None: ...


class SqlChatRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        lease_duration: timedelta = timedelta(minutes=30),
    ) -> None:
        if lease_duration.total_seconds() <= 0:
            raise ValueError("chat run lease duration must be positive")
        self._session = session
        self._lease_duration = lease_duration

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
            record = None if model is None else _session_record(model)
            await self._session.commit()
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return record

    async def list_sessions(self, *, limit: int = 100) -> list[ChatSessionRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("chat session limit must be between 1 and 500")
        statement = (
            select(ChatSessionModel)
            .order_by(ChatSessionModel.updated_at.desc(), ChatSessionModel.id.desc())
            .limit(limit)
        )
        try:
            models = (await self._session.scalars(statement)).all()
            records = [_session_record(model) for model in models]
            await self._session.commit()
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return records

    async def start_run(
        self,
        *,
        session_id: UUID,
        question: str,
        scope: RetrievalScope | None = None,
        retrieval_mode: RetrievalMode = "auto",
        dispatch: bool = False,
    ) -> ChatRunRecord:
        if not question.strip():
            raise ValueError("question must not be blank")
        if retrieval_mode not in {"auto", "off", "on"}:
            raise ValueError("invalid retrieval mode")
        try:
            chat_session = (
                await self._session.scalars(
                    select(ChatSessionModel)
                    .where(ChatSessionModel.id == session_id)
                    .with_for_update()
                )
            ).one_or_none()
            if chat_session is None:
                raise ChatSessionNotFoundError(
                    f"chat session {session_id} does not exist"
                )
            active_run = await self._session.scalar(
                select(ChatRunModel.id)
                .where(
                    ChatRunModel.session_id == session_id,
                    ChatRunModel.status.in_(("QUEUED", "RUNNING")),
                )
                .limit(1)
            )
            if active_run is not None:
                raise AppError(
                    "CHAT_RUN_IN_PROGRESS",
                    "Wait for the current chat turn to finish before starting another",
                )
            history_message_count = int(
                await self._session.scalar(
                    select(func.count(ChatMessageModel.id)).where(
                        ChatMessageModel.session_id == session_id
                    )
                )
                or 0
            )
        except (ChatSessionNotFoundError, AppError):
            await self._session.rollback()
            raise
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        resolved_scope = scope or _scope(chat_session.scope)
        chat_session.updated_at = func.now()
        run = ChatRunModel(
            id=new_id(),
            session_id=session_id,
            status="QUEUED" if dispatch else "RUNNING",
            error_code=None,
            question=question.strip(),
            scope={"document_ids": [str(item) for item in resolved_scope.document_ids]},
            retrieval_mode=retrieval_mode,
            history_message_count=history_message_count,
            lease_expires_at=None,
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
            self._session.add_all([run, message])
            # Flush the parent row before the event because no ORM relationship is
            # needed solely to communicate the database foreign-key dependency.
            # 先刷新父记录再写事件；无需仅为表达数据库外键而引入 ORM 关系。
            await self._session.flush()
            self._session.add(event)
            if dispatch:
                self._session.add(
                    OutboxEventModel(
                        id=new_id(),
                        event_type="chat.run.requested",
                        payload={"run_id": str(run.id)},
                        attempts=0,
                        available_at=datetime.now(UTC),
                        sent_at=None,
                    )
                )
            await self._session.commit()
            await self._session.refresh(run)
        except (ChatSessionNotFoundError, AppError):
            await self._session.rollback()
            raise
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return _run_record(run)

    async def claim_run(
        self, run_id: UUID, *, lease: timedelta
    ) -> ChatRunRecord | None:
        if lease.total_seconds() <= 0:
            raise ValueError("chat run lease must be positive")
        now = datetime.now(UTC)
        try:
            run = await self._lock_run(run_id)
            claimable = run.status == "QUEUED" or (
                run.status == "RUNNING"
                and (run.lease_expires_at is None or run.lease_expires_at <= now)
            )
            if not claimable:
                await self._session.rollback()
                return None
            run.status = "RUNNING"
            run.lease_expires_at = now + lease
            await self._session.commit()
            await self._session.refresh(run)
        except ChatSessionNotFoundError:
            await self._session.rollback()
            return None
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
            run = await self._lock_run(run_id)
            sequence = await self._next_event_sequence(run_id)
            if run.status == "RUNNING":
                run.lease_expires_at = datetime.now(UTC) + self._lease_duration
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
            run.lease_expires_at = None
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
            run.lease_expires_at = None
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
            records = [_message_record(model) for model in models]
            await self._session.commit()
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return records

    async def get_message(self, message_id: UUID) -> ChatMessageRecord | None:
        try:
            model = await self._session.get(ChatMessageModel, message_id)
            record = None if model is None else _message_record(model)
            await self._session.commit()
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return record

    async def get_run(self, run_id: UUID) -> ChatRunRecord | None:
        try:
            model = await self._session.get(ChatRunModel, run_id)
            record = None if model is None else _run_record(model)
            await self._session.commit()
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return record

    async def get_latest_run(self, session_id: UUID) -> ChatRunRecord | None:
        statement = (
            select(ChatRunModel)
            .where(ChatRunModel.session_id == session_id)
            .order_by(ChatRunModel.created_at.desc(), ChatRunModel.id.desc())
            .limit(1)
        )
        try:
            model = (await self._session.scalars(statement)).one_or_none()
            record = None if model is None else _run_record(model)
            await self._session.commit()
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return record

    async def list_events(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> list[ChatRunEventRecord]:
        statement = (
            select(ChatRunEventModel)
            .where(
                ChatRunEventModel.run_id == run_id,
                ChatRunEventModel.sequence > after_sequence,
            )
            .order_by(ChatRunEventModel.sequence)
        )
        try:
            models = (await self._session.scalars(statement)).all()
            records = [_event_record(model) for model in models]
            await self._session.commit()
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return records

    async def append_events_if_absent(
        self,
        *,
        run_id: UUID,
        marker_event_type: str,
        events: list[tuple[str, dict[str, object]]],
    ) -> bool:
        if not marker_event_type.strip() or not events:
            raise ValueError("stream events and marker must not be empty")
        try:
            await self._lock_run(run_id)
            marker_exists = await self._session.scalar(
                select(ChatRunEventModel.sequence)
                .where(
                    ChatRunEventModel.run_id == run_id,
                    ChatRunEventModel.event_type == marker_event_type,
                )
                .limit(1)
            )
            if marker_exists is not None:
                await self._session.rollback()
                return False
            sequence = await self._next_event_sequence(run_id)
            self._session.add_all(
                [
                    ChatRunEventModel(
                        run_id=run_id,
                        sequence=sequence + offset,
                        event_type=event_type,
                        payload=payload,
                    )
                    for offset, (event_type, payload) in enumerate(events)
                ]
            )
            await self._session.commit()
        except ChatSessionNotFoundError:
            await self._session.rollback()
            raise
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return True

    async def get_memory(self, session_id: UUID) -> ConversationMemoryRecord | None:
        try:
            model = await self._session.get(ChatMemoryModel, session_id)
            record = None if model is None else _memory_record(model)
            await self._session.commit()
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return record

    async def save_memory(
        self,
        *,
        session_id: UUID,
        summary: str,
        durable_facts: tuple[str, ...],
        covered_message_count: int,
        expected_revision: int,
    ) -> ConversationMemoryRecord | None:
        if not summary.strip():
            raise ValueError("memory summary must not be blank")
        if covered_message_count < 0 or expected_revision < 0:
            raise ValueError("memory counts and revisions must not be negative")
        try:
            chat_session = (
                await self._session.scalars(
                    select(ChatSessionModel)
                    .where(ChatSessionModel.id == session_id)
                    .with_for_update()
                )
            ).one_or_none()
            if chat_session is None:
                raise ChatSessionNotFoundError(
                    f"chat session {session_id} does not exist"
                )
            model = (
                await self._session.scalars(
                    select(ChatMemoryModel)
                    .where(ChatMemoryModel.session_id == session_id)
                    .with_for_update()
                )
            ).one_or_none()
            current_revision = 0 if model is None else model.revision
            if current_revision != expected_revision:
                await self._session.rollback()
                return None
            if model is None:
                model = ChatMemoryModel(
                    session_id=session_id,
                    summary=summary.strip(),
                    durable_facts=list(durable_facts),
                    covered_message_count=covered_message_count,
                    revision=1,
                )
                self._session.add(model)
            else:
                model.summary = summary.strip()
                model.durable_facts = list(durable_facts)
                model.covered_message_count = covered_message_count
                model.revision += 1
                model.updated_at = func.now()
            await self._session.commit()
            await self._session.refresh(model)
        except ChatSessionNotFoundError:
            await self._session.rollback()
            raise
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return _memory_record(model)

    async def _lock_run(self, run_id: UUID) -> ChatRunModel:
        statement = (
            select(ChatRunModel).where(ChatRunModel.id == run_id).with_for_update()
        )
        run = (await self._session.scalars(statement)).one_or_none()
        if run is None:
            raise ChatSessionNotFoundError(f"chat run {run_id} does not exist")
        return run

    async def _next_event_sequence(self, run_id: UUID) -> int:
        statement = select(
            func.coalesce(func.max(ChatRunEventModel.sequence), 0)
        ).where(ChatRunEventModel.run_id == run_id)
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
        question=model.question,
        scope=None if model.scope is None else _scope(model.scope),
        retrieval_mode=cast(RetrievalMode, model.retrieval_mode),
        history_message_count=model.history_message_count,
    )


def _event_record(model: ChatRunEventModel) -> ChatRunEventRecord:
    return ChatRunEventRecord(
        run_id=model.run_id,
        sequence=model.sequence,
        event_type=model.event_type,
        payload=model.payload,
        created_at=model.created_at,
    )


def _memory_record(model: ChatMemoryModel) -> ConversationMemoryRecord:
    facts = model.durable_facts
    if not isinstance(facts, list) or any(not isinstance(item, str) for item in facts):
        raise ChatPersistenceError()
    return ConversationMemoryRecord(
        session_id=model.session_id,
        summary=model.summary,
        durable_facts=tuple(facts),
        covered_message_count=model.covered_message_count,
        revision=model.revision,
        updated_at=model.updated_at,
    )
