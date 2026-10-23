import asyncio
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from research_agent.chat.application import ChatApplication
from research_agent.chat.domain import (
    ModelKnowledgeBlock,
    VerifiedAnswer,
)
from research_agent.chat.models import ChatMessageModel, ChatRunModel
from research_agent.chat.repository import SqlChatRepository
from research_agent.chat.stream import PersistedChatStream
from research_agent.main import create_app
from research_agent.papers.models import OutboxEventModel
from research_agent.papers.routes import get_db_session
from research_agent.research.domain import SpaceInput, SpaceUpdate
from research_agent.research.repository import SqlResearchRepository
from research_agent.retrieval.domain import RetrievalScope
from research_agent.shared.errors import AppError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_concurrent_retransmissions_create_one_run_message_and_outbox(
    independent_session_factory,
):
    factory = independent_session_factory
    async with factory() as db:
        chat = await SqlChatRepository(db).create_session(
            title="并发重传", scope=RetrievalScope()
        )
    request_id = uuid4()

    async def submit():
        async with factory() as db:
            return await SqlChatRepository(db).start_run(
                session_id=chat.id,
                question="同一个问题",
                client_request_id=request_id,
                dispatch=True,
            )

    results = await asyncio.gather(*(submit() for _ in range(4)))
    assert len({item.id for item in results}) == 1
    assert sum(item.replayed for item in results) == 3
    async with factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ChatMessageModel)
                .where(ChatMessageModel.session_id == chat.id)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ChatRunModel)
                .where(ChatRunModel.session_id == chat.id)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(OutboxEventModel)
                .where(OutboxEventModel.payload["run_id"].astext == str(results[0].id))
            )
            == 1
        )


async def test_request_id_conflict_and_session_boundary(db_session):
    repo = SqlChatRepository(db_session)
    first = await repo.create_session(title="第一会话", scope=RetrievalScope())
    second = await repo.create_session(title="第二会话", scope=RetrievalScope())
    key = uuid4()
    run = await repo.start_run(
        session_id=first.id, question="原问题", client_request_id=key
    )
    with pytest.raises(AppError) as conflict:
        await repo.start_run(
            session_id=first.id, question="篡改问题", client_request_id=key
        )
    assert conflict.value.code == "CHAT_REQUEST_CONFLICT"
    other = await repo.start_run(
        session_id=second.id, question="原问题", client_request_id=key
    )
    assert other.id != run.id
    await repo.fail_run(run_id=run.id, error_code="TEST_FAILURE")
    replay = await repo.start_run(
        session_id=first.id, question="原问题", client_request_id=key
    )
    assert replay.id == run.id and replay.status == "FAILED"
    assert len(await repo.list_messages(first.id)) == 1


async def test_retry_keeps_original_snapshot_user_message_and_terminal_state(
    db_session,
):
    repo, spaces = SqlChatRepository(db_session), SqlResearchRepository(db_session)
    space = await spaces.save(SpaceInput(name="研究", resource_constraints="24GB"))
    chat = await spaces.create_session(space.id, title="重试快照")
    original = await repo.start_run(
        session_id=chat.id, question="继续研究", client_request_id=uuid4()
    )
    cached = {
        "context": [{"role": "user", "content": "最初讨论"}],
        "memory_summary": "最初摘要",
        "memory_compacted": False,
    }
    await repo.save_context_snapshot(original.id, cached)
    await repo.fail_run(run_id=original.id, error_code="PROVIDER_TIMEOUT")
    original_done = await repo.get_run(original.id)
    await spaces.save(
        SpaceUpdate(name="研究", expected_revision=1, resource_constraints="12GB"),
        space_id=space.id,
    )
    await repo.save_memory(
        session_id=chat.id,
        summary="之后的新摘要",
        durable_facts=("之后的信息",),
        covered_message_count=1,
        expected_revision=0,
    )
    retry_key = uuid4()
    retried = await repo.start_run(
        session_id=chat.id,
        question="继续研究",
        client_request_id=retry_key,
        retry_of_run_id=original.id,
        dispatch=True,
    )
    assert retried.id != original.id and retried.retry_of_run_id == original.id
    assert retried.user_message_id == original.user_message_id
    assert retried.research_context == original.research_context
    assert retried.research_context["resource_constraints"] == "24GB"
    assert retried.history_message_count == original.history_message_count
    assert retried.memory_snapshot == original.memory_snapshot
    assert await repo.get_context_snapshot(retried.id) == cached
    assert len(await repo.list_messages(chat.id)) == 1
    replay = await repo.start_run(
        session_id=chat.id,
        question="继续研究",
        client_request_id=retry_key,
        retry_of_run_id=original.id,
        dispatch=True,
    )
    assert replay.id == retried.id and replay.replayed
    assert await repo.get_run(original.id) == original_done
    await repo.claim_run(retried.id, lease=timedelta(minutes=1))
    answer = await repo.complete_run(
        run_id=retried.id,
        answer=VerifiedAnswer(
            blocks=[ModelKnowledgeBlock(text="保留原设置的重试回答")]
        ),
    )
    history = await repo.list_messages(chat.id)
    assert [item.sequence for item in history] == [1, 2]
    assert history[0].id == original.user_message_id and history[-1].id == answer.id
    await PersistedChatStream(repo).prepare(retried.id)
    events = await repo.list_events(retried.id)
    start = next(
        event.payload
        for event in events
        if event.event_type == "ui_part"
        and event.payload.get("part", {}).get("type") == "text-start"
    )
    assert start["part"]["id"] == str(answer.id)


async def test_pagination_cursor_is_stable_while_new_messages_arrive(db_session):
    repo = SqlChatRepository(db_session)
    chat = await repo.create_session(title="分页", scope=RetrievalScope())
    for i in range(3):
        run = await repo.start_run(session_id=chat.id, question=f"问题{i}")
        await repo.complete_run(
            run_id=run.id,
            answer=VerifiedAnswer(blocks=[ModelKnowledgeBlock(text=f"回答{i}")]),
        )
    latest, cursor = await repo.list_message_page(chat.id, limit=2)
    assert [item.sequence for item in latest] == [5, 6] and cursor == 5
    await repo.start_run(session_id=chat.id, question="新到消息")
    older, next_cursor = await repo.list_message_page(
        chat.id, limit=2, before_sequence=cursor
    )
    first, end = await repo.list_message_page(
        chat.id, limit=2, before_sequence=next_cursor
    )
    assert [item.sequence for item in older] == [3, 4]
    assert [item.sequence for item in first] == [1, 2] and end is None
    assert len({item.id for item in [*first, *older, *latest]}) == 6


async def test_successful_run_cannot_be_retried(db_session):
    repo = SqlChatRepository(db_session)
    chat = await repo.create_session(title="终态", scope=RetrievalScope())
    run = await repo.start_run(session_id=chat.id, question="问题")
    await repo.complete_run(
        run_id=run.id, answer=VerifiedAnswer(blocks=[ModelKnowledgeBlock(text="回答")])
    )
    with pytest.raises(AppError) as rejected:
        await repo.start_run(
            session_id=chat.id,
            question="问题",
            retry_of_run_id=run.id,
            client_request_id=uuid4(),
        )
    assert rejected.value.code == "CHAT_RUN_NOT_RETRYABLE"


async def test_http_ack_retransmission_and_explicit_retry(independent_session_factory):
    factory = independent_session_factory
    app = create_app(chat_commands=ChatApplication(factory, dispatch_outbox=True))

    async def database():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = (
            await client.post(
                "/api/v1/chat/sessions", json={"title": "HTTP 重传", "document_ids": []}
            )
        ).json()
        url = f"/api/v1/chat/sessions/{created['id']}/messages"
        body = {
            "question": "相同请求",
            "client_request_id": str(uuid4()),
            "retrieval_mode": "off",
        }
        first = await client.post(url, json=body)
        second = await client.post(url, json=body)
        assert first.status_code == 202 and second.status_code == 202
        assert first.json() == second.json()
        assert first.json()["user_message_id"]
        run_id = first.json()["run_id"]
        async with factory() as db:
            repo = SqlChatRepository(db)
            await repo.claim_run(run_id, lease=timedelta(minutes=1))
            await repo.fail_run(run_id=run_id, error_code="TEST_PROVIDER_FAILURE")
        retry_body = {"client_request_id": str(uuid4())}
        retried = await client.post(
            f"/api/v1/chat/runs/{run_id}/retry", json=retry_body
        )
        assert retried.status_code == 202, retried.text
        assert retried.json()["run_id"] != run_id
        assert retried.json()["user_message_id"] == first.json()["user_message_id"]
        duplicate = await client.post(
            f"/api/v1/chat/runs/{run_id}/retry", json=retry_body
        )
        assert duplicate.json() == retried.json()
        history = (await client.get(url + "?limit=1")).json()
        assert len(history["items"]) == 1
        assert history["items"][0]["sequence"] == 1
        assert (await client.get(url + "?limit=201")).status_code == 422
