from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from research_agent.main import create_app
from research_agent.notes.domain import (
    NOTE_TEMPLATE,
    NoteField,
    NoteRevisionRecord,
    PaperNoteContent,
    PaperNoteRecord,
)
from research_agent.notes.routes import get_note_repository, get_note_task_sender


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], str]] = []

    def send_task(
        self,
        name: str,
        *,
        kwargs: dict[str, object],
        queue: str,
    ) -> object:
        self.calls.append((name, kwargs, queue))
        return object()


class FakeNoteRepository:
    def __init__(self) -> None:
        self.note_id = uuid4()
        self.document_id = uuid4()
        self.saved: PaperNoteContent | None = None

    async def get_note(self, note_id):
        if note_id != self.note_id:
            return None
        now = datetime.now(UTC)
        return PaperNoteRecord(
            id=self.note_id,
            document_id=self.document_id,
            current_revision_id=None,
            created_at=now,
            updated_at=now,
        )

    async def save_user_edit(self, *, note_id, content):
        self.saved = content
        return NoteRevisionRecord(
            id=uuid4(),
            note_id=note_id,
            revision_number=2,
            source="USER_EDIT",
            content=content,
            created_at=datetime.now(UTC),
            evidence=(),
        )


def content() -> PaperNoteContent:
    return PaperNoteContent(
        fields=[
            NoteField(
                key=key,
                title=title,
                markdown=("My comment" if key == "user_comments" else "Paper fact"),
                evidence_chunk_ids=([] if key == "user_comments" else [uuid4()]),
                status=("USER_CONFIRMED" if key == "user_comments" else "AI_GENERATED"),
            )
            for key, title in NOTE_TEMPLATE
        ]
    )


@pytest.mark.asyncio
async def test_generation_is_queued_only_after_explicit_request() -> None:
    sender = FakeSender()
    app = create_app(web_origin="http://web.test")
    app.dependency_overrides[get_note_task_sender] = lambda: sender
    document_id = uuid4()

    assert sender.calls == []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/v1/documents/{document_id}/notes/generate"
        )

    assert response.status_code == 202
    assert sender.calls == [
        (
            "research_agent.notes.generate_note",
            {"document_id": str(document_id), "activate_if_new": True},
            "note_generation",
        )
    ]


@pytest.mark.asyncio
async def test_regenerate_queues_non_activating_draft_and_user_save_confirms_fields() -> None:
    sender = FakeSender()
    repository = FakeNoteRepository()
    app = create_app(web_origin="http://web.test")
    app.dependency_overrides[get_note_task_sender] = lambda: sender
    app.dependency_overrides[get_note_repository] = lambda: repository

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        regenerated = await client.post(
            f"/api/v1/notes/{repository.note_id}/regenerate"
        )
        saved = await client.put(
            f"/api/v1/notes/{repository.note_id}",
            json={"content": content().model_dump(mode="json")},
        )

    assert regenerated.status_code == 202
    assert sender.calls[0][1] == {
        "document_id": str(repository.document_id),
        "activate_if_new": False,
    }
    assert saved.status_code == 200
    assert saved.json()["source"] == "USER_EDIT"
    assert repository.saved is not None
    assert all(field.status == "USER_CONFIRMED" for field in repository.saved.fields)
