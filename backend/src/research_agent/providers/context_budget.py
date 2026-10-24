"""Deterministic application budgets, independent of a provider tokenizer alias."""

import asyncio
import hashlib
import json
from collections import Counter
from collections.abc import Awaitable, Callable, Iterator
from time import monotonic
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_agent.providers.protocols import ChatTurn, LLMProvider, OutputT
from research_agent.shared.errors import AppError


def units(value: str) -> int:
    # UTF-8 bytes are deliberately conservative for multilingual text. This is
    # an application estimator, not a claim about a proprietary native tokenizer.
    return len(value.encode("utf-8"))


def prefix(value: str, limit: int) -> str:
    return value.encode("utf-8")[: max(0, limit)].decode("utf-8", errors="ignore")


def suffix(value: str, limit: int) -> str:
    return (
        value.encode("utf-8")[-limit:].decode("utf-8", errors="ignore")
        if limit > 0
        else ""
    )


def shorten(value: str, limit: int) -> str:
    if units(value) <= limit:
        return value
    marker = "\n[内容已缩短；原文保留]\n"
    room = max(0, limit - units(marker))
    return (
        prefix(value, room // 3) + marker + suffix(value, room - room // 3)
        if room
        else prefix(marker, limit)
    )


def segments(value: str, limit: int) -> Iterator[str]:
    if limit < 4:
        raise ValueError("segment budget must allow a UTF-8 character")
    part: list[str] = []
    size = 0
    for char in value:
        width = units(char)
        if size + width > limit:
            yield "".join(part)
            part, size = [], 0
        part.append(char)
        size += width
    if part:
        yield "".join(part)


class ContextBudgetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["context-budget-v1"] = "context-budget-v1"
    estimator: Literal["utf8-bytes-plus-envelope-v1"] = "utf8-bytes-plus-envelope-v1"
    window: int = Field(default=32768, ge=8192, le=1_000_000)
    output: int = Field(default=4096, ge=256)
    system: int = Field(default=6144, ge=512)
    question: int = Field(default=4096, ge=512)
    research: int = Field(default=2048, ge=512)
    constraints: int = Field(default=1024, ge=512)
    recent: int = Field(default=4096, ge=256)
    history: int = Field(default=2048, ge=512)
    evidence: int = Field(default=8192, ge=512)
    preparation_calls: int = Field(default=32, ge=1, le=128)
    preparation_seconds: float = Field(default=60, gt=0, le=600)
    history_pages: int = Field(default=8, ge=1, le=64)
    history_page_size: int = Field(default=32, ge=2, le=200)

    @model_validator(mode="after")
    def check_allocation(self):
        if (
            sum(
                getattr(self, name)
                for name in (
                    "output",
                    "system",
                    "question",
                    "research",
                    "constraints",
                    "recent",
                    "history",
                    "evidence",
                )
            )
            + 1024
            > self.window
        ):
            raise ValueError("context allocations plus envelope reserve exceed window")
        return self

    def output_limit(self, operation: str) -> int:
        return (
            min(self.output, 768)
            if operation in {"prepare_context_segment", "compress_conversation_memory"}
            else self.output
        )

    def check_payload(
        self, payload: dict[str, object], *, operation: str, attempt: int = 1
    ) -> dict[str, object]:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        messages = payload.get("messages", [])
        framing = 128 + (64 * len(messages) if isinstance(messages, list) else 0)
        estimate = units(raw) + framing
        output = int(str(payload.get("max_tokens", self.output_limit(operation))))
        if estimate + output > self.window:
            raise AppError(
                "LLM_CONTEXT_BUDGET_EXCEEDED",
                "模型请求超过本轮上下文预算，原始消息已保留",
            )
        return {
            "operation": operation,
            "attempt": attempt,
            "estimated_input_units": estimate,
            "output_reserved_tokens": output,
            "context_window_units": self.window,
            "estimator": self.estimator,
        }


def structured_messages(
    operation: str, messages: list[ChatTurn], output_type: type[BaseModel]
) -> list[dict[str, str]]:
    schema = json.dumps(
        output_type.model_json_schema(), ensure_ascii=False, separators=(",", ":")
    )
    return [
        {
            "role": "system",
            "content": "Return exactly one valid JSON object and no markdown. "
            + f"Operation: {operation}. Output type: {output_type.__name__}. "
            + f"The JSON must satisfy this JSON Schema: {schema}",
        },
        *[{"role": item.role, "content": item.content} for item in messages],
    ]


def request_payload(
    model: str,
    messages: list[dict[str, str]],
    *,
    policy: ContextBudgetPolicy | None = None,
    operation: str = "",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "enable_thinking": False,
    }
    if policy is not None:
        payload["max_tokens"] = policy.output_limit(operation)
    return payload


class PromptSnapshotStore(Protocol):
    async def get_prompt_snapshot(
        self, run_id: UUID, key: str
    ) -> dict[str, object] | None: ...
    async def save_prompt_snapshot(
        self, run_id: UUID, key: str, value: dict[str, object]
    ) -> dict[str, object]: ...


BudgetObserver = Callable[[dict[str, object]], Awaitable[None]]


def _snapshot_turns(snapshot: dict[str, object]) -> list[ChatTurn]:
    raw = snapshot.get("messages")
    if not isinstance(raw, list):
        raise AppError("CHAT_INPUT_SNAPSHOT_INVALID", "运行输入快照无法读取")
    turns: list[ChatTurn] = []
    for item in raw:
        if (
            not isinstance(item, dict)
            or item.get("role") not in {"system", "user", "assistant"}
            or not isinstance(item.get("content"), str)
            or not isinstance(item.get("category"), str)
        ):
            raise AppError("CHAT_INPUT_SNAPSHOT_INVALID", "运行输入快照无法读取")
        turns.append(ChatTurn(item["role"], item["content"], item["category"]))
    return turns


class BudgetedLLMProvider:
    """Fit labelled optional context and freeze each logical model request."""

    def __init__(
        self,
        provider: LLMProvider,
        policy: ContextBudgetPolicy,
        *,
        store: PromptSnapshotStore | None = None,
        run_id: UUID | None = None,
        observer: BudgetObserver | None = None,
    ):
        self.provider, self.policy = provider, policy
        self.store, self.run_id, self.observer = store, run_id, observer
        self.counts: Counter[str] = Counter()
        self.warnings: set[str] = set()
        self.preparation_count = 0
        self.deadline: float | None = None
        self.model_id = str(getattr(provider, "model_id", "test-provider"))

    async def generate_structured(
        self, *, operation: str, messages: list[ChatTurn], output_type: type[OutputT]
    ) -> OutputT:
        preparation = operation in {
            "prepare_context_segment",
            "compress_conversation_memory",
        }
        if preparation:
            if self.deadline is None:
                self.deadline = monotonic() + self.policy.preparation_seconds
            if (
                self.preparation_count >= self.policy.preparation_calls
                or monotonic() >= self.deadline
            ):
                raise AppError(
                    "CONTEXT_PREPARATION_LIMIT", "上下文分段处理达到本轮预算"
                )
            self.preparation_count += 1
        key = f"{operation}:{self.counts[operation]}"
        self.counts[operation] += 1
        schema_sha = hashlib.sha256(
            json.dumps(output_type.model_json_schema(), sort_keys=True).encode()
        ).hexdigest()
        snapshot = (
            await self.store.get_prompt_snapshot(self.run_id, key)
            if self.store and self.run_id
            else None
        )
        if snapshot is not None:
            if (
                snapshot.get("schema_sha256") != schema_sha
                or snapshot.get("model") != self.model_id
            ):
                raise AppError(
                    "CHAT_INPUT_VERSION_CHANGED",
                    "原运行的模型或输出结构已变化，请发起新提问",
                )
            fitted = _snapshot_turns(snapshot)
        else:
            remaining = {
                name: int(getattr(self.policy, name))
                for name in (
                    "system",
                    "question",
                    "research",
                    "constraints",
                    "recent",
                    "history",
                    "evidence",
                )
            }
            schema_instruction = structured_messages(operation, [], output_type)[0]
            remaining["system"] -= units(
                json.dumps(schema_instruction, ensure_ascii=False)
            )
            if remaining["system"] < 0:
                raise AppError(
                    "LLM_CONTEXT_BUDGET_EXCEEDED", "输出结构超过系统提示预算"
                )
            fitted: list[ChatTurn] = []
            # Traverse optional history from the end so recent user corrections
            # survive before older assistant prose. Preserve original order after.
            for index in range(len(messages) - 1, -1, -1):
                item = messages[index]
                category = (
                    "system"
                    if item.role == "system" and item.category == "recent"
                    else item.category
                )
                if category not in remaining:
                    category = "recent"
                cap = remaining[category]
                if units(item.content) > cap:
                    if category in {"system", "question", "evidence"}:
                        raise AppError(
                            "LLM_CONTEXT_BUDGET_EXCEEDED",
                            "必要输入尚未完成分段处理，原文已保留",
                        )
                    self.warnings.add("CONTEXT_OPTIONAL_CONTENT_SHORTENED")
                content = shorten(item.content, cap)
                remaining[category] -= units(content)
                if content:
                    fitted.append(ChatTurn(item.role, content, category))
            fitted.reverse()
            snapshot = {
                "model": self.model_id,
                "schema_sha256": schema_sha,
                "warnings": sorted(self.warnings),
                "request": request_payload(
                    self.model_id,
                    structured_messages(operation, fitted, output_type),
                    policy=self.policy,
                    operation=operation,
                ),
                "messages": [
                    {
                        "role": item.role,
                        "content": item.content,
                        "category": item.category,
                    }
                    for item in fitted
                ],
            }
            if self.store and self.run_id:
                snapshot = await self.store.save_prompt_snapshot(
                    self.run_id, key, snapshot
                )
                fitted = _snapshot_turns(snapshot)
        raw_warnings = snapshot.get("warnings")
        if isinstance(raw_warnings, list):
            self.warnings.update(item for item in raw_warnings if isinstance(item, str))
        payload = request_payload(
            self.model_id,
            structured_messages(operation, fitted, output_type),
            policy=self.policy,
            operation=operation,
        )
        if snapshot.get("request") != payload:
            raise AppError(
                "CHAT_INPUT_VERSION_CHANGED", "原运行的输入格式已变化，请发起新提问"
            )
        observation = self.policy.check_payload(
            payload,
            operation=operation,
            attempt=0,
        )
        cached_result = (
            await self.store.get_prompt_snapshot(self.run_id, f"result:{key}")
            if self.store and self.run_id
            else None
        )
        if self.observer:
            await self.observer(
                {**observation, "result_reused": cached_result is not None}
            )
        if cached_result is not None:
            return output_type.model_validate(cached_result["output"])
        request = self.provider.generate_structured(
            operation=operation, messages=fitted, output_type=output_type
        )
        if preparation:
            result = await asyncio.wait_for(
                request,
                timeout=max(0.001, (self.deadline or monotonic()) - monotonic()),
            )
        else:
            result = await request
        if self.store and self.run_id:
            saved = await self.store.save_prompt_snapshot(
                self.run_id, f"result:{key}", {"output": result.model_dump(mode="json")}
            )
            return output_type.model_validate(saved["output"])
        return result
