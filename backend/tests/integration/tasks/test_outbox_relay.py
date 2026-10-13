import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from celery import Celery
from fastapi import FastAPI
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.chat.domain import RetrievalScope
from research_agent.chat.models import ChatRunModel, ChatSessionModel
from research_agent.chat.repository import SqlChatRepository
from research_agent.main import create_app, run_outbox_relay_pass
from research_agent.papers.models import OutboxEventModel
from research_agent.tasks.outbox_relay import OutboxRelay, SqlOutboxRepository
from research_agent.tasks.recovery import SqlChatRunRecovery


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
    payload: dict[str, object] | None = None,
    event_type: str = "document.uploaded",
) -> tuple[UUID, str]:
    event_id = uuid4()
    version_id = str(uuid4())
    session.add(
        OutboxEventModel(
            id=event_id,
            event_type=event_type,
            payload=payload or {"version_id": version_id},
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
async def test_nested_payload_cannot_override_fixed_ingestion_routing(
    db_session: AsyncSession,
) -> None:
    version_id = str(uuid4())
    await add_event(
        db_session,
        payload={
            "task_name": "untrusted.task",
            "queue": "untrusted_queue",
            "kwargs": {"version_id": version_id},
        },
    )
    sender = RecordingSender()

    assert await OutboxRelay(SqlOutboxRepository(db_session), sender).run_once() == 1
    assert sender.calls == [
        (
            "research_agent.ingestion.process_document",
            {"version_id": version_id},
            "paper_ingestion",
        )
    ]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_run_event_uses_allowlisted_chat_worker_route(
    db_session: AsyncSession,
) -> None:
    run_id = str(uuid4())
    await add_event(
        db_session,
        event_type="chat.run.requested",
        payload={
            "task_name": "untrusted.task",
            "queue": "untrusted_queue",
            "run_id": run_id,
        },
    )
    sender = RecordingSender()

    assert await OutboxRelay(SqlOutboxRepository(db_session), sender).run_once() == 1
    assert sender.calls == [
        (
            "research_agent.chat.process_run",
            {"run_id": run_id},
            "chat_generation",
        )
    ]


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

    assert await OutboxRelay(SqlOutboxRepository(db_session), sender).run_once() == 0
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
            OutboxRelay(SqlOutboxRepository(first_session), sender).run_once(),
            OutboxRelay(SqlOutboxRepository(second_session), sender).run_once(),
        )

    assert sum(sent_counts) == 1
    assert sender.calls == [
        (
            "research_agent.ingestion.process_document",
            {"version_id": version_id},
            "paper_ingestion",
        )
    ]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_chat_lease_is_requeued_with_a_fresh_outbox_event(
    independent_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with independent_session_factory() as seed_session:
        chats = SqlChatRepository(seed_session)
        chat_session = await chats.create_session(
            title="Recover lost worker",
            scope=RetrievalScope(),
        )
        run = await chats.start_run(
            session_id=chat_session.id,
            question="Resume this turn",
            dispatch=True,
        )
        original = (
            await seed_session.scalars(
                select(OutboxEventModel).where(
                    OutboxEventModel.event_type == "chat.run.requested",
                    OutboxEventModel.payload["run_id"].as_string() == str(run.id),
                )
            )
        ).one()
        original.sent_at = datetime.now(UTC)
        await seed_session.commit()
        claimed = await chats.claim_run(run.id, lease=timedelta(seconds=1))
        assert claimed is not None
        await chats.record_event(
            run_id=run.id,
            event_type="worker_heartbeat",
            payload={},
        )
        renewed = await seed_session.get(ChatRunModel, run.id)
        assert renewed is not None
        assert renewed.lease_expires_at is not None
        assert renewed.lease_expires_at > datetime.now(UTC) + timedelta(minutes=20)
        renewed.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await seed_session.commit()

    async with independent_session_factory() as recovery_session:
        recovered_count = await SqlChatRunRecovery(recovery_session).requeue_expired()
        recovered = await recovery_session.get(ChatRunModel, run.id)
        outbox = (
            await recovery_session.scalars(
                select(OutboxEventModel).where(
                    OutboxEventModel.event_type == "chat.run.requested",
                    OutboxEventModel.payload["run_id"].as_string() == str(run.id),
                )
            )
        ).all()

        assert recovered_count == 1
        assert recovered is not None
        assert recovered.status == "QUEUED"
        assert recovered.lease_expires_at is None
        assert len(outbox) == 2
        assert sum(event.sent_at is None for event in outbox) == 1

        await recovery_session.execute(
            delete(OutboxEventModel).where(
                OutboxEventModel.id.in_([e.id for e in outbox])
            )
        )
        await recovery_session.execute(
            delete(ChatSessionModel).where(ChatSessionModel.id == chat_session.id)
        )
        await recovery_session.commit()


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
