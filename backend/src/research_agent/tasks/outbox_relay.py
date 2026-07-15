from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.papers.models import OutboxEventModel
from research_agent.shared.ids import EntityId


@dataclass(frozen=True, slots=True)
class OutboxEventRecord:
    id: EntityId
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
                OutboxEventModel.available_at <= func.now(),
            )
            .order_by(OutboxEventModel.available_at, OutboxEventModel.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        events = (await self._session.scalars(statement)).all()
        return [
            OutboxEventRecord(id=event.id, payload=event.payload)
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
                version_id = event.payload["version_id"]
                if not isinstance(version_id, str):
                    raise TypeError("outbox version_id must be a string")
                self._celery.send_task(
                    "research_agent.ingestion.process_document",
                    kwargs={"version_id": version_id},
                    queue="paper_ingestion",
                )
            except Exception:
                await self._repository.record_failure(event.id)
                continue
            await self._repository.mark_sent(event.id)
            sent += 1
        await self._repository.commit()
        return sent
