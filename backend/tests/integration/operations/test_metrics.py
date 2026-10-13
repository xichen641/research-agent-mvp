from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.chat.domain import RetrievalScope
from research_agent.chat.models import ChatRunModel, ChatSessionModel
from research_agent.chat.repository import SqlChatRepository
from research_agent.operations.metrics import SqlOperationalMetrics, render_prometheus
from research_agent.papers.models import OutboxEventModel


@pytest.mark.integration
@pytest.mark.asyncio
async def test_operational_snapshot_reports_backlog_and_expired_leases(
    db_session: AsyncSession,
) -> None:
    outbox_id = uuid4()
    db_session.add(
        OutboxEventModel(
            id=outbox_id,
            event_type="chat.run.requested",
            payload={"run_id": str(uuid4())},
            attempts=2,
            available_at=datetime.now(UTC) - timedelta(seconds=10),
            sent_at=None,
        )
    )
    await db_session.commit()
    chats = SqlChatRepository(db_session)
    chat_session = await chats.create_session(
        title="Expired metrics run",
        scope=RetrievalScope(),
    )
    run = await chats.start_run(
        session_id=chat_session.id,
        question="This worker disappeared",
    )
    model = await db_session.get(ChatRunModel, run.id)
    assert model is not None
    model.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    snapshot = await SqlOperationalMetrics(db_session).collect()
    rendered = render_prometheus(snapshot)

    assert snapshot.outbox_pending >= 1
    assert snapshot.outbox_attempts >= 2
    assert snapshot.outbox_oldest_age_seconds >= 9
    assert snapshot.expired_chat_leases >= 1
    assert snapshot.chat_runs["RUNNING"] >= 1
    assert 'research_agent_chat_runs{status="RUNNING"}' in rendered
    assert str(run.id) not in rendered

    await db_session.execute(
        delete(OutboxEventModel).where(OutboxEventModel.id == outbox_id)
    )
    await db_session.execute(
        delete(ChatSessionModel).where(ChatSessionModel.id == chat_session.id)
    )
    await db_session.commit()
