import asyncio
from dataclasses import dataclass
import hashlib
from typing import BinaryIO

from research_agent.papers.domain import (
    DuplicateDocumentError,
    PaperPersistenceError,
    UploadIds,
)
from research_agent.papers.repository import UploadPaperRepository
from research_agent.papers.storage import ObjectStorage
from research_agent.shared.errors import AppError
from research_agent.shared.ids import EntityId


@dataclass(slots=True)
class UploadPaperCommand:
    filename: str
    content_type: str
    stream: BinaryIO


@dataclass(frozen=True, slots=True)
class UploadReceipt:
    document_id: EntityId
    version_id: EntityId
    task_id: EntityId
    duplicate: bool


def validate_and_hash_pdf(stream: BinaryIO) -> str:
    original_position = stream.tell()
    digest = hashlib.sha256()
    try:
        stream.seek(0)
        if stream.read(5) != b"%PDF-":
            raise AppError(
                code="INVALID_PDF",
                message="File signature is not PDF",
            )
        stream.seek(0)
        while block := stream.read(1024 * 1024):
            digest.update(block)
        return digest.hexdigest()
    finally:
        stream.seek(original_position)


class PaperService:
    def __init__(
        self,
        papers: UploadPaperRepository,
        storage: ObjectStorage,
    ) -> None:
        self._papers = papers
        self._storage = storage

    async def upload_pdf(self, command: UploadPaperCommand) -> UploadReceipt:
        if command.content_type != "application/pdf":
            raise AppError(
                code="INVALID_PDF",
                message="Only PDF files are supported",
            )
        sha256 = await asyncio.to_thread(validate_and_hash_pdf, command.stream)
        command.stream.seek(0)
        existing = await self._papers.find_by_sha256(sha256)
        if existing is not None:
            return UploadReceipt(
                document_id=existing.id,
                version_id=existing.current_version_id,
                task_id=existing.latest_task_id,
                duplicate=True,
            )

        ids = UploadIds.create()
        key = f"papers/{ids.document_id}/source/{ids.version_id}.pdf"
        await asyncio.to_thread(
            self._storage.put,
            key=key,
            stream=command.stream,
            content_type="application/pdf",
        )
        try:
            await self._papers.create_upload(
                ids=ids,
                title=command.filename.removesuffix(".pdf"),
                filename=command.filename,
                sha256=sha256,
                source_object_key=key,
                parser_name="docling",
                parser_version="unparsed",
                pipeline_version="1",
            )
            await self._papers.add_outbox_event(
                event_id=ids.outbox_event_id,
                event_type="document.uploaded",
                payload={
                    "version_id": str(ids.version_id),
                    "task_id": str(ids.task_id),
                },
            )
            await self._papers.commit()
        except DuplicateDocumentError:
            await self._papers.rollback()
            await asyncio.to_thread(self._storage.delete, key=key)
            existing = await self._papers.find_by_sha256(sha256)
            if existing is None:
                raise AppError(
                    code="DUPLICATE_DOCUMENT_UNAVAILABLE",
                    message="The duplicate document could not be reloaded",
                    retryable=True,
                )
            return UploadReceipt(
                document_id=existing.id,
                version_id=existing.current_version_id,
                task_id=existing.latest_task_id,
                duplicate=True,
            )
        except PaperPersistenceError:
            await self._papers.rollback()
            await asyncio.to_thread(self._storage.delete, key=key)
            raise AppError(
                code="PERSISTENCE_ERROR",
                message="The upload could not be persisted",
                retryable=True,
            ) from None
        except Exception:
            await self._papers.rollback()
            await asyncio.to_thread(self._storage.delete, key=key)
            raise

        return UploadReceipt(
            document_id=ids.document_id,
            version_id=ids.version_id,
            task_id=ids.task_id,
            duplicate=False,
        )
