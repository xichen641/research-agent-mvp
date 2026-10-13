import hashlib
from datetime import timedelta
from io import BytesIO
import threading
from typing import BinaryIO, cast
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.papers.domain import (
    DocumentRecord,
    DocumentStatus,
    DuplicateDocumentError,
    PaperPersistenceError,
    UploadIds,
)
from research_agent.papers.repository import SqlPaperRepository
from research_agent.papers.service import (
    PaperService,
    UploadPaperCommand,
    validate_and_hash_pdf,
)
from research_agent.shared.errors import AppError


class FakePaperRepository:
    def __init__(self) -> None:
        self.existing: DocumentRecord | None = None
        self.find_results: list[DocumentRecord | None] = []
        self.created: list[dict[str, object]] = []
        self.outbox: list[dict[str, object]] = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.create_error: Exception | None = None
        self.commit_error: Exception | None = None

    async def find_by_sha256(self, sha256: str) -> DocumentRecord | None:
        if self.find_results:
            return self.find_results.pop(0)
        if self.existing is not None and self.existing.sha256 == sha256:
            return self.existing
        return None

    async def create_upload(
        self,
        *,
        ids: UploadIds,
        title: str,
        filename: str,
        sha256: str,
        source_object_key: str,
        parser_name: str,
        parser_version: str,
        pipeline_version: str,
    ) -> None:
        self.created.append(
            {
                "ids": ids,
                "title": title,
                "filename": filename,
                "sha256": sha256,
                "source_object_key": source_object_key,
                "parser_name": parser_name,
                "parser_version": parser_version,
                "pipeline_version": pipeline_version,
            }
        )
        if self.create_error is not None:
            raise self.create_error

    async def add_outbox_event(
        self,
        *,
        event_id: UUID,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        self.outbox.append(
            {"event_id": event_id, "event_type": event_type, "payload": payload}
        )

    async def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollback_calls += 1


class FakeObjectStorage:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, object]] = []
        self.delete_calls: list[str] = []

    def put(self, *, key: str, stream: BinaryIO, content_type: str) -> None:
        self.put_calls.append(
            {"key": key, "content": stream.read(), "content_type": content_type}
        )

    def get(self, *, key: str) -> BinaryIO:
        del key
        return BytesIO()

    def delete(self, *, key: str) -> None:
        self.delete_calls.append(key)

    def presigned_get(self, *, key: str, expires: timedelta) -> str:
        del key, expires
        return "https://example.test/object"


def document_record(content: bytes) -> DocumentRecord:
    return DocumentRecord(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        title="existing",
        sha256=hashlib.sha256(content).hexdigest(),
        status=DocumentStatus.PROCESSING,
        current_version_id=UUID("00000000-0000-0000-0000-000000000002"),
        latest_task_id=UUID("00000000-0000-0000-0000-000000000003"),
    )


@pytest.mark.asyncio
async def test_upload_rejects_non_pdf_mime_before_writes() -> None:
    papers = FakePaperRepository()
    storage = FakeObjectStorage()
    service = PaperService(papers, storage)

    with pytest.raises(AppError) as error:
        await service.upload_pdf(
            UploadPaperCommand("paper.txt", "text/plain", BytesIO(b"not-pdf"))
        )

    assert error.value.code == "INVALID_PDF"
    assert storage.put_calls == []
    assert papers.created == []


@pytest.mark.asyncio
async def test_upload_rejects_invalid_pdf_signature_before_writes() -> None:
    papers = FakePaperRepository()
    storage = FakeObjectStorage()
    service = PaperService(papers, storage)

    with pytest.raises(AppError) as error:
        await service.upload_pdf(
            UploadPaperCommand("paper.pdf", "application/pdf", BytesIO(b"not-pdf"))
        )

    assert error.value.code == "INVALID_PDF"
    assert storage.put_calls == []
    assert papers.created == []


@pytest.mark.asyncio
async def test_duplicate_hash_returns_existing_ids_without_writes() -> None:
    content = b"%PDF-1.7\ncontent"
    papers = FakePaperRepository()
    papers.existing = document_record(content)
    storage = FakeObjectStorage()
    service = PaperService(papers, storage)

    receipt = await service.upload_pdf(
        UploadPaperCommand("paper.pdf", "application/pdf", BytesIO(content))
    )

    assert receipt.document_id == papers.existing.id
    assert receipt.version_id == papers.existing.current_version_id
    assert receipt.task_id == papers.existing.latest_task_id
    assert receipt.duplicate is True
    assert storage.put_calls == []
    assert papers.created == []
    assert papers.commit_calls == 0


@pytest.mark.asyncio
async def test_valid_pdf_is_validated_from_zero_when_stream_starts_nonzero() -> None:
    content = b"%PDF-1.7\ncontent"
    papers = FakePaperRepository()
    papers.existing = document_record(content)
    storage = FakeObjectStorage()
    stream = BytesIO(content)
    stream.seek(9)

    receipt = await PaperService(papers, storage).upload_pdf(
        UploadPaperCommand("paper.pdf", "application/pdf", stream)
    )

    assert receipt.duplicate is True
    assert receipt.document_id == papers.existing.id
    assert storage.put_calls == []


class ReadThreadTrackingStream(BytesIO):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.read_threads: list[int] = []

    def read(self, size: int | None = -1) -> bytes:
        self.read_threads.append(threading.get_ident())
        return super().read(size)


@pytest.mark.asyncio
async def test_validation_and_hash_reads_run_off_event_loop_thread() -> None:
    content = b"%PDF-1.7\n" + b"a" * (1024 * 1024 + 17)
    papers = FakePaperRepository()
    papers.existing = document_record(content)
    storage = FakeObjectStorage()
    stream = ReadThreadTrackingStream(content)
    event_loop_thread = threading.get_ident()

    receipt = await PaperService(papers, storage).upload_pdf(
        UploadPaperCommand("paper.pdf", "application/pdf", stream)
    )

    assert receipt.duplicate is True
    assert stream.read_threads
    assert event_loop_thread not in stream.read_threads


@pytest.mark.asyncio
async def test_successful_upload_writes_state_outbox_and_commits() -> None:
    content = b"%PDF-1.7\ncontent"
    papers = FakePaperRepository()
    storage = FakeObjectStorage()
    service = PaperService(papers, storage)

    receipt = await service.upload_pdf(
        UploadPaperCommand("study.pdf", "application/pdf", BytesIO(content))
    )

    expected_key = f"papers/{receipt.document_id}/source/{receipt.version_id}.pdf"
    assert receipt.duplicate is False
    assert storage.put_calls == [
        {
            "key": expected_key,
            "content": content,
            "content_type": "application/pdf",
        }
    ]
    assert papers.created[0]["title"] == "study"
    assert papers.created[0]["filename"] == "study.pdf"
    assert papers.created[0]["sha256"] == hashlib.sha256(content).hexdigest()
    assert papers.created[0]["source_object_key"] == expected_key
    assert papers.created[0]["parser_name"] == "docling"
    assert papers.created[0]["parser_version"] == "unparsed"
    assert papers.created[0]["pipeline_version"] == "1"
    ids = papers.created[0]["ids"]
    assert isinstance(ids, UploadIds)
    assert papers.outbox == [
        {
            "event_id": ids.outbox_event_id,
            "event_type": "document.uploaded",
            "payload": {
                "version_id": str(receipt.version_id),
                "task_id": str(receipt.task_id),
            },
        }
    ]
    assert papers.commit_calls == 1
    assert papers.rollback_calls == 0


@pytest.mark.asyncio
async def test_post_storage_failure_rolls_back_and_deletes_generated_key() -> None:
    papers = FakePaperRepository()
    papers.create_error = RuntimeError("database failed")
    storage = FakeObjectStorage()
    service = PaperService(papers, storage)

    with pytest.raises(RuntimeError, match="database failed"):
        await service.upload_pdf(
            UploadPaperCommand(
                "paper.pdf", "application/pdf", BytesIO(b"%PDF-1.7\ncontent")
            )
        )

    assert papers.rollback_calls == 1
    assert storage.delete_calls == [storage.put_calls[0]["key"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["create", "commit"])
async def test_persistence_failure_is_sanitized_for_service_caller(
    failure_point: str,
) -> None:
    papers = FakePaperRepository()
    persistence_error = PaperPersistenceError()
    if failure_point == "create":
        papers.create_error = persistence_error
    else:
        papers.commit_error = persistence_error
    storage = FakeObjectStorage()
    service = PaperService(papers, storage)

    with pytest.raises(AppError) as error:
        await service.upload_pdf(
            UploadPaperCommand(
                "paper.pdf", "application/pdf", BytesIO(b"%PDF-1.7\ncontent")
            )
        )

    assert error.value.code == "PERSISTENCE_ERROR"
    assert error.value.message == "The upload could not be persisted"
    assert error.value.retryable is True
    assert "sql" not in str(error.value).lower()
    assert "driver" not in str(error.value).lower()
    assert papers.rollback_calls == 1
    assert storage.delete_calls == [storage.put_calls[0]["key"]]


@pytest.mark.asyncio
async def test_duplicate_race_rolls_back_deletes_loser_and_returns_winner() -> None:
    content = b"%PDF-1.7\nrace"
    winner = document_record(content)
    papers = FakePaperRepository()
    papers.find_results = [None, winner]
    papers.create_error = DuplicateDocumentError(winner.sha256)
    storage = FakeObjectStorage()
    service = PaperService(papers, storage)

    receipt = await service.upload_pdf(
        UploadPaperCommand("race.pdf", "application/pdf", BytesIO(content))
    )

    assert receipt.document_id == winner.id
    assert receipt.version_id == winner.current_version_id
    assert receipt.task_id == winner.latest_task_id
    assert receipt.duplicate is True
    assert papers.rollback_calls == 1
    assert storage.delete_calls == [storage.put_calls[0]["key"]]


@pytest.mark.asyncio
async def test_duplicate_race_without_reload_raises_retryable_error() -> None:
    content = b"%PDF-1.7\nrace"
    papers = FakePaperRepository()
    papers.find_results = [None, None]
    papers.create_error = DuplicateDocumentError(hashlib.sha256(content).hexdigest())
    storage = FakeObjectStorage()
    service = PaperService(papers, storage)

    with pytest.raises(AppError) as error:
        await service.upload_pdf(
            UploadPaperCommand("race.pdf", "application/pdf", BytesIO(content))
        )

    assert error.value.code == "DUPLICATE_DOCUMENT_UNAVAILABLE"
    assert error.value.retryable is True
    assert papers.rollback_calls == 1
    assert storage.delete_calls == [storage.put_calls[0]["key"]]


def test_hash_stream_reads_more_than_one_mebibyte_and_restores_position() -> None:
    content = b"%PDF-" + b"a" * (1024 * 1024 + 17)
    stream = BytesIO(content)
    stream.seek(13)

    digest = validate_and_hash_pdf(stream)

    assert digest == hashlib.sha256(content).hexdigest()
    assert stream.tell() == 13


class FailingStream(BytesIO):
    def read(self, size: int | None = -1) -> bytes:
        del size
        raise OSError("read failed")


def test_hash_stream_restores_position_when_read_fails() -> None:
    stream = FailingStream(b"content")
    stream.seek(4)

    with pytest.raises(OSError, match="read failed"):
        validate_and_hash_pdf(stream)

    assert stream.tell() == 4


class IntegrityFailingSession:
    def __init__(self, failure_point: str) -> None:
        self.failure_point = failure_point
        self.added: list[object] = []
        self.integrity_error = IntegrityError(
            "INSERT INTO private_table(secret)",
            {"secret": "driver-token"},
            Exception("driver detail"),
        )

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        if self.failure_point == "create":
            raise self.integrity_error

    async def commit(self) -> None:
        if self.failure_point == "commit":
            raise self.integrity_error


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["create", "commit"])
async def test_repository_translates_non_duplicate_integrity_error(
    failure_point: str,
) -> None:
    session = IntegrityFailingSession(failure_point)
    repository = SqlPaperRepository(cast(AsyncSession, session))

    with pytest.raises(PaperPersistenceError) as error:
        if failure_point == "create":
            await repository.create_upload(
                ids=UploadIds.create(),
                title="paper",
                filename="paper.pdf",
                sha256="a" * 64,
                source_object_key="papers/document/source/version.pdf",
                parser_name="docling",
                parser_version="unparsed",
                pipeline_version="1",
            )
        else:
            await repository.commit()

    assert not isinstance(error.value, IntegrityError)
    assert "private_table" not in str(error.value)
    assert "driver" not in str(error.value).lower()
    assert "token" not in str(error.value).lower()
