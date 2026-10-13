from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest

from research_agent.chat.domain import (
    ChatMessageRecord,
    ChatRunEventRecord,
    ChatRunRecord,
    ModelKnowledgeBlock,
    VerifiedAnswer,
    VerifiedCitation,
    VerifiedPaperEvidenceBlock,
)
from research_agent.chat.stream import (
    PersistedChatStream,
    answer_payload,
    answer_stream_events,
)


def verified_answer() -> VerifiedAnswer:
    return VerifiedAnswer(
        blocks=[
            VerifiedPaperEvidenceBlock(
                text="Paper claim",
                citations=[
                    VerifiedCitation(
                        chunk_id=uuid4(),
                        document_id=uuid4(),
                        document_version_id=uuid4(),
                        title="Paper",
                        section_path=("Methods",),
                        page_start=4,
                        page_end=5,
                        quote="verbatim quote",
                    )
                ],
            ),
            ModelKnowledgeBlock(text="Background"),
        ]
    )


def test_verified_answer_parts_follow_protocol_and_carry_clickable_sources() -> None:
    answer = verified_answer()

    events = answer_stream_events(uuid4(), answer, delta_size=5)
    parts = [
        cast(dict[str, object], payload["part"])
        for event_type, payload in events
        if event_type == "ui_part"
    ]
    types = [part["type"] for part in parts]
    payload = answer_payload(answer)
    blocks = cast(list[dict[str, object]], payload["blocks"])
    citations = cast(list[dict[str, object]], payload["citations"])

    assert types[0] == "text-start"
    assert "text-delta" in types
    assert types[-3:] == ["text-end", "data-citations", "finish"]
    assert events[-1] == ("ui_done", {})
    assert blocks[0]["citationIds"] == ["c1"]
    assert blocks[1]["type"] == "model_knowledge"
    assert citations[0]["pageStart"] == 4
    assert citations[0]["quote"] == "verbatim quote"


class CompletingStreamRepository:
    def __init__(self, answer: VerifiedAnswer) -> None:
        self.run_id = uuid4()
        self.session_id = uuid4()
        self.message_id = uuid4()
        self.answer = answer
        self.completed = False
        self.events = [self._event(1, "run_started", {})]

    async def get_run(self, run_id) -> ChatRunRecord | None:
        assert run_id == self.run_id
        return ChatRunRecord(
            id=self.run_id,
            session_id=self.session_id,
            status="SUCCEEDED" if self.completed else "RUNNING",
            error_code=None,
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC) if self.completed else None,
        )

    async def get_message(self, message_id) -> ChatMessageRecord | None:
        assert message_id == self.message_id
        return ChatMessageRecord(
            id=self.message_id,
            session_id=self.session_id,
            role="assistant",
            content=self.answer.model_dump(mode="json"),
            created_at=datetime.now(UTC),
        )

    async def list_events(
        self, run_id, *, after_sequence: int = 0
    ) -> list[ChatRunEventRecord]:
        assert run_id == self.run_id
        return [event for event in self.events if event.sequence > after_sequence]

    async def append_events_if_absent(
        self, *, run_id, marker_event_type: str, events
    ) -> bool:
        assert run_id == self.run_id
        if any(event.event_type == marker_event_type for event in self.events):
            return False
        for event_type, payload in events:
            self.events.append(self._event(len(self.events) + 1, event_type, payload))
        return True

    async def complete_after_first_poll(self, delay: float) -> None:
        del delay
        if self.completed:
            return
        self.completed = True
        self.events.append(
            self._event(
                len(self.events) + 1,
                "run_completed",
                {"message_id": str(self.message_id)},
            )
        )

    def _event(
        self, sequence: int, event_type: str, payload: dict[str, object]
    ) -> ChatRunEventRecord:
        return ChatRunEventRecord(
            run_id=self.run_id,
            sequence=sequence,
            event_type=event_type,
            payload=payload,
            created_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_running_direct_stream_persists_verified_parts_after_completion() -> None:
    repository = CompletingStreamRepository(verified_answer())
    stream = PersistedChatStream(
        repository,
        poll_interval=0.01,
        sleep=repository.complete_after_first_poll,
    )

    chunks = [chunk async for chunk in stream.iter_sse(repository.run_id)]

    output = "".join(chunks)
    assert '"type":"start"' in output
    assert '"type":"data-citations"' in output
    assert "data: [DONE]" in output
    assert sum(event.event_type == "ui_done" for event in repository.events) == 1
