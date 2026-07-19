from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from research_agent.chat.domain import RetrievalScope
from research_agent.chat.graph import GraphDependencies, persistent_chat_graph
from research_agent.chat.repository import SqlChatRepository
from research_agent.chat.service import ChatService
from research_agent.chat.stream import PersistedChatStream
from research_agent.providers.fake import FakeLLMProvider
from research_agent.retrieval.domain import RetrievalCandidate, RetrievalQuery


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
                                "document_version_id": str(
                                    source.document_version_id
                                ),
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
            result = await service.ask(
                chat_session.id, "What does the method use?"
            )

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
        streamed = "".join(
            [chunk async for chunk in stream.iter_sse(result.run_id)]
        )
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
    assert streamed.index('"type":"start"') < streamed.index(
        '"type":"data-workflow"'
    )
    assert streamed.index('"type":"text-start"') < streamed.index(
        '"type":"text-delta"'
    )
    assert streamed.index('"type":"text-delta"') < streamed.index(
        '"type":"data-citations"'
    )
    assert "data: [DONE]" in streamed
    assert snapshot.values["run_id"] == result.run_id
    metadata = snapshot.metadata
    assert metadata is not None
    assert metadata.get("run_id") == str(result.run_id)
