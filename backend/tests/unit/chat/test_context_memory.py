from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from research_agent.chat.context import ConversationMemoryService
from research_agent.chat.domain import (
    ChatMessageRecord,
    ConversationMemoryRecord,
)
from research_agent.providers.protocols import ChatTurn


class MemoryLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[ChatTurn]]] = []

    async def generate_structured(
        self,
        *,
        operation: str,
        messages: list[ChatTurn],
        output_type,
    ):
        self.calls.append((operation, messages))
        return output_type.model_validate(
            {
                "summary": "The user is comparing two representation strategies.",
                "durable_facts": ["Prefer evidence-backed comparisons."],
            }
        )


class MemoryRepository:
    def __init__(self, messages: list[ChatMessageRecord]) -> None:
        self.messages = messages
        self.memory: ConversationMemoryRecord | None = None

    async def list_messages(self, session_id: UUID) -> list[ChatMessageRecord]:
        del session_id
        return self.messages

    async def get_memory(self, session_id: UUID) -> ConversationMemoryRecord | None:
        del session_id
        return self.memory

    async def save_memory(
        self,
        *,
        session_id: UUID,
        summary: str,
        durable_facts: tuple[str, ...],
        covered_message_count: int,
        expected_revision: int,
    ) -> ConversationMemoryRecord | None:
        current_revision = 0 if self.memory is None else self.memory.revision
        if current_revision != expected_revision:
            return None
        self.memory = ConversationMemoryRecord(
            session_id=session_id,
            summary=summary,
            durable_facts=durable_facts,
            covered_message_count=covered_message_count,
            revision=current_revision + 1,
            updated_at=datetime.now(UTC),
        )
        return self.memory


def message(session_id: UUID, role: str, text: str) -> ChatMessageRecord:
    return ChatMessageRecord(
        id=uuid4(),
        session_id=session_id,
        role=role,  # type: ignore[arg-type]
        content={"text": text},
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_run_memory_snapshot_does_not_adopt_a_future_summary() -> None:
    session_id = uuid4()
    repository = MemoryRepository(
        [
            message(session_id, "user", "原问题"),
            message(session_id, "assistant", "原回答"),
            message(session_id, "user", "之后的问题"),
        ]
    )
    repository.memory = ConversationMemoryRecord(
        session_id=session_id,
        summary="未来的摘要",
        durable_facts=("未来的约束",),
        covered_message_count=3,
        revision=8,
        updated_at=datetime.now(UTC),
    )
    context = await ConversationMemoryService(repository, MemoryLLM()).build_snapshot(
        session_id=session_id,
        history_message_count=2,
        snapshot={
            "summary": "旧摘要",
            "durable_facts": ["24GB"],
            "covered_message_count": 1,
            "revision": 1,
        },
    )
    assert context.memory_summary == "旧摘要"
    assert context.durable_facts == ("24GB",)
    assert [turn.content for turn in context.messages] == ["原回答"]


@pytest.mark.asyncio
async def test_context_uses_only_messages_that_precede_current_run() -> None:
    session_id = uuid4()
    repository = MemoryRepository(
        [
            message(session_id, "user", "Earlier question"),
            message(session_id, "assistant", "Earlier answer"),
            message(session_id, "user", "Current question"),
        ]
    )
    llm = MemoryLLM()
    service = ConversationMemoryService(
        repository, llm, max_context_tokens=100, recent_message_tokens=40
    )

    context = await service.build(session_id=session_id, history_message_count=2)

    assert [turn.content for turn in context.messages] == [
        "Earlier question",
        "Earlier answer",
    ]
    assert llm.calls == []
    assert repository.memory is None


@pytest.mark.asyncio
async def test_long_history_is_compacted_and_recent_turns_remain_verbatim() -> None:
    session_id = uuid4()
    repository = MemoryRepository(
        [
            message(session_id, "user", "old-question-" + "a" * 80),
            message(session_id, "assistant", "old-answer-" + "b" * 80),
            message(session_id, "user", "recent-question-" + "c" * 24),
            message(session_id, "assistant", "recent-answer-" + "d" * 24),
            message(session_id, "user", "current-question"),
        ]
    )
    llm = MemoryLLM()
    service = ConversationMemoryService(
        repository, llm, max_context_tokens=45, recent_message_tokens=25
    )

    context = await service.build(session_id=session_id, history_message_count=4)

    assert context.compacted is True
    assert context.memory_summary.startswith("The user is comparing")
    assert context.durable_facts == ("Prefer evidence-backed comparisons.",)
    assert repository.memory is not None
    assert repository.memory.covered_message_count == 2
    assert [turn.content for turn in context.messages] == [
        "recent-question-" + "c" * 24,
        "recent-answer-" + "d" * 24,
    ]
    assert [call[0] for call in llm.calls] == ["compress_conversation_memory"]
    compression_prompt = llm.calls[0][1][0].content
    assert "never cite it as paper evidence" not in compression_prompt.lower()
    assert "never present remembered paper claims as verified evidence" in (
        compression_prompt.lower()
    )
