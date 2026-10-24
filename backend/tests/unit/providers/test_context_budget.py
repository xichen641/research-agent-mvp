import copy
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from research_agent.providers.context_budget import (
    BudgetedLLMProvider,
    ContextBudgetPolicy,
    request_payload,
    segments,
    structured_messages,
    units,
)
from research_agent.providers.protocols import ChatTurn
from research_agent.providers.qwen_llm import QwenLLMProvider
from research_agent.shared.errors import AppError


class Result(BaseModel):
    text: str


class SnapshotStore:
    def __init__(self):
        self.values = {}

    async def get_prompt_snapshot(self, run_id, key):
        return copy.deepcopy(self.values.get((run_id, key)))

    async def save_prompt_snapshot(self, run_id, key, value):
        return copy.deepcopy(self.values.setdefault((run_id, key), value))


def test_utf8_segments_preserve_every_character_and_enforce_bytes():
    text = '中文🙂\\"x' * 100
    parts = list(segments(text, 13))
    assert "".join(parts) == text
    assert all(units(part) <= 13 for part in parts)
    assert units("中文") == 6


def test_policy_rejects_overallocated_context():
    with pytest.raises(ValidationError):
        ContextBudgetPolicy(window=8192)


@pytest.mark.asyncio
async def test_actual_http_attempts_include_schema_repair_and_native_output_reserve():
    policy = ContextBudgetPolicy()
    client, observe = AsyncMock(), AsyncMock()
    client.post.side_effect = [
        httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}),
        httpx.Response(
            200, json={"choices": [{"message": {"content": '{"text":"ok"}'}}]}
        ),
    ]
    provider = QwenLLMProvider(
        client=client,
        base_url="https://example.org",
        api_key="private-key",
        model_id="model",
        context_budget=policy,
        budget_observer=observe,
    )
    result = await provider.generate_structured(
        operation="plan_query",
        messages=[ChatTurn("user", "private中文")],
        output_type=Result,
    )
    assert result.text == "ok"
    assert [call.args[0]["attempt"] for call in observe.await_args_list] == [1, 2]
    for call in client.post.await_args_list:
        payload = call.kwargs["json"]
        assert payload["max_tokens"] == policy.output
        measured = policy.check_payload(payload, operation="plan_query")
        assert measured["estimated_input_units"] + policy.output <= policy.window
    assert (
        "schema validation"
        in client.post.await_args_list[1].kwargs["json"]["messages"][-1]["content"]
    )
    assert "private" not in json.dumps(
        [call.args[0] for call in observe.await_args_list]
    )


@pytest.mark.asyncio
async def test_overlong_request_is_rejected_before_http():
    client = AsyncMock()
    provider = QwenLLMProvider(
        client=client,
        base_url="https://example.org",
        api_key="private",
        model_id="model",
        context_budget=ContextBudgetPolicy(),
    )
    with pytest.raises(AppError, match="预算"):
        await provider.generate_structured(
            operation="test",
            messages=[ChatTurn("user", "中" * 20000)],
            output_type=Result,
        )
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovered_request_uses_immutable_prompt_and_checks_schema_and_model():
    provider, store, run_id = AsyncMock(), SnapshotStore(), uuid4()
    provider.model_id = "stable-model"
    provider.generate_structured.return_value = Result(text="ok")
    for text in ("original", "future-memory"):
        wrapper = BudgetedLLMProvider(
            provider, ContextBudgetPolicy(), store=store, run_id=run_id
        )
        await wrapper.generate_structured(
            operation="plan_query",
            messages=[ChatTurn("user", text, "question")],
            output_type=Result,
        )
    assert [
        call.kwargs["messages"][0].content
        for call in provider.generate_structured.await_args_list
    ] == ["original"]
    provider.model_id = "changed-model"
    with pytest.raises(AppError) as error:
        await BudgetedLLMProvider(
            provider, ContextBudgetPolicy(), store=store, run_id=run_id
        ).generate_structured(operation="plan_query", messages=[], output_type=Result)
    assert error.value.code == "CHAT_INPUT_VERSION_CHANGED"


@pytest.mark.asyncio
async def test_optional_history_is_bounded_without_displacing_current_question():
    policy, provider = ContextBudgetPolicy(), AsyncMock()
    provider.model_id = "model"
    provider.generate_structured.return_value = Result(text="ok")
    wrapper = BudgetedLLMProvider(provider, policy)
    await wrapper.generate_structured(
        operation="plan_query",
        messages=[
            ChatTurn("system", "system"),
            ChatTurn("assistant", "旧" * 20000),
            ChatTurn("user", "更正：12GB", "constraints"),
            ChatTurn("user", "current", "question"),
        ],
        output_type=Result,
    )
    turns = provider.generate_structured.await_args.kwargs["messages"]
    assert turns[-1].content == "current"
    assert "12GB" in turns[-2].content
    assert units(turns[1].content) <= policy.recent
    assert wrapper.warnings == {"CONTEXT_OPTIONAL_CONTENT_SHORTENED"}
    policy.check_payload(
        request_payload(
            "model", structured_messages("plan_query", turns, Result), policy=policy
        ),
        operation="plan_query",
    )
