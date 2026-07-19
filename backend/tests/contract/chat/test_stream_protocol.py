from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from research_agent.chat.routes import get_chat_stream_service
from research_agent.chat.domain import ChatSessionRecord
from research_agent.retrieval.domain import RetrievalScope
from research_agent.chat.stream import sse_done, sse_part
from research_agent.main import create_app


class SeededVerifiedStream:
    async def prepare(self, run_id) -> None:
        del run_id

    async def iter_sse(self, run_id, *, after_sequence: int = 0):
        del run_id
        parts = [
            {"type": "start", "messageId": "message-1"},
            {"type": "data-workflow", "data": {"stage": "plan"}},
            {"type": "text-start", "id": "text-1"},
            {"type": "text-delta", "id": "text-1", "delta": "Answer"},
            {"type": "text-end", "id": "text-1"},
            {
                "type": "data-citations",
                "data": {"blocks": [], "citations": [], "warnings": []},
            },
            {"type": "finish"},
        ]
        for sequence, part in enumerate(parts, start=1):
            if sequence > after_sequence:
                yield sse_part(sequence, part)
        if len(parts) + 1 > after_sequence:
            yield sse_done(len(parts) + 1)


class FakeChatCommands:
    def __init__(self) -> None:
        self.session_id = uuid4()
        self.run_id = uuid4()
        self.started: list[tuple[object, str]] = []

    async def create_session(
        self, *, title: str, scope: RetrievalScope
    ) -> ChatSessionRecord:
        now = datetime.now(UTC)
        return ChatSessionRecord(
            id=self.session_id,
            title=title,
            scope=scope,
            created_at=now,
            updated_at=now,
        )

    async def start(self, *, session_id, question: str):
        self.started.append((session_id, question))
        return self.run_id


@pytest.mark.asyncio
async def test_verified_answer_stream_has_required_parts() -> None:
    app = create_app(web_origin="http://web.test")
    app.dependency_overrides[get_chat_stream_service] = lambda: SeededVerifiedStream()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            f"/api/v1/chat/runs/{uuid4()}/stream",
            headers={"Accept": "text/event-stream"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    assert '"type":"start"' in response.text
    assert '"type":"text-start"' in response.text
    assert '"type":"text-delta"' in response.text
    assert '"type":"data-citations"' in response.text
    assert "data: [DONE]" in response.text


@pytest.mark.asyncio
async def test_last_event_id_resumes_only_later_persisted_parts() -> None:
    app = create_app(web_origin="http://web.test")
    app.dependency_overrides[get_chat_stream_service] = lambda: SeededVerifiedStream()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/v1/chat/runs/{uuid4()}/stream",
            headers={"Accept": "text/event-stream", "Last-Event-ID": "4"},
        )

    assert '"type":"start"' not in response.text
    assert '"type":"text-delta"' not in response.text
    assert '"type":"data-citations"' in response.text
    assert "data: [DONE]" in response.text


@pytest.mark.asyncio
async def test_message_submission_starts_run_and_immediately_streams() -> None:
    commands = FakeChatCommands()
    app = create_app(web_origin="http://web.test", chat_commands=commands)
    app.dependency_overrides[get_chat_stream_service] = lambda: SeededVerifiedStream()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/v1/chat/sessions",
            json={"title": "ReID", "document_ids": [str(uuid4())]},
        )
        response = await client.post(
            f"/api/v1/chat/sessions/{commands.session_id}/messages",
            json={"question": "Compare the methods"},
            headers={"Accept": "text/event-stream"},
        )

    assert created.status_code == 201
    assert response.status_code == 200
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    assert commands.started == [(commands.session_id, "Compare the methods")]
    assert "data: [DONE]" in response.text
