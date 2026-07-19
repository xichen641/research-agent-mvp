from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.engine import make_url

from research_agent.chat.citations import CitationVerifier
from research_agent.chat.domain import (
    AnswerDraft,
    ChatGraphState,
    ChatRunEventRecord,
    ChatMessageRecord,
    ModelKnowledgeBlock,
    QueryPlan,
    VerifiedAnswer,
)
from research_agent.providers.protocols import ChatTurn, LLMProvider
from research_agent.retrieval.domain import (
    RetrievalCandidate,
    RetrievalQuery,
)


class CandidateRetriever(Protocol):
    async def retrieve(self, query: RetrievalQuery) -> list[RetrievalCandidate]: ...


class GraphChatRepository(Protocol):
    async def record_event(
        self, *, run_id: UUID, event_type: str, payload: dict[str, object]
    ) -> ChatRunEventRecord | object: ...

    async def complete_run(
        self, *, run_id: UUID, answer: VerifiedAnswer
    ) -> ChatMessageRecord | object: ...


@dataclass(frozen=True, slots=True)
class GraphDependencies:
    llm: LLMProvider
    retrieval: CandidateRetriever
    chats: GraphChatRepository
    verifier: CitationVerifier = field(default_factory=CitationVerifier)


def route_after_evidence_check(
    state: Mapping[str, object],
) -> Literal["generate_draft", "clarify"]:
    return "generate_draft" if state.get("evidence_sufficient") is True else "clarify"


def compile_chat_graph(
    dependencies: GraphDependencies,
    *,
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph:
    async def plan_query(state: ChatGraphState) -> dict[str, object]:
        plan = await dependencies.llm.generate_structured(
            operation="plan_query",
            messages=[ChatTurn(role="user", content=state["question"])],
            output_type=QueryPlan,
        )
        await dependencies.chats.record_event(
            run_id=state["run_id"],
            event_type="query_planned",
            payload={"intent": plan.intent},
        )
        return {"plan": plan}

    async def retrieve(state: ChatGraphState) -> dict[str, object]:
        plan = state.get("plan")
        if plan is None:
            raise ValueError("query plan is missing")
        evidence = await dependencies.retrieval.retrieve(
            RetrievalQuery(
                original=plan.original_query,
                lexical_queries=plan.lexical_queries,
                semantic_query=plan.semantic_query,
                # Scope is application-owned and never accepted from model output.
                # 检索范围由应用持有，绝不采用模型输出中的范围。
                scope=state["scope"],
            )
        )
        await dependencies.chats.record_event(
            run_id=state["run_id"],
            event_type="retrieval_completed",
            payload={"candidate_count": len(evidence)},
        )
        await dependencies.chats.record_event(
            run_id=state["run_id"],
            event_type="rerank_completed",
            payload={"candidate_count": len(evidence)},
        )
        return {"evidence": evidence}

    async def check_evidence(state: ChatGraphState) -> dict[str, object]:
        evidence = state.get("evidence", [])
        plan = state.get("plan")
        if plan is None:
            raise ValueError("query plan is missing")
        intent = plan.intent
        distinct_documents = {item.document_id for item in evidence}
        sufficient = bool(evidence) and (
            intent != "compare_methods" or len(distinct_documents) >= 2
        )
        await dependencies.chats.record_event(
            run_id=state["run_id"],
            event_type="evidence_checked",
            payload={"sufficient": sufficient},
        )
        return {"evidence_sufficient": sufficient}

    async def generate_draft(state: ChatGraphState) -> dict[str, object]:
        evidence_text = _evidence_text(state.get("evidence", []))
        draft = await dependencies.llm.generate_structured(
            operation="generate_draft",
            messages=[
                ChatTurn(
                    role="system",
                    content=(
                        "Return labelled answer blocks. Paper claims must use "
                        "paper_evidence with supplied chunk IDs, document version IDs, "
                        "and verbatim quotes. General background must use "
                        "model_knowledge."
                    ),
                ),
                ChatTurn(
                    role="user",
                    content=f"Question: {state['question']}\n\nEvidence:\n{evidence_text}",
                ),
            ],
            output_type=AnswerDraft,
        )
        await dependencies.chats.record_event(
            run_id=state["run_id"], event_type="draft_generated", payload={}
        )
        return {"draft": draft}

    async def clarify(state: ChatGraphState) -> dict[str, object]:
        warning = "INSUFFICIENT_PAPER_EVIDENCE"
        await dependencies.chats.record_event(
            run_id=state["run_id"],
            event_type="clarification_required",
            payload={"warning": warning},
        )
        return {
            "draft": AnswerDraft(
                blocks=[
                    ModelKnowledgeBlock(
                        text=(
                            "The selected papers do not provide enough evidence to "
                            "answer this question. Please narrow the question or "
                            "select more papers."
                        )
                    )
                ]
            ),
            "warnings": [warning],
        }

    async def verify_citations(state: ChatGraphState) -> dict[str, object]:
        draft = state.get("draft")
        if draft is None:
            raise ValueError("answer draft is missing")
        evidence = state.get("evidence", [])
        warnings = list(state.get("warnings", []))
        verification = dependencies.verifier.verify_answer(draft, evidence)
        repaired = False
        if verification.failed_block_indexes:
            repaired = True
            repaired_draft = await dependencies.llm.generate_structured(
                operation="repair_citations",
                messages=[
                    ChatTurn(
                        role="system",
                        content=(
                            "Remove or rewrite unsupported claims. Use only the supplied "
                            "chunk IDs and verbatim quotes. Do not convert unsupported "
                            "content into paper evidence. General background may appear "
                            "only in a model_knowledge block."
                        ),
                    ),
                    ChatTurn(
                        role="user",
                        content=(
                            f"Draft:\n{draft.model_dump_json()}\n\n"
                            f"Evidence:\n{_evidence_text(evidence)}"
                        ),
                    ),
                ],
                output_type=AnswerDraft,
            )
            verification = dependencies.verifier.verify_answer(
                repaired_draft, evidence
            )
            if verification.failed_block_indexes:
                warnings.extend(
                    [
                        "CITATION_VERIFICATION_FAILED",
                        "INSUFFICIENT_PAPER_EVIDENCE",
                    ]
                )
            else:
                warnings.append("CITATIONS_REPAIRED")

        verified = VerifiedAnswer(
            blocks=verification.answer.blocks,
            warnings=_unique(warnings),
        )
        citation_count = sum(
            len(block.citations)
            for block in verified.blocks
            if block.type == "paper_evidence"
        )
        await dependencies.chats.record_event(
            run_id=state["run_id"],
            event_type="answer_verified",
            payload={"citation_count": citation_count, "repaired": repaired},
        )
        return {"verified_answer": verified}

    async def persist_answer(state: ChatGraphState) -> dict[str, object]:
        answer = state.get("verified_answer")
        if answer is None:
            raise ValueError("verified answer is missing")
        await dependencies.chats.complete_run(
            run_id=state["run_id"], answer=answer
        )
        return {}

    builder = StateGraph(ChatGraphState)
    builder.add_node("plan_query", plan_query)
    builder.add_node("retrieve", retrieve)
    builder.add_node("check_evidence", check_evidence)
    builder.add_node("generate_draft", generate_draft)
    builder.add_node("clarify", clarify)
    builder.add_node("verify_citations", verify_citations)
    builder.add_node("persist_answer", persist_answer)
    builder.add_edge(START, "plan_query")
    builder.add_edge("plan_query", "retrieve")
    builder.add_edge("retrieve", "check_evidence")
    builder.add_conditional_edges(
        "check_evidence",
        route_after_evidence_check,
        {"generate_draft": "generate_draft", "clarify": "clarify"},
    )
    builder.add_edge("generate_draft", "verify_citations")
    builder.add_edge("clarify", "verify_citations")
    builder.add_edge("verify_citations", "persist_answer")
    builder.add_edge("persist_answer", END)
    return builder.compile(checkpointer=checkpointer)


@asynccontextmanager
async def persistent_chat_graph(
    database_url: str,
    dependencies: GraphDependencies,
) -> AsyncIterator[CompiledStateGraph]:
    """Own one PostgreSQL saver for the application's graph lifecycle."""

    checkpoint_url = _checkpoint_database_url(database_url)
    async with AsyncPostgresSaver.from_conn_string(checkpoint_url) as saver:
        # The saver owns its schema migrations; application chat tables stay in Alembic.
        # Saver 自行管理检查点表迁移，应用聊天表仍由 Alembic 管理。
        await saver.setup()
        yield compile_chat_graph(dependencies, checkpointer=saver)


def _checkpoint_database_url(database_url: str) -> str:
    url = make_url(database_url)
    if url.drivername not in {"postgresql", "postgresql+asyncpg", "postgresql+psycopg"}:
        raise ValueError("LangGraph checkpointing requires PostgreSQL")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _evidence_text(evidence: list[RetrievalCandidate]) -> str:
    return "\n\n".join(
        f"chunk_id={item.chunk_id}\n"
        f"document_version_id={item.document_version_id}\n"
        f"title={item.title}\n"
        f"pages={item.page_start}-{item.page_end}\n"
        f"content={item.content}"
        for item in evidence
    )


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
