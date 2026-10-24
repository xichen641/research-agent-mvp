from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from research_agent.chat.context import ConversationMemoryService, context_prompt_turns
from research_agent.chat.domain import ChatMessageRecord, ConversationMemoryRecord
from research_agent.chat.graph import (
    GraphDependencies,
    _bounded_evidence,
    _evidence_text,
    compile_chat_graph,
)
from research_agent.chat.preparation import (
    bounded_constraints,
    critical_lines,
    project_text,
)
from research_agent.providers.context_budget import (
    BudgetedLLMProvider,
    ContextBudgetPolicy,
    units,
)
from research_agent.retrieval.domain import RetrievalCandidate, RetrievalScope
from research_agent.shared.errors import AppError


class LLM:
    def __init__(self, *, fail=False, oversized=False):
        self.calls = []
        self.fail, self.oversized = fail, oversized

    async def generate_structured(self, *, operation, messages, output_type):
        self.calls.append((operation, messages))
        if self.fail:
            raise AppError("LLM_UNAVAILABLE", "test")
        if operation == "prepare_context_segment":
            return output_type(text="分段上下文")
        return output_type(
            summary="记忆摘要" * (1200 if self.oversized else 1),
            durable_facts=["实验尚未执行"],
        )


class Repository:
    def __init__(self):
        self.session_id, self.messages, self.memory, self.reads = uuid4(), [], None, []

    def add(self, text, role="user"):
        self.messages.append(
            ChatMessageRecord(
                uuid4(),
                self.session_id,
                role,
                {"text": text},
                datetime.now(UTC),
                len(self.messages) + 1,
            )
        )

    async def list_messages(self, _):
        raise AssertionError("production context must not read all history")

    async def list_history_slice(
        self, _, *, after_sequence, through_sequence, limit, descending=False
    ):
        self.reads.append((after_sequence, through_sequence, limit))
        rows = [
            m for m in self.messages if after_sequence < m.sequence <= through_sequence
        ]
        return rows[-limit:] if descending else rows[:limit]

    async def save_memory(self, **values):
        if values.pop("expected_revision") != (
            self.memory.revision if self.memory else 0
        ):
            return None
        self.memory = ConversationMemoryRecord(
            **values,
            revision=1 if self.memory is None else self.memory.revision + 1,
            updated_at=datetime.now(UTC),
        )
        return self.memory

    def snapshot(self):
        memory = self.memory
        return {
            "history_max_sequence": len(self.messages),
            "summary": memory.summary if memory else "",
            "durable_facts": list(memory.durable_facts) if memory else [],
            "critical_constraints": list(memory.critical_constraints) if memory else [],
            "covered_message_count": memory.covered_message_count if memory else 0,
            "covered_through_sequence": memory.covered_through_sequence
            if memory
            else 0,
            "revision": memory.revision if memory else 0,
        }


@pytest.mark.asyncio
async def test_two_compactions_keep_latest_raw_correction_and_fixed_history_boundary():
    repo, llm = Repository(), LLM()
    for cycle in range(2):
        repo.add("旧讨论。" * 150)
        repo.add(
            "更正：显存限制改为12GB，基线为PromptSG，实验尚未执行。"
            if cycle
            else "显存24GB。"
        )
        snapshot = repo.snapshot()
        service = ConversationMemoryService(
            repo, llm, max_context_tokens=800, recent_message_tokens=256
        )
        context = await service.build_snapshot(
            session_id=repo.session_id,
            history_message_count=len(repo.messages),
            snapshot=snapshot,
        )
        assert context.compacted
    assert repo.memory.revision == 2
    assert "12GB" in "\n".join(context.critical_constraints)
    assert "论文证据" not in repo.memory.summary
    assert "旧讨论。" * 150 == repo.messages[0].content["text"]
    repo.add("未来更正：显存48GB。")
    old = await service.build_snapshot(
        session_id=repo.session_id, history_message_count=4, snapshot=snapshot
    )
    assert "48GB" not in str(old)
    assert all(end <= 4 for _, end, _ in repo.reads)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail,oversized", [(True, False), (False, True)])
async def test_huge_single_message_and_failed_or_oversized_summary_are_bounded(
    fail, oversized
):
    repo, llm, policy = (
        Repository(),
        LLM(fail=fail, oversized=oversized),
        ContextBudgetPolicy(),
    )
    raw = "中文🙂" * 3000 + "。更正：限制12GB，尚未训练。"
    repo.add(raw)
    repo.add("继续讨论")
    context = await ConversationMemoryService(
        repo, llm, max_context_tokens=800, recent_message_tokens=256
    ).build_snapshot(
        session_id=repo.session_id, history_message_count=2, snapshot=repo.snapshot()
    )
    assert all(
        units(turn.content) <= policy.history
        for turn in context_prompt_turns(context)
        if turn.category == "history"
    )
    assert "12GB" in str(context.critical_constraints)
    assert repo.messages[0].content["text"] == raw
    if fail:
        assert repo.memory is None
        assert "CONTEXT_COMPACTION_FALLBACK" in context.warnings
        assert len(llm.calls) == 1
    else:
        assert "CONTEXT_MEMORY_SHORTENED" in context.warnings
        assert repo.memory.covered_through_sequence == 1


@pytest.mark.asyncio
async def test_preparation_call_cap_has_explicit_fallback_and_retains_tail_correction():
    policy, provider = ContextBudgetPolicy(preparation_calls=1), LLM()
    raw = "讨论" * 10000 + "。更正：显存12GB，尚未训练。"
    projection, warnings = await project_text(
        BudgetedLLMProvider(provider, policy),
        policy,
        raw,
        limit=policy.question,
        label="question",
    )
    assert units(projection) <= policy.question
    assert "12GB" in projection
    assert warnings == ["CONTEXT_PREPARATION_FALLBACK"]
    assert len(provider.calls) == 1
    assert "12GB" in str(bounded_constraints(critical_lines(raw, "original"), 512))


@pytest.mark.asyncio
async def test_history_read_and_coverage_are_bounded_when_backlog_is_large():
    repo, policy = (
        Repository(),
        ContextBudgetPolicy(history_pages=1, history_page_size=2),
    )
    for _ in range(20):
        repo.add("长期讨论。" * 100)
    context = await ConversationMemoryService(
        repo, LLM(), policy=policy, max_context_tokens=800, recent_message_tokens=256
    ).build_snapshot(
        session_id=repo.session_id, history_message_count=20, snapshot=repo.snapshot()
    )
    assert len(repo.reads) == 2
    assert context.history_messages_read == 4
    assert repo.memory.covered_message_count == 2
    assert repo.memory.covered_through_sequence == 2
    assert "CONTEXT_HISTORY_PARTIAL" in context.warnings


def test_evidence_budget_uses_only_verbatim_prefixes_and_keeps_multiple_documents():
    original = RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_version_id=uuid4(),
        title="论文",
        section_path=(),
        content="论文原文🙂" * 2000,
        content_type="text",
        page_start=1,
        page_end=1,
        rerank_score=1,
    )
    sources = [original, replace(original, document_id=uuid4(), chunk_id=uuid4())]
    bounded = _bounded_evidence(sources, 2000)
    assert len(bounded) == 2
    assert units(_evidence_text(bounded)) <= 2000
    assert all(original.content.startswith(item.content) for item in bounded)
    assert original.content == "论文原文🙂" * 2000


@pytest.mark.asyncio
async def test_graph_segments_current_question_and_reuses_frozen_assembled_input():
    class GraphLLM(LLM):
        async def generate_structured(self, *, operation, messages, output_type):
            if operation == "plan_query":
                self.calls.append((operation, messages))
                return output_type(
                    intent="background",
                    original_query="continue",
                    needs_retrieval=False,
                )
            if operation == "generate_general_answer":
                self.calls.append((operation, messages))
                return output_type(text="当前约束12GB，尚未训练。")
            return await super().generate_structured(
                operation=operation, messages=messages, output_type=output_type
            )

    class Chats:
        cached = None

        async def get_context_snapshot(self, _):
            return self.cached

        async def save_context_snapshot(self, _, value):
            if self.cached is None:
                self.cached = value

        async def record_event(self, **_):
            pass

        async def complete_run(self, **_):
            pass

    class Retrieval:
        async def retrieve(self, _):
            raise AssertionError("retrieval must stay off")

    raw = "历史讨论。" * 3000 + "更正：当前显存12GB，尚未训练。"
    chats, provider = Chats(), GraphLLM()
    graph = compile_chat_graph(GraphDependencies(provider, Retrieval(), chats))
    state = {
        "session_id": uuid4(),
        "run_id": uuid4(),
        "question": raw,
        "scope": RetrievalScope(),
        "retrieval_mode": "off",
        "history_message_count": 0,
    }
    result = await graph.ainvoke(state)
    assert "CONTEXT_INPUT_SEGMENTED" in result["verified_answer"].warnings
    plan_turns = next(
        turns for operation, turns in provider.calls if operation == "plan_query"
    )
    assert "12GB" in next(
        turn.content for turn in plan_turns if turn.category == "constraints"
    )
    assert units(result["question_projection"]) <= ContextBudgetPolicy().question
    assert state["question"] == raw
    provider.calls.clear()
    await graph.ainvoke({**state, "question": "之后变化的问题"})
    assert not any(
        operation == "prepare_context_segment" for operation, _ in provider.calls
    )
    assert (
        next(turns for operation, turns in provider.calls if operation == "plan_query")
        == plan_turns
    )
