from typing import cast
from uuid import UUID, uuid4

import pytest

from research_agent.chat.domain import QueryPlan, VerifiedAnswer
from research_agent.chat.graph import (
    CandidateRetriever,
    GraphChatRepository,
    GraphDependencies,
    compile_chat_graph,
    route_after_evidence_check,
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


class SequenceLLM:
    def __init__(self, responses: dict[str, list[dict[str, object]]]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def generate_structured(
        self,
        *,
        operation: str,
        messages: list[ChatTurn],
        output_type,
    ):
        del messages
        self.calls.append(operation)
        return output_type.model_validate(self.responses[operation].pop(0))


class StaticRetrieval:
    def __init__(self, evidence: list[RetrievalCandidate]) -> None:
        self.evidence = evidence

    async def retrieve(self, query: RetrievalQuery) -> list[RetrievalCandidate]:
        del query
        return self.evidence


class RecordingChats:
    def __init__(self) -> None:
        self.completed: VerifiedAnswer | None = None

    async def record_event(
        self, *, run_id: UUID, event_type: str, payload: dict[str, object]
    ) -> object:
        del run_id, event_type, payload
        return object()

    async def complete_run(
        self, *, run_id: UUID, answer: VerifiedAnswer
    ) -> object:
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
            "repair_citations": [
                {"blocks": [paper_block(evidence, evidence.content)]}
            ],
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
            "repair_citations": [
                {"blocks": [paper_block(evidence, "still invented")]}
            ],
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
