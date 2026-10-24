import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import TypeAdapter
from sqlalchemy.engine import make_url

from research_agent.chat.citations import CitationVerifier
from research_agent.chat.context import (
    ConversationContextProvider,
    EmptyConversationContext,
    context_prompt_turns,
)
from research_agent.chat.domain import (
    AnswerDraft,
    ChatGraphState,
    ChatMessageRecord,
    ChatRunEventRecord,
    GeneralAnswer,
    ModelKnowledgeBlock,
    QueryPlan,
    VerifiedAnswer,
)
from research_agent.chat.preparation import (
    bounded_constraints,
    critical_lines,
    project_text,
)
from research_agent.providers.context_budget import (
    BudgetedLLMProvider,
    ContextBudgetPolicy,
    prefix,
    units,
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
    context: ConversationContextProvider = field(
        default_factory=EmptyConversationContext
    )


CHECKPOINT_MSGPACK_ALLOWLIST = (
    ("research_agent.chat.domain", "QueryPlan"),
    ("research_agent.chat.domain", "AnswerDraft"),
    ("research_agent.chat.domain", "VerifiedAnswer"),
    ("research_agent.retrieval.domain", "RetrievalScope"),
    ("asyncpg.pgproto.pgproto", "UUID"),
)


def checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_MSGPACK_ALLOWLIST)


def route_after_evidence_check(
    state: Mapping[str, object],
) -> Literal["generate_draft", "clarify"]:
    return "generate_draft" if state.get("evidence_sufficient") is True else "clarify"


def route_after_plan(
    state: Mapping[str, object],
) -> Literal["retrieve", "generate_general"]:
    mode = state.get("retrieval_mode", "auto")
    if mode == "off":
        return "generate_general"
    if mode == "on":
        return "retrieve"
    plan = state.get("plan")
    if isinstance(plan, QueryPlan) and plan.needs_retrieval:
        return "retrieve"
    return "generate_general"


def compile_chat_graph(
    dependencies: GraphDependencies,
    *,
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph:
    llm = (
        dependencies.llm
        if isinstance(dependencies.llm, BudgetedLLMProvider)
        else BudgetedLLMProvider(dependencies.llm, ContextBudgetPolicy())
    )
    policy = llm.policy

    async def load_context(state: ChatGraphState) -> dict[str, object]:
        reader = getattr(dependencies.chats, "get_context_snapshot", None)
        cached = await reader(state["run_id"]) if reader else None
        if cached is not None:
            await dependencies.chats.record_event(
                run_id=state["run_id"],
                event_type="context_assembled",
                payload={
                    "recent_message_count": len(cached.get("context", [])),
                    "memory_present": bool(cached.get("memory_summary")),
                    "memory_compacted": False,
                    "snapshot_reused": True,
                },
            )
            return cached
        snapshot_builder = getattr(dependencies.context, "build_snapshot", None)
        memory_snapshot = state.get("memory_snapshot")
        if snapshot_builder and memory_snapshot is not None:
            context = await snapshot_builder(
                session_id=state["session_id"],
                history_message_count=state.get("history_message_count", 0),
                snapshot=memory_snapshot,
            )
        else:
            context = await dependencies.context.build(
                session_id=state["session_id"],
                history_message_count=state.get("history_message_count", 0),
            )
        turns = context_prompt_turns(context)
        warnings = list(context.warnings)
        question, notices = await project_text(
            llm,
            policy,
            state["question"],
            limit=policy.question,
            label="Current question",
        )
        warnings.extend(notices)
        critical = []
        research_context = state.get("research_context")
        if research_context is not None:
            critical.extend(
                critical_lines(
                    str(research_context.get("resource_constraints", "")),
                    "space constraints",
                )
            )
            research_text, notices = await project_text(
                llm,
                policy,
                json.dumps(research_context, ensure_ascii=False),
                limit=policy.research - 256,
                label="Research space settings",
            )
            warnings.extend(notices)
            turns.append(
                ChatTurn(
                    role="user",
                    content="当前研究空间设置，由用户填写；这是研究目标和约束，不是论文证据，也不代表实验已经完成。以此轮设置为准：\n"
                    + research_text,
                    category="research",
                )
            )
        critical.extend(context.critical_constraints)
        critical.extend(critical_lines(state["question"], "current question"))
        constraints = bounded_constraints(critical, policy.constraints - 192)
        if units("\n".join(critical)) > policy.constraints - 192:
            warnings.append("CONTEXT_CONSTRAINTS_PARTIAL")
        if constraints:
            turns.append(
                ChatTurn(
                    "user",
                    "用户原文约束（按时间顺序；冲突时采用最新更正；不是论文证据）：\n"
                    + "\n".join(constraints),
                    "constraints",
                )
            )
        await dependencies.chats.record_event(
            run_id=state["run_id"],
            event_type="context_assembled",
            payload={
                "recent_message_count": len(context.messages),
                "memory_present": bool(context.memory_summary),
                "memory_compacted": context.compacted,
                "history_messages_read": context.history_messages_read,
                "history_max_sequence": (memory_snapshot or {}).get(
                    "history_max_sequence", 0
                ),
                "warning_count": len(warnings),
            },
        )
        assembled: dict[str, object] = {
            "context": [
                {"role": turn.role, "content": turn.content, "category": turn.category}
                for turn in turns
            ],
            "question_projection": question,
            "warnings": list(dict.fromkeys(warnings)),
            "memory_summary": context.memory_summary,
            "memory_compacted": context.compacted,
        }
        writer = getattr(dependencies.chats, "save_context_snapshot", None)
        if writer:
            await writer(state["run_id"], {**assembled, "memory_compacted": False})
        return assembled

    async def plan_query(state: ChatGraphState) -> dict[str, object]:
        plan = await llm.generate_structured(
            operation="plan_query",
            messages=[
                ChatTurn(
                    role="system",
                    content=(
                        "Plan this research conversation turn. Set needs_retrieval=true "
                        "only when answering requires claims from the paper library or "
                        "the selected papers. Greetings, brainstorming, process "
                        "discussion, and follow-ups answerable from conversation context "
                        "do not require retrieval. The application, not the model, owns "
                        "the final retrieval mode and document scope."
                    ),
                ),
                *_context_turns(state),
                ChatTurn(
                    role="user",
                    content=state.get("question_projection", state["question"]),
                    category="question",
                ),
            ],
            output_type=QueryPlan,
        )
        await dependencies.chats.record_event(
            run_id=state["run_id"],
            event_type="query_planned",
            payload={
                "intent": plan.intent,
                "needs_retrieval": plan.needs_retrieval,
                "retrieval_mode": state.get("retrieval_mode", "auto"),
            },
        )
        return {"plan": plan}

    async def retrieve(state: ChatGraphState) -> dict[str, object]:
        snapshot_reader = getattr(dependencies.chats, "get_prompt_snapshot", None)
        cached_evidence = (
            await snapshot_reader(state["run_id"], "retrieved_evidence")
            if snapshot_reader
            else None
        )
        if cached_evidence is not None:
            return {
                "evidence": TypeAdapter(list[RetrievalCandidate]).validate_python(
                    cached_evidence["items"]
                ),
                "warnings": list(state.get("warnings", []))
                + list(cached_evidence.get("warnings", [])),
            }
        plan = state.get("plan")
        if plan is None:
            raise ValueError("query plan is missing")
        lexical_queries = plan.lexical_queries or (plan.original_query,)
        semantic_query = plan.semantic_query.strip() or plan.original_query
        await dependencies.chats.record_event(
            run_id=state["run_id"], event_type="retrieval_started", payload={}
        )
        evidence = await dependencies.retrieval.retrieve(
            RetrievalQuery(
                original=plan.original_query,
                lexical_queries=lexical_queries,
                semantic_query=semantic_query,
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
        bounded = _bounded_evidence(evidence, policy.evidence)
        warnings = list(state.get("warnings", []))
        if bounded != evidence:
            warnings.append("CONTEXT_EVIDENCE_SHORTENED")
        snapshot_writer = getattr(dependencies.chats, "save_prompt_snapshot", None)
        if snapshot_writer:
            await snapshot_writer(
                state["run_id"],
                "retrieved_evidence",
                {
                    "items": TypeAdapter(list[RetrievalCandidate]).dump_python(
                        bounded, mode="json"
                    ),
                    "warnings": ["CONTEXT_EVIDENCE_SHORTENED"]
                    if bounded != evidence
                    else [],
                },
            )
        return {"evidence": bounded, "warnings": warnings}

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
        await dependencies.chats.record_event(
            run_id=state["run_id"],
            event_type="answer_generation_started",
            payload={"source": "paper_evidence"},
        )
        draft = await llm.generate_structured(
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
                *_context_turns(state),
                ChatTurn(
                    role="user",
                    content=state.get("question_projection", state["question"]),
                    category="question",
                ),
                ChatTurn("user", evidence_text, "evidence"),
            ],
            output_type=AnswerDraft,
        )
        await dependencies.chats.record_event(
            run_id=state["run_id"], event_type="draft_generated", payload={}
        )
        return {"draft": draft}

    async def generate_general(state: ChatGraphState) -> dict[str, object]:
        await dependencies.chats.record_event(
            run_id=state["run_id"],
            event_type="retrieval_skipped",
            payload={"retrieval_mode": state.get("retrieval_mode", "auto")},
        )
        await dependencies.chats.record_event(
            run_id=state["run_id"],
            event_type="answer_generation_started",
            payload={"source": "model_knowledge"},
        )
        answer = await llm.generate_structured(
            operation="generate_general_answer",
            messages=[
                ChatTurn(
                    role="system",
                    content=(
                        "Continue the multi-turn research discussion without retrieving "
                        "papers. Use conversation memory only as non-authoritative "
                        "context. Do not claim that a statement came from a paper, do "
                        "not invent citations, and clearly mark uncertainty when needed."
                    ),
                ),
                *_context_turns(state),
                ChatTurn(
                    role="user",
                    content=state.get("question_projection", state["question"]),
                    category="question",
                ),
            ],
            output_type=GeneralAnswer,
        )
        await dependencies.chats.record_event(
            run_id=state["run_id"],
            event_type="draft_generated",
            payload={"source": "model_knowledge"},
        )
        return {
            "draft": AnswerDraft(
                blocks=[ModelKnowledgeBlock(text=answer.text.strip())]
            ),
            "evidence": [],
            "evidence_sufficient": False,
        }

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
            "warnings": [*state.get("warnings", []), warning],
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
            repaired_draft = await llm.generate_structured(
                operation="repair_citations",
                messages=[
                    ChatTurn(
                        role="system",
                        content=(
                            "Remove or rewrite unsupported claims. Use only the "
                            "supplied chunk IDs and verbatim quotes. Do not convert "
                            "unsupported content into paper evidence. General "
                            "background may appear only in a model_knowledge block."
                        ),
                    ),
                    ChatTurn(
                        role="user",
                        content=(f"Draft:\n{draft.model_dump_json()}"),
                        category="recent",
                    ),
                    ChatTurn(
                        "user",
                        state.get("question_projection", state["question"]),
                        "question",
                    ),
                    ChatTurn("user", _evidence_text(evidence), "evidence"),
                ],
                output_type=AnswerDraft,
            )
            verification = dependencies.verifier.verify_answer(repaired_draft, evidence)
            if verification.failed_block_indexes:
                warnings.extend(
                    [
                        "CITATION_VERIFICATION_FAILED",
                        "INSUFFICIENT_PAPER_EVIDENCE",
                    ]
                )
            else:
                warnings.append("CITATIONS_REPAIRED")

        warnings.extend(sorted(llm.warnings))
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
        await dependencies.chats.complete_run(run_id=state["run_id"], answer=answer)
        return {}

    builder = StateGraph(ChatGraphState)
    builder.add_node("load_context", load_context)
    builder.add_node("plan_query", plan_query)
    builder.add_node("retrieve", retrieve)
    builder.add_node("check_evidence", check_evidence)
    builder.add_node("generate_draft", generate_draft)
    builder.add_node("generate_general", generate_general)
    builder.add_node("clarify", clarify)
    builder.add_node("verify_citations", verify_citations)
    builder.add_node("persist_answer", persist_answer)
    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "plan_query")
    builder.add_conditional_edges(
        "plan_query",
        route_after_plan,
        {"retrieve": "retrieve", "generate_general": "generate_general"},
    )
    builder.add_edge("retrieve", "check_evidence")
    builder.add_conditional_edges(
        "check_evidence",
        route_after_evidence_check,
        {"generate_draft": "generate_draft", "clarify": "clarify"},
    )
    builder.add_edge("generate_draft", "verify_citations")
    builder.add_edge("clarify", "verify_citations")
    builder.add_edge("generate_general", "verify_citations")
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
    serializer = checkpoint_serializer()
    async with AsyncPostgresSaver.from_conn_string(
        checkpoint_url, serde=serializer
    ) as saver:
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


def _context_turns(state: ChatGraphState) -> list[ChatTurn]:
    raw_turns = state.get("context", [])
    turns: list[ChatTurn] = []
    for item in raw_turns:
        role = item.get("role")
        content = item.get("content")
        if role in {"system", "user", "assistant"} and isinstance(content, str):
            turns.append(
                ChatTurn(
                    role=role, content=content, category=item.get("category", "recent")
                )
            )
    return turns


def _bounded_evidence(
    evidence: list[RetrievalCandidate], budget: int
) -> list[RetrievalCandidate]:
    selected: list[RetrievalCandidate] = []
    remaining = budget
    for index, item in enumerate(evidence):
        header = units(_evidence_text([replace(item, content="")])) + (
            2 if selected else 0
        )
        if remaining <= header:
            break
        # Only raw prefixes are supplied to both generator and verifier. Never
        # insert a generated summary or truncation marker into citable evidence.
        content = prefix(
            item.content, max(0, remaining // (len(evidence) - index) - header)
        )
        if content:
            selected.append(replace(item, content=content))
            remaining -= header + units(content)
    return selected
