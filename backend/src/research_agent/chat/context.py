from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from research_agent.chat.domain import (
    ChatMessageRecord,
    ConversationMemoryDraft,
    ConversationMemoryRecord,
)
from research_agent.providers.protocols import ChatTurn, LLMProvider


class ConversationContextRepository(Protocol):
    async def list_messages(self, session_id: UUID) -> list[ChatMessageRecord]: ...

    async def get_memory(self, session_id: UUID) -> ConversationMemoryRecord | None: ...

    async def save_memory(
        self,
        *,
        session_id: UUID,
        summary: str,
        durable_facts: tuple[str, ...],
        covered_message_count: int,
        expected_revision: int,
    ) -> ConversationMemoryRecord | None: ...


@dataclass(frozen=True, slots=True)
class ConversationContext:
    messages: tuple[ChatTurn, ...] = ()
    memory_summary: str = ""
    durable_facts: tuple[str, ...] = ()
    compacted: bool = False


class ConversationContextProvider(Protocol):
    async def build(
        self, *, session_id: UUID, history_message_count: int
    ) -> ConversationContext: ...


class EmptyConversationContext:
    async def build(
        self, *, session_id: UUID, history_message_count: int
    ) -> ConversationContext:
        del session_id, history_message_count
        return ConversationContext()


class ConversationMemoryService:
    """Build a bounded prompt context and durably compact older turns.

    Memory is a navigation aid, not paper evidence. Citation-bearing paper claims
    still have to pass through the retrieval and citation-verification path.
    """

    def __init__(
        self,
        repository: ConversationContextRepository,
        llm: LLMProvider,
        *,
        max_context_tokens: int = 6_000,
        recent_message_tokens: int = 2_500,
    ) -> None:
        if max_context_tokens <= 0 or recent_message_tokens <= 0:
            raise ValueError("memory token budgets must be positive")
        if recent_message_tokens >= max_context_tokens:
            raise ValueError(
                "recent message budget must be smaller than context budget"
            )
        self._repository = repository
        self._llm = llm
        self._max_context_tokens = max_context_tokens
        self._recent_message_tokens = recent_message_tokens

    async def build(
        self, *, session_id: UUID, history_message_count: int
    ) -> ConversationContext:
        if history_message_count < 0:
            raise ValueError("history message count must not be negative")
        all_messages = await self._repository.list_messages(session_id)
        history = all_messages[:history_message_count]

        for _ in range(3):
            memory = await self._repository.get_memory(session_id)
            covered_count = 0 if memory is None else memory.covered_message_count
            if covered_count > len(history):
                covered_count = 0
                memory = None
            unsummarized = history[covered_count:]
            summary = "" if memory is None else memory.summary
            durable_facts = () if memory is None else memory.durable_facts
            if not self._needs_compaction(summary, durable_facts, unsummarized):
                return ConversationContext(
                    messages=tuple(_chat_turn(item) for item in unsummarized),
                    memory_summary=summary,
                    durable_facts=durable_facts,
                )

            recent_start = _recent_start(unsummarized, self._recent_message_tokens)
            older = unsummarized[:recent_start]
            if not older:
                return ConversationContext(
                    messages=tuple(_chat_turn(item) for item in unsummarized),
                    memory_summary=summary,
                    durable_facts=durable_facts,
                )

            draft = await self._compress(
                previous_summary=summary,
                durable_facts=durable_facts,
                messages=older,
            )
            saved = await self._repository.save_memory(
                session_id=session_id,
                summary=draft.summary,
                durable_facts=tuple(item[:1_000] for item in draft.durable_facts),
                covered_message_count=covered_count + len(older),
                expected_revision=0 if memory is None else memory.revision,
            )
            if saved is not None:
                return ConversationContext(
                    messages=tuple(
                        _chat_turn(item) for item in unsummarized[recent_start:]
                    ),
                    memory_summary=saved.summary,
                    durable_facts=saved.durable_facts,
                    compacted=True,
                )

        # Another worker won every optimistic update. Re-read its durable result
        # instead of overwriting it or failing the user turn.
        memory = await self._repository.get_memory(session_id)
        covered_count = (
            0 if memory is None else min(memory.covered_message_count, len(history))
        )
        return ConversationContext(
            messages=tuple(_chat_turn(item) for item in history[covered_count:]),
            memory_summary="" if memory is None else memory.summary,
            durable_facts=() if memory is None else memory.durable_facts,
        )

    async def build_snapshot(
        self,
        *,
        session_id: UUID,
        history_message_count: int,
        snapshot: dict[str, object],
    ) -> ConversationContext:
        """Use the memory projection captured at submission, never a later revision."""
        history = (await self._repository.list_messages(session_id))[
            :history_message_count
        ]
        covered = int(str(snapshot.get("covered_message_count", 0)))
        summary = str(snapshot.get("summary", ""))
        raw_facts = snapshot.get("durable_facts", [])
        facts = (
            tuple(item for item in raw_facts if isinstance(item, str))
            if isinstance(raw_facts, list)
            else ()
        )
        if covered > len(history):
            covered, summary, facts = 0, "", ()
        recent = history[covered:]
        if not self._needs_compaction(summary, facts, recent):
            return ConversationContext(
                messages=tuple(_chat_turn(item) for item in recent),
                memory_summary=summary,
                durable_facts=facts,
            )
        start = _recent_start(recent, self._recent_message_tokens)
        if start == 0:
            return ConversationContext(
                messages=tuple(_chat_turn(item) for item in recent),
                memory_summary=summary,
                durable_facts=facts,
            )
        draft = await self._compress(
            previous_summary=summary, durable_facts=facts, messages=recent[:start]
        )
        saved = await self._repository.save_memory(
            session_id=session_id,
            summary=draft.summary,
            durable_facts=tuple(draft.durable_facts),
            covered_message_count=covered + start,
            expected_revision=int(str(snapshot.get("revision", 0))),
        )
        return ConversationContext(
            messages=tuple(_chat_turn(item) for item in recent[start:]),
            memory_summary=draft.summary,
            durable_facts=tuple(draft.durable_facts),
            compacted=saved is not None,
        )

    def _needs_compaction(
        self,
        summary: str,
        durable_facts: tuple[str, ...],
        messages: list[ChatMessageRecord],
    ) -> bool:
        text = "\n".join(
            [summary, *durable_facts, *(_message_text(item) for item in messages)]
        )
        return _estimate_tokens(text) > self._max_context_tokens

    async def _compress(
        self,
        *,
        previous_summary: str,
        durable_facts: tuple[str, ...],
        messages: list[ChatMessageRecord],
    ) -> ConversationMemoryDraft:
        transcript = "\n\n".join(
            f"{item.role}: {_message_text(item)}" for item in messages
        )
        return await self._llm.generate_structured(
            operation="compress_conversation_memory",
            messages=[
                ChatTurn(
                    role="system",
                    content=(
                        "Compress earlier research discussion into a concise working "
                        "memory. Preserve the user's goals, definitions, decisions, "
                        "hypotheses, preferences, and unresolved questions. Never "
                        "present remembered paper claims as verified evidence and do "
                        "not invent citations. The summary is non-authoritative context."
                    ),
                ),
                ChatTurn(
                    role="user",
                    content=(
                        f"Previous summary:\n{previous_summary or '(none)'}\n\n"
                        f"Durable facts:\n{list(durable_facts)}\n\n"
                        f"Turns to compact:\n{transcript}"
                    ),
                ),
            ],
            output_type=ConversationMemoryDraft,
        )


def context_prompt_turns(context: ConversationContext) -> list[ChatTurn]:
    turns: list[ChatTurn] = []
    if context.memory_summary or context.durable_facts:
        turns.append(
            ChatTurn(
                role="system",
                content=(
                    "Conversation memory (non-authoritative; never cite it as paper "
                    f"evidence):\n{context.memory_summary}\n"
                    f"Durable user facts: {list(context.durable_facts)}"
                ),
            )
        )
    turns.extend(context.messages)
    return turns


def _recent_start(messages: list[ChatMessageRecord], token_budget: int) -> int:
    used = 0
    start = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        tokens = _estimate_tokens(_message_text(messages[index]))
        if start < len(messages) and used + tokens > token_budget:
            break
        used += tokens
        start = index
    return start


def _chat_turn(message: ChatMessageRecord) -> ChatTurn:
    return ChatTurn(role=message.role, content=_message_text(message)[:20_000])


def _message_text(message: ChatMessageRecord) -> str:
    text = message.content.get("text")
    if isinstance(text, str):
        return text
    blocks = message.content.get("blocks")
    if not isinstance(blocks, list):
        return ""
    values: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_text = block.get("text")
        if isinstance(block_text, str):
            values.append(block_text)
    return "\n\n".join(values)


def _estimate_tokens(text: str) -> int:
    # Deterministic conservative approximation suitable for provider-independent
    # budgeting; provider-native tokenizers can replace it behind this service.
    return max(1, (len(text) + 3) // 4)
