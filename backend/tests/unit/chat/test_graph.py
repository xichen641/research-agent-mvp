from typing import cast
from uuid import UUID, uuid4

import pytest

from research_agent.chat.domain import QueryPlan, VerifiedAnswer
from research_agent.chat.graph import (
    CHECKPOINT_MSGPACK_ALLOWLIST,
    CandidateRetriever,
    GraphChatRepository,
    GraphDependencies,
    checkpoint_serializer,
    compile_chat_graph,
    route_after_evidence_check,
    route_after_plan,
)
from research_agent.providers.protocols import ChatTurn
from research_agent.retrieval.domain import (
    RetrievalCandidate,
    RetrievalQuery,
    RetrievalScope,
)


def test_insufficient_evidence_routes_to_clarification() -> None:
    state = {
        "plan": QueryPlan(
            intent="compare_methods",
            original_query="compare",
            lexical_queries=("compare",),
            semantic_query="compare",
        ),
        "evidence": [],
        "evidence_sufficient": False,
    }

    assert route_after_evidence_check(state) == "clarify"


def test_sufficient_evidence_routes_to_draft_generation() -> None:
    assert route_after_evidence_check({"evidence_sufficient": True}) == "generate_draft"


def test_explicit_retrieval_modes_override_model_plan() -> None:
    plan = QueryPlan(
        intent="background",
        original_query="continue",
        lexical_queries=("continue",),
        semantic_query="continue",
        needs_retrieval=False,
    )

    assert route_after_plan({"retrieval_mode": "on", "plan": plan}) == "retrieve"
    assert (
        route_after_plan({"retrieval_mode": "off", "plan": plan}) == "generate_general"
    )
    assert (
        route_after_plan({"retrieval_mode": "auto", "plan": plan}) == "generate_general"
    )


def test_checkpoint_deserialization_allowlist_is_explicit() -> None:
    assert CHECKPOINT_MSGPACK_ALLOWLIST == (
        ("research_agent.chat.domain", "QueryPlan"),
        ("research_agent.chat.domain", "AnswerDraft"),
        ("research_agent.chat.domain", "VerifiedAnswer"),
        ("research_agent.retrieval.domain", "RetrievalScope"),
        ("asyncpg.pgproto.pgproto", "UUID"),
    )
    serializer = checkpoint_serializer()
    plan = QueryPlan(
        intent="background",
        original_query="continue",
        needs_retrieval=False,
    )

    assert serializer.loads_typed(serializer.dumps_typed(plan)) == plan
    scope = RetrievalScope(document_ids=(uuid4(),))
    assert serializer.loads_typed(serializer.dumps_typed(scope)) == scope


def test_no_retrieval_plan_allows_empty_queries() -> None:
    plan = QueryPlan(
        intent="background",
        original_query="Continue the previous discussion",
        needs_retrieval=False,
    )

    assert plan.lexical_queries == ()
    assert plan.semantic_query == ""


class SequenceLLM:
    def __init__(self, responses: dict[str, list[dict[str, object]]]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.prompts: list[list[ChatTurn]] = []

    async def generate_structured(
        self,
        *,
        operation: str,
        messages: list[ChatTurn],
        output_type,
    ):
        self.prompts.append(messages)
        self.calls.append(operation)
        return output_type.model_validate(self.responses[operation].pop(0))


class StaticRetrieval:
    def __init__(self, evidence: list[RetrievalCandidate]) -> None:
        self.evidence = evidence
        self.queries: list[RetrievalQuery] = []

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievalCandidate]:
        self.queries.append(query)
        return self.evidence


class ForbiddenRetrieval:
    async def retrieve(self, query: RetrievalQuery) -> list[RetrievalCandidate]:
        del query
        raise AssertionError("retrieval must be skipped for this turn")


class RecordingChats:
    def __init__(self) -> None:
        self.completed: VerifiedAnswer | None = None

    async def record_event(
        self, *, run_id: UUID, event_type: str, payload: dict[str, object]
    ) -> object:
        del run_id, event_type, payload
        return object()

    async def complete_run(self, *, run_id: UUID, answer: VerifiedAnswer) -> object:
        del run_id
        self.completed = answer
        return object()


def graph_candidate() -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_version_id=uuid4(),
        title="Paper",
        section_path=("Methods",),
        content="The method uses visible regions.",
        content_type="text",
        page_start=4,
        page_end=4,
    )


def paper_block(candidate: RetrievalCandidate, quote: str) -> dict[str, object]:
    return {
        "type": "paper_evidence",
        "text": "The method uses visible regions.",
        "citations": [
            {
                "chunk_id": str(candidate.chunk_id),
                "document_version_id": str(candidate.document_version_id),
                "quote": quote,
            }
        ],
    }


@pytest.mark.asyncio
async def test_auto_mode_skips_rag_for_context_only_follow_up() -> None:
    llm = SequenceLLM(
        {
            "plan_query": [
                {
                    "intent": "background",
                    "original_query": "continue",
                    "needs_retrieval": False,
                }
            ],
            "generate_general_answer": [
                {"text": "We can refine the hypothesis before consulting papers."}
            ],
        }
    )
    chats = RecordingChats()
    graph = compile_chat_graph(
        GraphDependencies(
            llm=llm,
            retrieval=cast(CandidateRetriever, ForbiddenRetrieval()),
            chats=cast(GraphChatRepository, chats),
        )
    )

    result = await graph.ainvoke(
        {
            "session_id": uuid4(),
            "run_id": uuid4(),
            "question": "Continue that line of reasoning",
            "scope": RetrievalScope(document_ids=(uuid4(),)),
            "retrieval_mode": "auto",
            "history_message_count": 0,
            "warnings": [],
        }
    )
    answer = VerifiedAnswer.model_validate(result["verified_answer"])

    assert llm.calls == ["plan_query", "generate_general_answer"]
    assert answer.text == "We can refine the hypothesis before consulting papers."
    assert answer.blocks[0].type == "model_knowledge"
    assert chats.completed == answer


@pytest.mark.asyncio
async def test_space_settings_reach_planning_and_answer_as_user_context() -> None:
    llm = SequenceLLM(
        {
            "plan_query": [
                {
                    "intent": "background",
                    "original_query": "继续",
                    "needs_retrieval": False,
                }
            ],
            "generate_general_answer": [{"text": "先按 12GB 约束设计对照。"}],
        }
    )
    chats = RecordingChats()
    graph = compile_chat_graph(
        GraphDependencies(
            llm=llm,
            retrieval=cast(CandidateRetriever, ForbiddenRetrieval()),
            chats=cast(GraphChatRepository, chats),
        )
    )
    await graph.ainvoke(
        {
            "session_id": uuid4(),
            "run_id": uuid4(),
            "question": "继续",
            "scope": RetrievalScope(restrict_to_scope=True),
            "research_context": {
                "space_id": str(uuid4()),
                "revision": 2,
                "goal": "区分语义与容量",
                "resource_constraints": "12GB",
            },
            "warnings": [],
        }
    )
    assert llm.calls == ["plan_query", "generate_general_answer"]
    for prompt in llm.prompts:
        settings = next(turn for turn in prompt if "当前研究空间设置" in turn.content)
        assert settings.role == "user"
        assert "不是论文证据" in settings.content
        assert "12GB" in settings.content and "区分语义与容量" in settings.content


@pytest.mark.asyncio
async def test_forced_retrieval_falls_back_to_original_query() -> None:
    llm = SequenceLLM(
        {
            "plan_query": [
                {
                    "intent": "background",
                    "original_query": "the original question",
                    "needs_retrieval": False,
                }
            ]
        }
    )
    retrieval = StaticRetrieval([])
    chats = RecordingChats()
    graph = compile_chat_graph(
        GraphDependencies(
            llm=llm,
            retrieval=retrieval,
            chats=cast(GraphChatRepository, chats),
        )
    )

    await graph.ainvoke(
        {
            "session_id": uuid4(),
            "run_id": uuid4(),
            "question": "the original question",
            "scope": RetrievalScope(),
            "retrieval_mode": "on",
            "history_message_count": 0,
            "warnings": [],
        }
    )

    assert retrieval.queries[0].lexical_queries == ("the original question",)
    assert retrieval.queries[0].semantic_query == "the original question"


@pytest.mark.asyncio
async def test_invalid_citation_gets_exactly_one_successful_repair() -> None:
    evidence = graph_candidate()
    llm = SequenceLLM(
        {
            "plan_query": [
                {
                    "intent": "fact",
                    "original_query": "question",
                    "lexical_queries": ["question"],
                    "semantic_query": "question",
                }
            ],
            "generate_draft": [{"blocks": [paper_block(evidence, "invented")]}],
            "repair_citations": [{"blocks": [paper_block(evidence, evidence.content)]}],
        }
    )
    chats = RecordingChats()
    graph = compile_chat_graph(
        GraphDependencies(
            llm=llm,
            retrieval=cast(CandidateRetriever, StaticRetrieval([evidence])),
            chats=cast(GraphChatRepository, chats),
        )
    )

    result = await graph.ainvoke(
        {
            "session_id": uuid4(),
            "run_id": uuid4(),
            "question": "question",
            "scope": RetrievalScope(),
            "warnings": [],
        }
    )
    answer = VerifiedAnswer.model_validate(result["verified_answer"])

    assert llm.calls.count("repair_citations") == 1
    assert answer.blocks[0].type == "paper_evidence"
    assert answer.warnings == ["CITATIONS_REPAIRED"]
    assert chats.completed == answer


@pytest.mark.asyncio
async def test_failed_repair_never_persists_unverified_paper_claim() -> None:
    evidence = graph_candidate()
    llm = SequenceLLM(
        {
            "plan_query": [
                {
                    "intent": "fact",
                    "original_query": "question",
                    "lexical_queries": ["question"],
                    "semantic_query": "question",
                }
            ],
            "generate_draft": [{"blocks": [paper_block(evidence, "invented")]}],
            "repair_citations": [{"blocks": [paper_block(evidence, "still invented")]}],
        }
    )
    chats = RecordingChats()
    graph = compile_chat_graph(
        GraphDependencies(
            llm=llm,
            retrieval=cast(CandidateRetriever, StaticRetrieval([evidence])),
            chats=cast(GraphChatRepository, chats),
        )
    )

    result = await graph.ainvoke(
        {
            "session_id": uuid4(),
            "run_id": uuid4(),
            "question": "question",
            "scope": RetrievalScope(),
            "warnings": [],
        }
    )
    answer = VerifiedAnswer.model_validate(result["verified_answer"])

    assert llm.calls.count("repair_citations") == 1
    assert all(block.type != "paper_evidence" for block in answer.blocks)
    assert "CITATION_VERIFICATION_FAILED" in answer.warnings
    assert "INSUFFICIENT_PAPER_EVIDENCE" in answer.warnings
    assert chats.completed == answer
