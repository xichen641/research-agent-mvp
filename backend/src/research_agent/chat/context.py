from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from research_agent.chat.domain import (
    ChatMessageRecord,
    ConversationMemoryDraft,
    ConversationMemoryRecord,
)
from research_agent.chat.preparation import (
    bounded_constraints,
    critical_lines,
    project_text,
)
from research_agent.providers.context_budget import (
    BudgetedLLMProvider,
    ContextBudgetPolicy,
    segments,
    shorten,
    units,
)
from research_agent.providers.protocols import ChatTurn, LLMProvider
from research_agent.shared.errors import AppError


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
    warnings: tuple[str, ...] = ()
    critical_constraints: tuple[str, ...] = ()
    history_messages_read: int = 0


class ConversationContextProvider(Protocol):
    async def build(
        self, *, session_id: UUID, history_message_count: int
    ) -> ConversationContext: ...


class EmptyConversationContext:
    async def build(
        self, *, session_id: UUID, history_message_count: int
    ) -> ConversationContext:
        return ConversationContext()


class ConversationMemoryService:
    """Compact bounded batches under a fixed source boundary.

    Failed batches never advance coverage. A lost optimistic write never adopts
    a future summary. Memory is navigation context, never paper evidence.
    """

    def __init__(
        self,
        repository: ConversationContextRepository,
        llm: LLMProvider,
        *,
        max_context_tokens: int = 6000,
        recent_message_tokens: int = 2500,
        policy: ContextBudgetPolicy | None = None,
    ):
        if not 0 < recent_message_tokens < max_context_tokens:
            raise ValueError(
                "memory token budgets must be positive and recent smaller than context"
            )
        self._repository = repository
        self.policy = policy or ContextBudgetPolicy()
        self._llm = (
            llm
            if isinstance(llm, BudgetedLLMProvider)
            else BudgetedLLMProvider(llm, self.policy)
        )
        # Legacy setting names retained; v1.3 measures conservative UTF-8 bytes.
        self._max = min(max_context_tokens, self.policy.history + self.policy.recent)
        self._recent = min(recent_message_tokens, self.policy.recent)

    async def build(
        self, *, session_id: UUID, history_message_count: int
    ) -> ConversationContext:
        memory = await self._repository.get_memory(session_id)
        snapshot: dict[str, object] = (
            {}
            if memory is None
            else {
                "summary": memory.summary,
                "durable_facts": list(memory.durable_facts),
                "covered_message_count": memory.covered_message_count,
                "revision": memory.revision,
                "covered_through_sequence": memory.covered_through_sequence,
                "critical_constraints": list(memory.critical_constraints),
            }
        )
        return await self.build_snapshot(
            session_id=session_id,
            history_message_count=history_message_count,
            snapshot=snapshot,
        )

    async def build_snapshot(
        self,
        *,
        session_id: UUID,
        history_message_count: int,
        snapshot: dict[str, object],
    ) -> ConversationContext:
        if history_message_count < 0:
            raise ValueError("history message count must not be negative")
        warnings: list[str] = []
        covered = int(str(snapshot.get("covered_message_count", 0)))
        after = int(str(snapshot.get("covered_through_sequence", 0)))
        through = int(str(snapshot.get("history_max_sequence", history_message_count)))
        summary = str(snapshot.get("summary", ""))
        facts = tuple(_strings(snapshot.get("durable_facts")))
        critical = _strings(snapshot.get("critical_constraints"))
        if covered > history_message_count or after > through:
            covered, after, summary, facts, critical = 0, 0, "", (), []
        summary, facts = self._bound_memory(summary, facts, warnings)
        reader = getattr(self._repository, "list_history_slice", None)
        boundary = getattr(self._repository, "history_sequence_at", None)
        if (
            reader is not None
            and "history_max_sequence" not in snapshot
            and boundary is not None
        ):
            through = await boundary(session_id, history_message_count)
            after = await boundary(session_id, covered)
            snapshot = {**snapshot, "history_max_sequence": through}
        incremental = reader is not None and "history_max_sequence" in snapshot
        if incremental:
            assert reader is not None
            tail = await reader(
                session_id,
                after_sequence=after,
                through_sequence=through,
                limit=self.policy.history_page_size,
                descending=True,
            )
        else:
            # Compatibility for old snapshots/test adapters without sequence bounds.
            tail = (await self._repository.list_messages(session_id))[
                :history_message_count
            ][covered:]
        read_count = len(tail)
        needs = (
            units(summary + "\n".join(facts))
            + sum(units(_message_text(m)) for m in tail)
            > self._max
        )
        if incremental and tail and tail[0].sequence > after + 1:
            needs = True
        start = _recent_start(tail, self._recent) if needs else 0
        recent = tail[start:]
        end = recent[0].sequence - 1 if incremental and recent else through
        pending = tail[:start] if not incremental else []
        original_covered = covered
        for _ in range(self.policy.history_pages):
            if not needs:
                break
            if incremental:
                assert reader is not None
                batch = await reader(
                    session_id,
                    after_sequence=after,
                    through_sequence=end,
                    limit=self.policy.history_page_size,
                )
            else:
                batch = pending
            if incremental:
                read_count += len(batch)
            if not batch:
                break
            before_summary, before_facts = summary, facts
            transcript = "\n\n".join(
                f"[{m.sequence or covered + i + 1}:{m.id}] {m.role}: {_message_text(m)}"
                for i, m in enumerate(batch)
            )
            for m in batch:
                if m.role == "user":
                    critical.extend(critical_lines(_message_text(m), f"message:{m.id}"))
            try:
                for piece in segments(transcript, self.policy.question - 128):
                    draft = await self._llm.generate_structured(
                        operation="compress_conversation_memory",
                        messages=[
                            ChatTurn(
                                "system",
                                "Compress earlier research discussion into concise working memory. Preserve user goals, corrections, negations, decisions, hypotheses and unresolved questions. Never present remembered paper claims as verified evidence; do not invent citations. The summary is non-authoritative context.",
                            ),
                            ChatTurn(
                                "user",
                                shorten(
                                    f"Previous summary:\n{summary}\nDurable facts:\n{list(facts)}",
                                    self.policy.history,
                                ),
                                "history",
                            ),
                            ChatTurn("user", "Turns to compact:\n" + piece, "question"),
                        ],
                        output_type=ConversationMemoryDraft,
                    )
                    summary, facts = self._bound_memory(
                        draft.summary, tuple(draft.durable_facts), warnings
                    )
            except (AppError, TimeoutError):
                summary, facts = before_summary, before_facts
                warnings.append("CONTEXT_COMPACTION_FALLBACK")
                break
            covered += len(batch)
            after = batch[-1].sequence
            if not incremental:
                pending = []
                break
        if needs and ((incremental and after < end) or (not incremental and pending)):
            warnings.append("CONTEXT_HISTORY_PARTIAL")
        for m in recent:
            if m.role == "user":
                critical.extend(critical_lines(_message_text(m), f"message:{m.id}"))
        constraints = bounded_constraints(critical, self.policy.constraints - 128)
        if units("\n".join(critical)) > self.policy.constraints - 128:
            warnings.append("CONTEXT_CONSTRAINTS_PARTIAL")
        compacted = False
        if covered > original_covered and summary:
            kwargs = (
                {"covered_through_sequence": after, "critical_constraints": constraints}
                if incremental
                else {}
            )
            saved = await self._repository.save_memory(
                session_id=session_id,
                summary=summary,
                durable_facts=facts,
                covered_message_count=covered,
                expected_revision=int(str(snapshot.get("revision", 0))),
                **kwargs,
            )
            compacted = saved is not None
        turns: list[ChatTurn] = []
        room = self.policy.recent
        for m in reversed(recent):
            if room < 128:
                warnings.append("CONTEXT_HISTORY_PARTIAL")
                break
            text, notices = await project_text(
                self._llm,
                self.policy,
                _message_text(m),
                limit=room,
                label=f"message:{m.id}",
            )
            warnings.extend(notices)
            turns.append(ChatTurn(m.role, text, "recent"))
            room -= units(text)
        return ConversationContext(
            tuple(reversed(turns)),
            summary,
            facts,
            compacted,
            tuple(dict.fromkeys(warnings)),
            constraints,
            read_count,
        )

    def _bound_memory(
        self, summary: str, facts: tuple[str, ...], warnings: list[str]
    ) -> tuple[str, tuple[str, ...]]:
        cap = self.policy.history - 256
        if units(summary + "\n".join(facts)) > cap:
            warnings.append("CONTEXT_MEMORY_SHORTENED")
        fact_room = min(cap // 3, units("\n".join(facts)))
        bounded_facts = bounded_constraints(list(facts), fact_room)
        return shorten(summary, cap - units("\n".join(bounded_facts))), bounded_facts


def context_prompt_turns(context: ConversationContext) -> list[ChatTurn]:
    turns = []
    if context.memory_summary or context.durable_facts:
        turns.append(
            ChatTurn(
                "system",
                f"Conversation memory (non-authoritative; never cite it as paper evidence):\n{context.memory_summary}\nDurable user facts: {list(context.durable_facts)}",
                "history",
            )
        )
    turns.extend(context.messages)
    return turns


def _recent_start(messages: list[ChatMessageRecord], budget: int) -> int:
    used, start = 0, len(messages)
    for index in range(len(messages) - 1, -1, -1):
        cost = units(_message_text(messages[index]))
        if start < len(messages) and used + cost > budget:
            break
        used += cost
        start = index
    return start


def _message_text(message: ChatMessageRecord) -> str:
    text = message.content.get("text")
    if isinstance(text, str):
        return text
    blocks = message.content.get("blocks")
    return (
        "\n\n".join(
            str(block["text"])
            for block in blocks
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
        if isinstance(blocks, list)
        else ""
    )


def _strings(value: object) -> list[str]:
    return (
        [item for item in value if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )
