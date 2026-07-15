import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from celery import Celery
from fastapi import FastAPI
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.main import create_app, run_outbox_relay_pass
from research_agent.papers.models import OutboxEventModel
from research_agent.tasks.outbox_relay import OutboxRelay, SqlOutboxRepository


class RecordingSender:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, dict[str, str], str]] = []

    def send_task(
        self,
        name: str,
        *,
        kwargs: dict[str, str],
        queue: str,
    ) -> None:
        self.calls.append((name, kwargs, queue))
        if self.fail:
            raise RuntimeError("broker unavailable")


async def add_event(
    session: AsyncSession,
    *,
    available_at: datetime | None = None,
    sent_at: datetime | None = None,
) -> tuple[UUID, str]:
    event_id = uuid4()
    version_id = str(uuid4())
    session.add(
        OutboxEventModel(
            id=event_id,
            event_type="document.uploaded",
            payload={"version_id": version_id},
            attempts=0,
            available_at=available_at or datetime.now(UTC),
            sent_at=sent_at,
        )
    )
    await session.commit()
    return event_id, version_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_due_event_is_sent_exactly_once_and_not_selected_again(
    db_session: AsyncSession,
) -> None:
    event_id, version_id = await add_event(db_session)
    sender = RecordingSender()
    relay = OutboxRelay(SqlOutboxRepository(db_session), sender)

    assert await relay.run_once() == 1
    assert sender.calls == [
        (
            "research_agent.ingestion.process_document",
            {"version_id": version_id},
            "paper_ingestion",
        )
    ]
    await db_session.refresh(
        cast(OutboxEventModel, await db_session.get(OutboxEventModel, event_id))
    )
    persisted = await db_session.get(OutboxEventModel, event_id)
    assert persisted is not None
    assert persisted.sent_at is not None

    assert await relay.run_once() == 0
    assert len(sender.calls) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_send_failure_increments_attempts_and_leaves_event_unsent(
    db_session: AsyncSession,
) -> None:
    event_id, _ = await add_event(db_session)
    sender = RecordingSender(fail=True)
    relay = OutboxRelay(SqlOutboxRepository(db_session), sender)

    assert await relay.run_once() == 0

    persisted = await db_session.get(OutboxEventModel, event_id)
    assert persisted is not None
    await db_session.refresh(persisted)
    assert persisted.attempts == 1
    assert persisted.sent_at is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_future_event_is_not_selected(db_session: AsyncSession) -> None:
    event_id, _ = await add_event(
        db_session,
        available_at=datetime.now(UTC) + timedelta(hours=1),
    )
    sender = RecordingSender()

    assert await OutboxRelay(
        SqlOutboxRepository(db_session), sender
    ).run_once() == 0
    assert sender.calls == []
    persisted = await db_session.get(OutboxEventModel, event_id)
    assert persisted is not None
    assert persisted.sent_at is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_claimers_do_not_send_the_same_event(
    independent_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with independent_session_factory() as seed_session:
        _, version_id = await add_event(seed_session)

    sender = RecordingSender()
    async with (
        independent_session_factory() as first_session,
        independent_session_factory() as second_session,
    ):
        sent_counts = await asyncio.gather(
            OutboxRelay(
                SqlOutboxRepository(first_session), sender
            ).run_once(),
            OutboxRelay(
                SqlOutboxRepository(second_session), sender
            ).run_once(),
        )

    assert sum(sent_counts) == 1
    assert sender.calls == [
        (
            "research_agent.ingestion.process_document",
            {"version_id": version_id},
            "paper_ingestion",
        )
    ]


class FailingSession:
    def __init__(self) -> None:
        self.rollback_calls = 0

    async def __aenter__(self) -> "FailingSession":
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        return None

    async def scalars(self, statement: object) -> None:
        del statement
        raise RuntimeError("database pass failed")

    async def rollback(self) -> None:
        self.rollback_calls += 1


@pytest.mark.asyncio
async def test_unexpected_pass_failure_rolls_back() -> None:
    session = FailingSession()

    with pytest.raises(RuntimeError, match="database pass failed"):
        await run_outbox_relay_pass(
            lambda: session,
            cast(Celery, RecordingSender()),
        )

    assert session.rollback_calls == 1


@pytest.mark.asyncio
async def test_lifespan_continues_after_failure_and_cancels_cleanly() -> None:
    calls = 0
    recovered = asyncio.Event()
    block = asyncio.Event()

    async def flaky_pass() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient relay failure")
        recovered.set()

    async def controlled_sleep(delay: float) -> None:
        assert delay == 1
        if calls == 1:
            await asyncio.sleep(0)
        else:
            await block.wait()

    app = create_app(relay_pass=flaky_pass, relay_sleep=controlled_sleep)

    async with app.router.lifespan_context(cast(FastAPI, app)):
        await asyncio.wait_for(recovered.wait(), timeout=1)
        relay_task = app.state.outbox_relay_task
        assert not relay_task.done()

    assert calls == 2
    assert relay_task.done()
    assert relay_task.cancelled()
