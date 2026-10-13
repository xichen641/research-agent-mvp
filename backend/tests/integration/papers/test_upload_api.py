from collections.abc import AsyncIterator
from datetime import timedelta
from io import BytesIO
from typing import BinaryIO, cast

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.main import create_app
from research_agent.notes.models import PaperNoteModel
from research_agent.papers.models import (
    DocumentModel,
    DocumentTaskModel,
    DocumentVersionModel,
    OutboxEventModel,
)
from research_agent.papers.repository import SqlPaperRepository
from research_agent.papers.routes import (
    get_db_session,
    get_object_storage,
    get_paper_service,
)
from research_agent.papers.service import PaperService


class MemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[str] = []
        self.delete_calls: list[str] = []

    def put(self, *, key: str, stream: BinaryIO, content_type: str) -> None:
        assert content_type == "application/pdf"
        self.objects[key] = stream.read()
        self.put_calls.append(key)

    def get(self, *, key: str) -> BinaryIO:
        return BytesIO(self.objects[key])

    def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)
        self.delete_calls.append(key)

    def presigned_get(self, *, key: str, expires: timedelta) -> str:
        del expires
        return f"https://example.test/{key}"


class EmptyResult:
    def one_or_none(self) -> None:
        return None


class ApiIntegrityFailingSession:
    def __init__(self, failure_point: str) -> None:
        self.failure_point = failure_point
        self.rollback_calls = 0
        self.integrity_error = IntegrityError(
            "INSERT INTO private_table(secret)",
            {"secret": "driver-token"},
            Exception("driver detail"),
        )

    async def execute(self, statement: object) -> EmptyResult:
        del statement
        return EmptyResult()

    def add(self, instance: object) -> None:
        del instance

    async def flush(self) -> None:
        if self.failure_point == "create":
            raise self.integrity_error

    async def commit(self) -> None:
        if self.failure_point == "commit":
            raise self.integrity_error

    async def rollback(self) -> None:
        self.rollback_calls += 1


def build_client(
    db_session: AsyncSession,
    storage: MemoryObjectStorage,
) -> tuple[httpx.AsyncClient, object]:
    app = create_app()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_object_storage] = lambda: storage
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test"), app


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content_type", "content"),
    [
        ("paper.txt", "text/plain", b"not-pdf"),
        ("paper.pdf", "application/pdf", b"not-pdf"),
    ],
)
async def test_invalid_upload_returns_problem_with_matching_trace_id(
    db_session: AsyncSession,
    filename: str,
    content_type: str,
    content: bytes,
) -> None:
    storage = MemoryObjectStorage()
    client, _ = build_client(db_session, storage)

    async with client:
        response = await client.post(
            "/api/v1/documents",
            files={"file": (filename, content, content_type)},
            headers={"X-Trace-ID": "trace-from-test"},
        )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["X-Trace-ID"] == "trace-from-test"
    assert response.json() == {
        "type": "about:blank",
        "title": "Invalid Pdf",
        "status": 400,
        "code": "INVALID_PDF",
        "message": response.json()["message"],
        "retryable": False,
        "trace_id": "trace-from-test",
        "details": {},
    }
    assert storage.put_calls == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_valid_upload_returns_202_and_persists_complete_state(
    db_session: AsyncSession,
) -> None:
    content = b"%PDF-1.7\nintegration"
    storage = MemoryObjectStorage()
    client, _ = build_client(db_session, storage)

    async with client:
        response = await client.post(
            "/api/v1/documents",
            files={"file": ("study.pdf", content, "application/pdf")},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["duplicate"] is False
    assert body["document_id"]
    assert body["version_id"]
    assert body["task_id"]
    assert response.headers["X-Trace-ID"]
    assert len(storage.put_calls) == 1
    assert storage.objects[storage.put_calls[0]] == content

    counts = []
    for model in (
        DocumentModel,
        DocumentVersionModel,
        DocumentTaskModel,
        OutboxEventModel,
        PaperNoteModel,
    ):
        counts.append(await db_session.scalar(select(func.count()).select_from(model)))
    assert counts == [1, 1, 1, 1, 0]

    outbox = (await db_session.scalars(select(OutboxEventModel))).one()
    assert outbox.event_type == "document.uploaded"
    assert outbox.payload == {
        "version_id": body["version_id"],
        "task_id": body["task_id"],
    }
    assert outbox.attempts == 0
    assert outbox.sent_at is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_current_pdf_route_redirects_to_private_object_at_cited_page(
    db_session: AsyncSession,
) -> None:
    content = b"%PDF-1.7\ncitation source"
    storage = MemoryObjectStorage()
    client, _ = build_client(db_session, storage)

    async with client:
        uploaded = await client.post(
            "/api/v1/documents",
            files={"file": ("source.pdf", content, "application/pdf")},
        )
        response = await client.get(
            f"/api/v1/documents/{uploaded.json()['document_id']}/pdf?page=7",
            follow_redirects=False,
        )

    assert response.status_code == 307
    assert response.headers["location"].endswith("#page=7")
    assert "papers/" in response.headers["location"]
    assert "source_object_key" not in response.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_upload_returns_original_ids_without_second_write(
    db_session: AsyncSession,
) -> None:
    content = b"%PDF-1.7\nduplicate"
    storage = MemoryObjectStorage()
    client, _ = build_client(db_session, storage)

    async with client:
        first = await client.post(
            "/api/v1/documents",
            files={"file": ("first.pdf", content, "application/pdf")},
        )
        duplicate = await client.post(
            "/api/v1/documents",
            files={"file": ("renamed.pdf", content, "application/pdf")},
        )

    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["document_id"] == first.json()["document_id"]
    assert duplicate.json()["version_id"] == first.json()["version_id"]
    assert duplicate.json()["task_id"] == first.json()["task_id"]
    assert len(storage.put_calls) == 1

    document_count = await db_session.scalar(
        select(func.count()).select_from(DocumentModel)
    )
    outbox_count = await db_session.scalar(
        select(func.count()).select_from(OutboxEventModel)
    )
    assert document_count == 1
    assert outbox_count == 1


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["create", "commit"])
async def test_non_duplicate_integrity_error_returns_sanitized_problem(
    failure_point: str,
) -> None:
    app = create_app()
    session = ApiIntegrityFailingSession(failure_point)
    storage = MemoryObjectStorage()
    service = PaperService(
        SqlPaperRepository(cast(AsyncSession, session)),
        storage,
    )
    app.dependency_overrides[get_paper_service] = lambda: service
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/documents",
            files={"file": ("paper.pdf", b"%PDF-1.7\ncontent", "application/pdf")},
            headers={"X-Trace-ID": "persistence-trace"},
        )

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["X-Trace-ID"] == "persistence-trace"
    assert response.json()["code"] == "PERSISTENCE_ERROR"
    assert response.json()["message"] == "The upload could not be persisted"
    assert response.json()["retryable"] is True
    assert response.json()["trace_id"] == "persistence-trace"
    assert "private_table" not in response.text
    assert "driver" not in response.text.lower()
    assert "token" not in response.text.lower()
    assert session.rollback_calls == 1
    assert len(storage.put_calls) == 1
    assert storage.delete_calls == storage.put_calls
