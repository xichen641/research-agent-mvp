from uuid import uuid4

import pytest
from sqlalchemy import select

from research_agent.chat.domain import ModelKnowledgeBlock, QueryPlan, VerifiedAnswer
from research_agent.chat.models import ChatRunModel
from research_agent.chat.repository import SqlChatRepository
from research_agent.config import get_settings
from research_agent.providers.context_budget import (
    BudgetedLLMProvider,
    ContextBudgetPolicy,
)
from research_agent.providers.protocols import ChatTurn
from research_agent.retrieval.domain import RetrievalScope

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_retry_retains_policy_prompt_and_sequence_boundary(db_session):
    repo = SqlChatRepository(db_session)
    chat = await repo.create_session(title="预算恢复", scope=RetrievalScope())
    first = await repo.start_run(
        session_id=chat.id, question="第一轮", client_request_id=uuid4()
    )
    await repo.complete_run(
        run_id=first.id,
        answer=VerifiedAnswer(blocks=[ModelKnowledgeBlock(text="回答")]),
    )
    original = await repo.start_run(
        session_id=chat.id, question="原问题", client_request_id=uuid4()
    )
    assert original.memory_snapshot["history_max_sequence"] == 2
    policy = original.memory_snapshot["context_budget"]
    prompt = {
        "messages": [{"role": "user", "content": "原输入", "category": "question"}],
        "model": "test",
        "schema_sha256": "test",
    }
    await repo.save_prompt_snapshot(original.id, "plan_query:0", prompt)
    assert (
        await repo.save_prompt_snapshot(original.id, "plan_query:0", {"changed": True})
        == prompt
    )
    await repo.fail_run(run_id=original.id, error_code="TEST_FAILURE")
    settings = get_settings()
    old_policy = settings.chat_context_budget
    try:
        settings.chat_context_budget = ContextBudgetPolicy(preparation_calls=1)
        retry = await repo.start_run(
            session_id=chat.id,
            question="原问题",
            client_request_id=uuid4(),
            retry_of_run_id=original.id,
        )
    finally:
        settings.chat_context_budget = old_policy
    assert retry.memory_snapshot == original.memory_snapshot
    assert retry.memory_snapshot["context_budget"] == policy
    assert await repo.get_prompt_snapshot(retry.id, "plan_query:0") == prompt
    messages = await repo.list_history_slice(
        chat.id, after_sequence=0, through_sequence=2, limit=2, descending=True
    )
    assert [item.sequence for item in messages] == [1, 2]
    assert all(item.content.get("text") != "原问题" for item in messages)
    model = await db_session.scalar(
        select(ChatRunModel).where(ChatRunModel.id == retry.id)
    )
    assert model.request_snapshots == {"plan_query:0": prompt}


async def test_memory_sequence_boundary_and_raw_constraints_round_trip(db_session):
    repo = SqlChatRepository(db_session)
    chat = await repo.create_session(title="原文约束", scope=RetrievalScope())
    first = await repo.start_run(session_id=chat.id, question="更正：12GB，尚未训练。")
    await repo.complete_run(
        run_id=first.id,
        answer=VerifiedAnswer(blocks=[ModelKnowledgeBlock(text="收到")]),
    )
    saved = await repo.save_memory(
        session_id=chat.id,
        summary="讨论摘要",
        durable_facts=(),
        covered_message_count=1,
        critical_constraints=("[source] 更正：12GB，尚未训练。",),
        expected_revision=0,
    )
    assert saved.covered_through_sequence == 1
    second = await repo.start_run(session_id=chat.id, question="继续")
    assert second.memory_snapshot["covered_through_sequence"] == 1
    assert second.memory_snapshot["critical_constraints"] == list(
        saved.critical_constraints
    )
    assert await repo.history_sequence_at(chat.id, 2) == 2
    assert (await repo.list_message_page(chat.id, limit=2))[0][-1].content[
        "text"
    ] == "继续"


async def test_completed_planning_result_is_reused_after_later_failure(db_session):
    class ChangingModel:
        model_id = "stable-alias"
        calls = 0

        async def generate_structured(self, **_):
            self.calls += 1
            return QueryPlan(
                intent="fact",
                original_query="论文原问题",
                needs_retrieval=self.calls == 1,
            )

    repo, provider, policy = (
        SqlChatRepository(db_session),
        ChangingModel(),
        ContextBudgetPolicy(),
    )
    chat = await repo.create_session(title="固定规划分支", scope=RetrievalScope())
    original = await repo.start_run(session_id=chat.id, question="论文原问题")
    first = await BudgetedLLMProvider(
        provider, policy, store=repo, run_id=original.id
    ).generate_structured(
        operation="plan_query",
        messages=[ChatTurn("user", "原上下文", "question")],
        output_type=QueryPlan,
    )
    await repo.fail_run(run_id=original.id, error_code="LATER_STAGE_FAILED")
    retry = await repo.start_run(
        session_id=chat.id,
        question="论文原问题",
        retry_of_run_id=original.id,
        client_request_id=uuid4(),
    )
    second = await BudgetedLLMProvider(
        provider, policy, store=repo, run_id=retry.id
    ).generate_structured(
        operation="plan_query",
        messages=[ChatTurn("user", "之后的新上下文", "question")],
        output_type=QueryPlan,
    )
    assert second == first
    assert second.needs_retrieval
    assert provider.calls == 1
    assert await repo.get_prompt_snapshot(
        retry.id, "plan_query:0"
    ) == await repo.get_prompt_snapshot(original.id, "plan_query:0")
