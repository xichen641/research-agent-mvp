import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import json
from time import monotonic
from typing import Protocol
from uuid import UUID

from research_agent.chat.domain import (
    ChatMessageRecord,
    ChatRunEventRecord,
    ChatRunRecord,
    VerifiedAnswer,
)
from research_agent.shared.errors import AppError


Sleep = Callable[[float], Awaitable[object]]
UIPart = dict[str, object]


class ChatStreamRepository(Protocol):
    async def get_run(self, run_id: UUID) -> ChatRunRecord | None: ...

    async def get_message(self, message_id: UUID) -> ChatMessageRecord | None: ...

    async def list_events(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> list[ChatRunEventRecord]: ...

    async def append_events_if_absent(
        self,
        *,
        run_id: UUID,
        marker_event_type: str,
        events: list[tuple[str, dict[str, object]]],
    ) -> bool: ...


class ChatStream(Protocol):
    async def prepare(self, run_id: UUID) -> None: ...

    def iter_sse(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> AsyncIterator[str]: ...


class PersistedChatStream:
    def __init__(
        self,
        repository: ChatStreamRepository,
        *,
        poll_interval: float = 0.25,
        keepalive_interval: float = 15.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if poll_interval <= 0 or keepalive_interval <= 0:
            raise ValueError("stream intervals must be positive")
        self._repository = repository
        self._poll_interval = poll_interval
        self._keepalive_interval = keepalive_interval
        self._sleep = sleep

    async def prepare(self, run_id: UUID) -> None:
        run = await self._repository.get_run(run_id)
        if run is None:
            raise AppError("CHAT_RUN_NOT_FOUND", "Chat run was not found")
        events = await self._repository.list_events(run_id)
        if any(event.event_type == "ui_done" for event in events):
            return
        if run.status == "SUCCEEDED":
            answer = await self._completed_answer(events)
            await self._repository.append_events_if_absent(
                run_id=run_id,
                marker_event_type="ui_done",
                events=answer_stream_events(run_id, answer),
            )
        elif run.status == "FAILED":
            await self._repository.append_events_if_absent(
                run_id=run_id,
                marker_event_type="ui_done",
                events=failed_stream_events(run_id, run.error_code),
            )

    async def _completed_answer(
        self, events: list[ChatRunEventRecord]
    ) -> VerifiedAnswer:
        completed = next(
            (
                event
                for event in reversed(events)
                if event.event_type == "run_completed"
            ),
            None,
        )
        raw_message_id = (
            None if completed is None else completed.payload.get("message_id")
        )
        try:
            message_id = UUID(str(raw_message_id))
        except (TypeError, ValueError):
            raise AppError(
                "CHAT_STREAM_CORRUPT",
                "Verified chat answer could not be loaded",
            ) from None
        message = await self._repository.get_message(message_id)
        if message is None or message.role != "assistant":
            raise AppError(
                "CHAT_STREAM_CORRUPT",
                "Verified chat answer could not be loaded",
            )
        try:
            return VerifiedAnswer.model_validate(message.content)
        except ValueError:
            raise AppError(
                "CHAT_STREAM_CORRUPT",
                "Verified chat answer could not be loaded",
            ) from None

    async def iter_sse(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> AsyncIterator[str]:
        existing = await self._repository.list_events(run_id)
        done = next(
            (event for event in existing if event.event_type == "ui_done"), None
        )
        if done is not None and done.sequence <= after_sequence:
            return

        cursor = after_sequence
        last_output = monotonic()
        while True:
            # A direct POST starts streaming while the graph is still RUNNING.
            # Re-check terminal state so verified UI parts are persisted as soon
            # as the background run completes.
            await self.prepare(run_id)
            events = await self._repository.list_events(run_id, after_sequence=cursor)
            for event in events:
                cursor = event.sequence
                payload = event_to_ui_part(event, run_id)
                if payload is None:
                    continue
                last_output = monotonic()
                if isinstance(payload, str):
                    if payload == "[DONE]":
                        yield sse_done(event.sequence)
                        return
                    continue
                yield sse_part(event.sequence, payload)
            if monotonic() - last_output >= self._keepalive_interval:
                yield ": keepalive\n\n"
                last_output = monotonic()
            await self._sleep(self._poll_interval)


def answer_stream_events(
    run_id: UUID,
    answer: VerifiedAnswer,
    *,
    delta_size: int = 256,
) -> list[tuple[str, dict[str, object]]]:
    if delta_size <= 0:
        raise ValueError("delta size must be positive")
    text_id = f"text-{run_id}"
    display_text = answer.text or _insufficient_message(answer)
    parts: list[UIPart] = [
        {"type": "text-start", "id": text_id},
        *[
            {"type": "text-delta", "id": text_id, "delta": delta}
            for delta in _chunks(display_text, delta_size)
        ],
        {"type": "text-end", "id": text_id},
        {"type": "data-citations", "data": answer_payload(answer)},
        {"type": "finish"},
    ]
    events: list[tuple[str, dict[str, object]]] = [
        ("ui_part", {"part": part}) for part in parts
    ]
    events.append(("ui_done", {}))
    return events


def failed_stream_events(
    run_id: UUID,
    error_code: str | None,
) -> list[tuple[str, dict[str, object]]]:
    text_id = f"text-{run_id}"
    parts: list[UIPart] = [
        {"type": "text-start", "id": text_id},
        {
            "type": "text-delta",
            "id": text_id,
            "delta": "The answer could not be generated. Please try again.",
        },
        {"type": "text-end", "id": text_id},
        {
            "type": "data-citations",
            "data": {
                "blocks": [],
                "citations": [],
                "warnings": [error_code or "CHAT_RUN_FAILED"],
            },
        },
        {"type": "finish"},
    ]
    events: list[tuple[str, dict[str, object]]] = [
        ("ui_part", {"part": part}) for part in parts
    ]
    events.append(("ui_done", {}))
    return events


def event_to_ui_part(
    event: ChatRunEventRecord,
    run_id: UUID,
) -> UIPart | str | None:
    workflow_stages = {
        "query_planned": "plan",
        "retrieval_completed": "retrieval",
        "rerank_completed": "rerank",
        "answer_verified": "citation_verification",
    }
    if event.event_type == "run_started":
        return {"type": "start", "messageId": str(run_id)}
    if stage := workflow_stages.get(event.event_type):
        return {
            "type": "data-workflow",
            "data": {"stage": stage, "status": "completed"},
        }
    if event.event_type == "answer_generation_started":
        return {
            "type": "data-workflow",
            "data": {"stage": "answer", "status": "running"},
        }
    if event.event_type == "run_failed":
        return {
            "type": "data-workflow",
            "data": {
                "stage": "answer",
                "status": "failed",
                "errorCode": event.payload.get("error_code", "CHAT_RUN_FAILED"),
            },
        }
    if event.event_type == "ui_part":
        part = event.payload.get("part")
        return part if isinstance(part, dict) else None
    if event.event_type == "ui_done":
        return "[DONE]"
    return None


def answer_payload(answer: VerifiedAnswer) -> dict[str, object]:
    citations: list[dict[str, object]] = []
    blocks: list[dict[str, object]] = []
    citation_number = 0
    for block in answer.blocks:
        if block.type == "model_knowledge":
            blocks.append({"type": block.type, "text": block.text})
            continue
        citation_ids: list[str] = []
        for citation in block.citations:
            citation_number += 1
            citation_id = f"c{citation_number}"
            citation_ids.append(citation_id)
            citations.append(
                {
                    "id": citation_id,
                    "chunkId": str(citation.chunk_id),
                    "documentId": str(citation.document_id),
                    "documentVersionId": str(citation.document_version_id),
                    "title": citation.title,
                    "sectionPath": list(citation.section_path),
                    "pageStart": citation.page_start,
                    "pageEnd": citation.page_end,
                    "quote": citation.quote,
                }
            )
        blocks.append(
            {
                "type": block.type,
                "text": block.text,
                "citationIds": citation_ids,
            }
        )
    return {
        "blocks": blocks,
        "citations": citations,
        "warnings": answer.warnings,
    }


def sse_part(sequence: int, part: UIPart) -> str:
    data = json.dumps(part, ensure_ascii=False, separators=(",", ":"))
    return f"id: {sequence}\ndata: {data}\n\n"


def sse_done(sequence: int) -> str:
    return f"id: {sequence}\ndata: [DONE]\n\n"


def _chunks(value: str, size: int) -> list[str]:
    return [value[index : index + size] for index in range(0, len(value), size)]


def _insufficient_message(answer: VerifiedAnswer) -> str:
    if "INSUFFICIENT_PAPER_EVIDENCE" in answer.warnings:
        return "The selected papers do not contain enough verified evidence."
    return "No verified answer is available."
