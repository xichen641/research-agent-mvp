from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from research_agent.chat.domain import (
    ChatMessageRecord,
    ChatRunRecord,
    ChatSessionRecord,
    ModelKnowledgeBlock,
    RetrievalMode,
    VerifiedAnswer,
)
from research_agent.chat.routes import get_chat_history, get_chat_stream_service
from research_agent.chat.stream import sse_done, sse_part
from research_agent.main import create_app
from research_agent.retrieval.domain import RetrievalScope


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
        self.started: list[tuple[object, str, RetrievalMode]] = []

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

    async def start(
        self,
        *,
        session_id,
        question: str,
        retrieval_mode: RetrievalMode = "auto",
    ):
        self.started.append((session_id, question, retrieval_mode))
        return self.run_id


class FakeChatHistory:
    def __init__(self, session_id) -> None:
        self.session_id = session_id
        now = datetime.now(UTC)
        self.session = ChatSessionRecord(
            id=session_id,
            title="Durable discussion",
            scope=RetrievalScope(),
            created_at=now,
            updated_at=now,
        )
        self.messages = [
            ChatMessageRecord(
                id=uuid4(),
                session_id=session_id,
                role="user",
                content={"text": "Remember this decision"},
                created_at=now,
            ),
            ChatMessageRecord(
                id=uuid4(),
                session_id=session_id,
                role="assistant",
                content=VerifiedAnswer(
                    blocks=[ModelKnowledgeBlock(text="The decision is durable.")]
                ).model_dump(mode="json"),
                created_at=now,
            ),
        ]
        self.latest_run: ChatRunRecord | None = None

    async def get_session(self, session_id):
        return self.session if session_id == self.session_id else None

    async def list_sessions(self, *, limit: int = 100):
        return [self.session][:limit]

    async def list_messages(self, session_id):
        return self.messages if session_id == self.session_id else []

    async def list_message_page(self, session_id, *, before_sequence=None, limit=50):
        return (await self.list_messages(session_id))[:limit], None

    async def get_latest_run(self, session_id):
        return self.latest_run if session_id == self.session_id else None


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
            json={"question": "Compare the methods", "retrieval_mode": "on"},
            headers={"Accept": "text/event-stream"},
        )

    assert created.status_code == 201
    assert response.status_code == 200
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    assert commands.started == [(commands.session_id, "Compare the methods", "on")]
    assert "data: [DONE]" in response.text


@pytest.mark.asyncio
async def test_chat_history_restores_persisted_turns() -> None:
    session_id = uuid4()
    history = FakeChatHistory(session_id)
    app = create_app(web_origin="http://web.test")
    app.dependency_overrides[get_chat_history] = lambda: history
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/v1/chat/sessions/{session_id}/messages")

    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["content"] == {"text": "Remember this decision"}
    assert items[1]["content"] == {
        "blocks": [{"type": "model_knowledge", "text": "The decision is durable."}],
        "citations": [],
        "warnings": [],
    }


@pytest.mark.asyncio
async def test_chat_session_index_exposes_reopenable_discussions() -> None:
    session_id = uuid4()
    history = FakeChatHistory(session_id)
    app = create_app(web_origin="http://web.test")
    app.dependency_overrides[get_chat_history] = lambda: history
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/chat/sessions", params={"limit": 20})

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == str(session_id)
    assert response.json()["items"][0]["title"] == "Durable discussion"


@pytest.mark.asyncio
async def test_chat_session_activity_exposes_a_reconnectable_run() -> None:
    session_id = uuid4()
    history = FakeChatHistory(session_id)
    now = datetime.now(UTC)
    history.latest_run = ChatRunRecord(
        id=uuid4(),
        session_id=session_id,
        status="RUNNING",
        error_code=None,
        created_at=now,
        completed_at=None,
    )
    app = create_app(web_origin="http://web.test")
    app.dependency_overrides[get_chat_history] = lambda: history
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/v1/chat/sessions/{session_id}/activity")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": str(session_id),
        "run_id": str(history.latest_run.id),
        "status": "RUNNING",
        "error_code": None,
        "stream_url": f"/api/v1/chat/runs/{history.latest_run.id}/stream",
        "user_message_id": None,
        "assistant_message_id": None,
    }


@pytest.mark.asyncio
async def test_chat_session_activity_is_idle_before_the_first_question() -> None:
    session_id = uuid4()
    history = FakeChatHistory(session_id)
    app = create_app(web_origin="http://web.test")
    app.dependency_overrides[get_chat_history] = lambda: history
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/v1/chat/sessions/{session_id}/activity")

    assert response.status_code == 200
    assert response.json()["status"] == "IDLE"
    assert response.json()["run_id"] is None
