from dataclasses import replace
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
from research_agent.config import get_settings
from research_agent.papers.models import OutboxEventModel
from research_agent.research.models import (
    ResearchSpaceMaterialModel,
    ResearchSpaceModel,
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
            scope=_scope_payload(scope),
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
        client_request_id: UUID | None = None,
        retry_of_run_id: UUID | None = None,
    ) -> ChatRunRecord:
        if not question.strip():
            raise ValueError("question must not be blank")
        if retrieval_mode not in {"auto", "off", "on"}:
            raise ValueError("invalid retrieval mode")
        research_context: dict[str, object] | None = None
        space_scope: RetrievalScope | None = None
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
            if client_request_id is not None:
                existing = await self._session.scalar(
                    select(ChatRunModel).where(
                        ChatRunModel.session_id == session_id,
                        ChatRunModel.client_request_id == client_request_id,
                    )
                )
                if existing is not None:
                    if (
                        existing.question != question.strip()
                        or existing.retrieval_mode != retrieval_mode
                        or existing.retry_of_run_id != retry_of_run_id
                    ):
                        raise AppError(
                            "CHAT_REQUEST_CONFLICT",
                            "同一请求 ID 不能用于不同问题或重试任务",
                        )
                    record = replace(_run_record(existing), replayed=True)
                    await self._session.commit()
                    return record
            original = None
            if retry_of_run_id is not None:
                original = await self._session.get(ChatRunModel, retry_of_run_id)
                if original is None or original.session_id != session_id:
                    raise AppError("CHAT_RUN_NOT_FOUND", "Chat run was not found")
                if original.status not in ("FAILED", "CANCELLED"):
                    raise AppError(
                        "CHAT_RUN_NOT_RETRYABLE", "仅可重试已失败或已取消的任务"
                    )
                if (
                    original.scope is None
                    or original.memory_snapshot is None
                    or original.user_message_id is None
                ):
                    raise AppError(
                        "CHAT_RUN_NOT_RETRYABLE", "旧任务没有完整输入快照，请发起新提问"
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
            if chat_session.research_space_id is not None:
                space = await self._session.scalar(
                    select(ResearchSpaceModel)
                    .where(ResearchSpaceModel.id == chat_session.research_space_id)
                    .with_for_update()
                )
                if space is None:
                    raise AppError(
                        "RESEARCH_SPACE_NOT_FOUND", "Research space was not found"
                    )
                links = (
                    await self._session.scalars(
                        select(ResearchSpaceMaterialModel)
                        .where(ResearchSpaceMaterialModel.space_id == space.id)
                        .order_by(ResearchSpaceMaterialModel.document_id)
                    )
                ).all()
                space_scope = RetrievalScope(
                    document_ids=tuple(link.document_id for link in links),
                    document_version_ids=tuple(
                        link.document_version_id for link in links
                    ),
                    restrict_to_scope=True,
                )
                research_context = {
                    "space_id": str(space.id),
                    "revision": space.revision,
                    "name": space.name,
                    "goal": space.goal,
                    "task_settings": space.task_settings,
                    "resource_constraints": space.resource_constraints,
                    "current_question": space.current_question,
                }
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
        resolved_scope = space_scope or scope or _scope(chat_session.scope)
        memory = await self._session.get(ChatMemoryModel, session_id)
        memory_snapshot: dict[str, object] = {
            "summary": "" if memory is None else memory.summary,
            "durable_facts": [] if memory is None else list(memory.durable_facts),
            "covered_message_count": 0
            if memory is None
            else memory.covered_message_count,
            "revision": 0 if memory is None else memory.revision,
            "covered_through_sequence": 0
            if memory is None
            else memory.covered_through_sequence,
            "critical_constraints": []
            if memory is None
            else memory.critical_constraints,
            "history_max_sequence": int(
                await self._session.scalar(
                    select(func.coalesce(func.max(ChatMessageModel.sequence), 0)).where(
                        ChatMessageModel.session_id == session_id
                    )
                )
                or 0
            ),
            "context_budget": get_settings().chat_context_budget.model_dump(
                mode="json"
            ),
            "graph_version": "t1.3-v1",
            "llm_model": get_settings().llm_model,
            "research_records_revision": None,
            "memory_max_context_units": get_settings().memory_max_context_tokens,
            "memory_recent_units": get_settings().memory_recent_message_tokens,
        }
        if original is not None:
            resolved_scope = _scope(original.scope or {})
            research_context = original.research_context
            history_message_count = original.history_message_count
            memory_snapshot = original.memory_snapshot or {}
        chat_session.updated_at = func.now()
        user_message_id = new_id() if original is None else original.user_message_id
        run = ChatRunModel(
            id=new_id(),
            session_id=session_id,
            status="QUEUED" if dispatch else "RUNNING",
            error_code=None,
            question=question.strip(),
            scope=_scope_payload(resolved_scope),
            research_context=research_context,
            client_request_id=client_request_id,
            user_message_id=user_message_id,
            retry_of_run_id=retry_of_run_id,
            memory_snapshot=memory_snapshot,
            context_snapshot=None if original is None else original.context_snapshot,
            request_snapshots=None if original is None else original.request_snapshots,
            retrieval_mode=retrieval_mode,
            history_message_count=history_message_count,
            lease_expires_at=None,
            completed_at=None,
        )
        message = ChatMessageModel(
            id=user_message_id,
            session_id=session_id,
            role="user",
            content={"text": question.strip()},
            sequence=int(
                await self._session.scalar(
                    select(func.coalesce(func.max(ChatMessageModel.sequence), 0)).where(
                        ChatMessageModel.session_id == session_id
                    )
                )
                or 0
            )
            + 1,
        )
        event = ChatRunEventModel(
            run_id=run.id,
            sequence=1,
            event_type="run_started",
            payload={},
        )
        try:
            if original is None:
                self._session.add(message)
                await self._session.flush()
            self._session.add(run)
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
                sequence=int(
                    await self._session.scalar(
                        select(
                            func.coalesce(func.max(ChatMessageModel.sequence), 0)
                        ).where(ChatMessageModel.session_id == run.session_id)
                    )
                    or 0
                )
                + 1,
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
            self._session.add(message)
            await self._session.flush()
            run.assistant_message_id = message.id
            self._session.add(event)
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
            .order_by(ChatMessageModel.sequence)
        )
        try:
            models = (await self._session.scalars(statement)).all()
            records = [_message_record(model) for model in models]
            await self._session.commit()
        except SQLAlchemyError:
            await self._rollback_after_error()
            raise ChatPersistenceError() from None
        return records

    async def list_message_page(
        self, session_id: UUID, *, before_sequence: int | None = None, limit: int = 50
    ) -> tuple[list[ChatMessageRecord], int | None]:
        if not 1 <= limit <= 200:
            raise ValueError("message page limit must be between 1 and 200")
        query = select(ChatMessageModel).where(
            ChatMessageModel.session_id == session_id
        )
        if before_sequence is not None:
            query = query.where(ChatMessageModel.sequence < before_sequence)
        rows = list(
            (
                await self._session.scalars(
                    query.order_by(ChatMessageModel.sequence.desc()).limit(limit + 1)
                )
            ).all()
        )
        more = len(rows) > limit
        records = [_message_record(row) for row in reversed(rows[:limit])]
        await self._session.commit()
        return records, records[0].sequence if more and records else None

    async def list_history_slice(
        self,
        session_id: UUID,
        *,
        after_sequence: int,
        through_sequence: int,
        limit: int,
        descending: bool = False,
    ) -> list[ChatMessageRecord]:
        if not 1 <= limit <= 200:
            raise ValueError("history slice limit must be between 1 and 200")
        query = select(ChatMessageModel).where(
            ChatMessageModel.session_id == session_id,
            ChatMessageModel.sequence > after_sequence,
            ChatMessageModel.sequence <= through_sequence,
        )
        order = (
            ChatMessageModel.sequence.desc()
            if descending
            else ChatMessageModel.sequence.asc()
        )
        rows = list(
            (await self._session.scalars(query.order_by(order).limit(limit))).all()
        )
        records = [_message_record(row) for row in rows]
        await self._session.commit()
        return list(reversed(records)) if descending else records

    async def history_sequence_at(self, session_id: UUID, count: int) -> int:
        if count <= 0:
            return 0
        value = await self._session.scalar(
            select(ChatMessageModel.sequence)
            .where(ChatMessageModel.session_id == session_id)
            .order_by(ChatMessageModel.sequence)
            .offset(count - 1)
            .limit(1)
        )
        await self._session.commit()
        return int(value or 0)

    async def get_prompt_snapshot(
        self, run_id: UUID, key: str
    ) -> dict[str, object] | None:
        model = await self._session.get(ChatRunModel, run_id)
        value = None if model is None else (model.request_snapshots or {}).get(key)
        await self._session.commit()
        return value if isinstance(value, dict) else None

    async def save_prompt_snapshot(
        self, run_id: UUID, key: str, value: dict[str, object]
    ) -> dict[str, object]:
        run = await self._lock_run(run_id)
        snapshots = dict(run.request_snapshots or {})
        if key not in snapshots:
            if run.status != "RUNNING":
                await self._session.rollback()
                raise AppError("CHAT_RUN_NOT_RUNNING", "运行已停止，无法保存模型输入")
            snapshots[key] = value
            run.request_snapshots = snapshots
        saved = snapshots[key]
        await self._session.commit()
        if not isinstance(saved, dict):
            raise AppError("CHAT_INPUT_SNAPSHOT_INVALID", "模型输入快照无法读取")
        return saved

    async def get_context_snapshot(self, run_id: UUID) -> dict[str, object] | None:
        model = await self._session.get(ChatRunModel, run_id)
        value = None if model is None else model.context_snapshot
        await self._session.commit()
        return value

    async def save_context_snapshot(
        self, run_id: UUID, value: dict[str, object]
    ) -> None:
        run = await self._lock_run(run_id)
        if run.context_snapshot is None and run.status == "RUNNING":
            run.context_snapshot = value
        await self._session.commit()

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
        covered_through_sequence: int = 0,
        critical_constraints: tuple[str, ...] = (),
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
            if covered_message_count and covered_through_sequence == 0:
                # Older callers only know ordinal coverage. Resolve its stable
                # sequence under the session lock, without reading message text.
                covered_through_sequence = int(
                    await self._session.scalar(
                        select(ChatMessageModel.sequence)
                        .where(ChatMessageModel.session_id == session_id)
                        .order_by(ChatMessageModel.sequence)
                        .offset(covered_message_count - 1)
                        .limit(1)
                    )
                    or 0
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
                    covered_through_sequence=covered_through_sequence,
                    critical_constraints=list(critical_constraints),
                    revision=1,
                )
                self._session.add(model)
            else:
                model.summary = summary.strip()
                model.durable_facts = list(durable_facts)
                model.covered_message_count = covered_message_count
                model.covered_through_sequence = covered_through_sequence
                model.critical_constraints = list(critical_constraints)
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
    raw_versions = value.get("document_version_ids", [])
    restricted = value.get("restrict_to_scope", False)
    if (
        not isinstance(raw_ids, list)
        or not isinstance(raw_versions, list)
        or not isinstance(restricted, bool)
    ):
        raise ChatPersistenceError()
    return RetrievalScope(
        tuple(UUID(str(item)) for item in raw_ids),
        tuple(UUID(str(item)) for item in raw_versions),
        restricted,
    )


def _scope_payload(scope: RetrievalScope) -> dict[str, object]:
    value: dict[str, object] = {
        "document_ids": [str(item) for item in scope.document_ids]
    }
    if scope.restrict_to_scope or scope.document_version_ids:
        value.update(
            document_version_ids=[str(item) for item in scope.document_version_ids],
            restrict_to_scope=scope.restrict_to_scope,
        )
    return value


def _session_record(model: ChatSessionModel) -> ChatSessionRecord:
    return ChatSessionRecord(
        id=model.id,
        title=model.title,
        scope=_scope(model.scope),
        created_at=model.created_at,
        updated_at=model.updated_at,
        research_space_id=model.research_space_id,
    )


def _message_record(model: ChatMessageModel) -> ChatMessageRecord:
    return ChatMessageRecord(
        id=model.id,
        session_id=model.session_id,
        role=cast(ChatRole, model.role),
        content=model.content,
        created_at=model.created_at,
        sequence=model.sequence,
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
        research_context=model.research_context,
        client_request_id=model.client_request_id,
        user_message_id=model.user_message_id,
        assistant_message_id=model.assistant_message_id,
        retry_of_run_id=model.retry_of_run_id,
        memory_snapshot=model.memory_snapshot,
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
        covered_through_sequence=model.covered_through_sequence,
        critical_constraints=tuple(model.critical_constraints),
    )
