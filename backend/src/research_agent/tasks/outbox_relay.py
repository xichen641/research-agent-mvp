from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.papers.models import OutboxEventModel
from research_agent.shared.ids import EntityId


INGESTION_TASK_NAME = "research_agent.ingestion.process_document"
INGESTION_QUEUE = "paper_ingestion"
CHAT_TASK_NAME = "research_agent.chat.process_run"
CHAT_QUEUE = "chat_generation"
OUTBOX_ROUTES = {
    "document.uploaded": (INGESTION_TASK_NAME, INGESTION_QUEUE, "version_id"),
    "document.retry_requested": (
        INGESTION_TASK_NAME,
        INGESTION_QUEUE,
        "version_id",
    ),
    "chat.run.requested": (CHAT_TASK_NAME, CHAT_QUEUE, "run_id"),
}


@dataclass(frozen=True, slots=True)
class OutboxEventRecord:
    id: EntityId
    event_type: str
    payload: dict[str, object]


@runtime_checkable
class CelerySender(Protocol):
    def send_task(
        self,
        name: str,
        *,
        kwargs: dict[str, str],
        queue: str,
    ) -> object: ...


@runtime_checkable
class OutboxRepository(Protocol):
    async def lock_unsent(self, *, limit: int) -> list[OutboxEventRecord]: ...

    async def record_failure(self, event_id: EntityId) -> None: ...

    async def mark_sent(self, event_id: EntityId) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class SqlOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_unsent(self, *, limit: int) -> list[OutboxEventRecord]:
        statement = (
            select(OutboxEventModel)
            .where(
                OutboxEventModel.sent_at.is_(None),
                # PostgreSQL now() is frozen at transaction start. A relay may
                # hold a transaction while dispatching, so eligibility must use
                # wall-clock statement time instead.
                OutboxEventModel.available_at <= func.clock_timestamp(),
            )
            .order_by(OutboxEventModel.available_at, OutboxEventModel.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        events = (await self._session.scalars(statement)).all()
        return [
            OutboxEventRecord(
                id=event.id,
                event_type=event.event_type,
                payload=event.payload,
            )
            for event in events
        ]

    async def record_failure(self, event_id: EntityId) -> None:
        await self._session.execute(
            update(OutboxEventModel)
            .where(OutboxEventModel.id == event_id)
            .values(attempts=OutboxEventModel.attempts + 1)
        )

    async def mark_sent(self, event_id: EntityId) -> None:
        await self._session.execute(
            update(OutboxEventModel)
            .where(OutboxEventModel.id == event_id)
            .values(sent_at=datetime.now(UTC))
        )

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


class OutboxRelay:
    def __init__(
        self,
        repository: OutboxRepository,
        celery: CelerySender,
    ) -> None:
        self._repository = repository
        self._celery = celery

    async def run_once(self, limit: int = 20) -> int:
        events = await self._repository.lock_unsent(limit=limit)
        sent = 0
        for event in events:
            try:
                route = OUTBOX_ROUTES.get(event.event_type)
                if route is None:
                    raise TypeError("outbox event type is not dispatchable")
                task_name, queue, argument_name = route
                kwargs = event.payload.get("kwargs")
                argument = (
                    kwargs.get(argument_name)
                    if isinstance(kwargs, dict)
                    else event.payload.get(argument_name)
                )
                if not isinstance(argument, str):
                    raise TypeError("outbox route argument must be a string")
                self._celery.send_task(
                    task_name,
                    kwargs={argument_name: argument},
                    queue=queue,
                )
            except Exception:
                await self._repository.record_failure(event.id)
                continue
            await self._repository.mark_sent(event.id)
            sent += 1
        await self._repository.commit()
        return sent
