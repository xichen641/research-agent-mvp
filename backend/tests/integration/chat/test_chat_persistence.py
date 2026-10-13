import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from research_agent.chat.application import ChatApplication
from research_agent.chat.domain import RetrievalScope
from research_agent.chat.graph import GraphDependencies, persistent_chat_graph
from research_agent.chat.models import ChatRunModel, ChatSessionModel
from research_agent.chat.repository import SqlChatRepository
from research_agent.chat.service import ChatService
from research_agent.chat.stream import PersistedChatStream
from research_agent.providers.fake import FakeLLMProvider
from research_agent.papers.models import OutboxEventModel
from research_agent.retrieval.domain import RetrievalCandidate, RetrievalQuery
from research_agent.shared.errors import AppError


class StubRetrieval:
    def __init__(self, candidates: list[RetrievalCandidate]) -> None:
        self.candidates = candidates
        self.queries: list[RetrievalQuery] = []

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievalCandidate]:
        self.queries.append(query)
        return self.candidates


def candidate(document_id: UUID) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=document_id,
        document_version_id=uuid4(),
        title="A paper",
        section_path=("Methods",),
        content="The method uses visible regions.",
        content_type="text",
        page_start=4,
        page_end=4,
        rerank_score=0.9,
    )


def fake_llm(source: RetrievalCandidate) -> FakeLLMProvider:
    return FakeLLMProvider(
        {
            "plan_query": {
                "intent": "fact",
                "original_query": "What does the method use?",
                "lexical_queries": ["method visible regions"],
                "semantic_query": "method visible regions",
            },
            "generate_draft": {
                "blocks": [
                    {
                        "type": "paper_evidence",
                        "text": "The method uses visible regions.",
                        "citations": [
                            {
                                "chunk_id": str(source.chunk_id),
                                "document_version_id": str(source.document_version_id),
                                "quote": source.content,
                            }
                        ],
                    }
                ]
            },
        }
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_successful_chat_survives_process_restart(
    migrated_database_url: str,
) -> None:
    document_id = uuid4()
    first_engine = create_async_engine(migrated_database_url)
    first_factory = async_sessionmaker(first_engine, expire_on_commit=False)

    async with first_factory() as first_session:
        first_repository = SqlChatRepository(first_session)
        source = candidate(document_id)
        retrieval = StubRetrieval([source])
        dependencies = GraphDependencies(fake_llm(source), retrieval, first_repository)
        async with persistent_chat_graph(
            migrated_database_url, dependencies
        ) as first_graph:
            service = ChatService(first_repository, first_graph)
            chat_session = await service.create_session(
                title="ReID methods",
                scope=RetrievalScope(document_ids=(document_id,)),
            )
            result = await service.ask(chat_session.id, "What does the method use?")

    await first_engine.dispose()

    # A new engine, repository, saver and graph simulate a fresh application process.
    # 新建引擎、仓储、Saver 与图，用于模拟应用进程重启。
    second_engine = create_async_engine(migrated_database_url)
    second_factory = async_sessionmaker(second_engine, expire_on_commit=False)
    async with second_factory() as second_session:
        second_repository = SqlChatRepository(second_session)
        dependencies = GraphDependencies(
            fake_llm(source), StubRetrieval([]), second_repository
        )
        async with persistent_chat_graph(
            migrated_database_url, dependencies
        ) as second_graph:
            snapshot = await second_graph.aget_state(
                {"configurable": {"thread_id": str(chat_session.id)}}
            )

        stream = PersistedChatStream(second_repository)
        await stream.prepare(result.run_id)
        streamed = "".join([chunk async for chunk in stream.iter_sse(result.run_id)])
        events_before_second_prepare = await second_repository.list_events(
            result.run_id
        )
        await stream.prepare(result.run_id)
        messages = await second_repository.list_messages(chat_session.id)
        persisted_run = await second_repository.get_run(result.run_id)
        events = await second_repository.list_events(result.run_id)

    await second_engine.dispose()

    assert retrieval.queries[0].scope.document_ids == (document_id,)
    assert result.answer.text == "The method uses visible regions."
    assert [message.role for message in messages] == ["user", "assistant"]
    blocks = messages[1].content["blocks"]
    assert isinstance(blocks, list)
    assert blocks[0]["text"] == result.answer.text
    assert persisted_run is not None
    assert persisted_run.status == "SUCCEEDED"
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert len(events) == len(events_before_second_prepare)
    assert streamed.index('"type":"start"') < streamed.index('"type":"data-workflow"')
    assert streamed.index('"type":"text-start"') < streamed.index('"type":"text-delta"')
    assert streamed.index('"type":"text-delta"') < streamed.index(
        '"type":"data-citations"'
    )
    assert "data: [DONE]" in streamed
    assert snapshot.values["run_id"] == result.run_id
    metadata = snapshot.metadata
    assert metadata is not None
    assert metadata.get("run_id") == str(result.run_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_conversation_memory_is_durable_and_optimistically_versioned(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        repository = SqlChatRepository(session)
        chat_session = await repository.create_session(
            title="Long-running discussion",
            scope=RetrievalScope(),
        )
        saved = await repository.save_memory(
            session_id=chat_session.id,
            summary="The discussion compares two hypotheses.",
            durable_facts=("The user prefers falsifiable claims.",),
            covered_message_count=8,
            expected_revision=0,
        )
        stale_write = await repository.save_memory(
            session_id=chat_session.id,
            summary="A stale worker must not overwrite memory.",
            durable_facts=(),
            covered_message_count=4,
            expected_revision=0,
        )

    async with factory() as restarted_session:
        persisted = await SqlChatRepository(restarted_session).get_memory(
            chat_session.id
        )

    await engine.dispose()

    assert saved is not None
    assert saved.revision == 1
    assert stale_write is None
    assert persisted == saved


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_application_persists_input_and_dispatches_before_returning(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    document_id = uuid4()
    application = ChatApplication(factory, dispatch_outbox=True)

    chat_session = await application.create_session(
        title="Durable discussion",
        scope=RetrievalScope(document_ids=(document_id,)),
    )
    run_id = await application.start(
        session_id=chat_session.id,
        question="Continue without retrieving this turn",
        retrieval_mode="off",
    )

    async with factory() as session:
        repository = SqlChatRepository(session)
        run = await repository.get_run(run_id)
        events = await repository.list_events(run_id)
        outbox = (
            await session.scalars(
                select(OutboxEventModel).where(
                    OutboxEventModel.event_type == "chat.run.requested",
                )
            )
        ).one()

    assert run is not None
    assert run.status == "QUEUED"
    assert run.question == "Continue without retrieving this turn"
    assert run.scope == RetrievalScope(document_ids=(document_id,))
    assert run.retrieval_mode == "off"
    assert run.history_message_count == 0
    assert outbox.sent_at is None
    assert outbox.payload == {"run_id": str(run_id)}
    assert events[0].event_type == "run_started"

    # This test commits through application-owned sessions, so mark its outbox
    # fixture consumed before other integration modules inspect the shared DB.
    async with factory() as cleanup_session:
        persisted_outbox = await cleanup_session.get(OutboxEventModel, outbox.id)
        assert persisted_outbox is not None
        persisted_outbox.sent_at = datetime.now(UTC)
        await cleanup_session.commit()

    async with factory() as first_session, factory() as second_session:
        first, second = await asyncio.gather(
            SqlChatRepository(first_session).claim_run(
                run_id, lease=timedelta(minutes=30)
            ),
            SqlChatRepository(second_session).claim_run(
                run_id, lease=timedelta(minutes=30)
            ),
        )

    with pytest.raises(AppError) as raised:
        await application.start(
            session_id=chat_session.id,
            question="This turn must wait",
            retrieval_mode="off",
        )

    async with factory() as cleanup_session:
        await cleanup_session.execute(
            delete(OutboxEventModel).where(OutboxEventModel.id == outbox.id)
        )
        await cleanup_session.execute(
            delete(ChatSessionModel).where(ChatSessionModel.id == chat_session.id)
        )
        await cleanup_session.commit()

    await engine.dispose()

    assert sum(item is not None for item in (first, second)) == 1
    assert raised.value.code == "CHAT_RUN_IN_PROGRESS"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_chat_run_lease_can_be_recovered(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    application = ChatApplication(factory, dispatch_outbox=True)
    chat_session = await application.create_session(
        title="Lease recovery",
        scope=RetrievalScope(),
    )
    run_id = await application.start(
        session_id=chat_session.id,
        question="Resume after a worker loss",
        retrieval_mode="off",
    )

    async with factory() as first_session:
        first_claim = await SqlChatRepository(first_session).claim_run(
            run_id, lease=timedelta(minutes=30)
        )
    assert first_claim is not None

    async with factory() as expiry_session:
        run_model = await expiry_session.get(ChatRunModel, run_id)
        assert run_model is not None
        run_model.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await expiry_session.commit()

    async with factory() as recovery_session:
        recovered = await SqlChatRepository(recovery_session).claim_run(
            run_id, lease=timedelta(minutes=30)
        )
        outbox = (
            await recovery_session.scalars(
                select(OutboxEventModel).where(
                    OutboxEventModel.event_type == "chat.run.requested",
                    OutboxEventModel.payload["run_id"].as_string() == str(run_id),
                )
            )
        ).one()
        outbox.sent_at = datetime.now(UTC)
        await recovery_session.commit()

    async with factory() as cleanup_session:
        await cleanup_session.execute(
            delete(OutboxEventModel).where(OutboxEventModel.id == outbox.id)
        )
        await cleanup_session.execute(
            delete(ChatSessionModel).where(ChatSessionModel.id == chat_session.id)
        )
        await cleanup_session.commit()

    await engine.dispose()

    assert recovered is not None
    assert recovered.status == "RUNNING"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_read_paths_release_transactions_between_sse_polls(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        repository = SqlChatRepository(session)
        chat_session = await repository.create_session(
            title="Transaction release",
            scope=RetrievalScope(),
        )
        run = await repository.start_run(
            session_id=chat_session.id,
            question="Keep polling reads short",
        )

        assert await repository.get_run(run.id) is not None
        assert not session.in_transaction()
        assert await repository.list_events(run.id)
        assert not session.in_transaction()
        assert await repository.list_messages(chat_session.id)
        assert not session.in_transaction()

        await session.execute(
            delete(ChatSessionModel).where(ChatSessionModel.id == chat_session.id)
        )
        await session.commit()
    await engine.dispose()
