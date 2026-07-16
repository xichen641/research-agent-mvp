from collections.abc import AsyncIterator
import base64
from datetime import datetime
from functools import lru_cache
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.config import get_settings
from research_agent.papers.domain import (
    DocumentDetailRecord,
    DocumentSummaryRecord,
    PaperPersistenceError,
    SafeDocumentVersionRecord,
)
from research_agent.papers.repository import SqlPaperRepository
from research_agent.papers.service import PaperService, UploadPaperCommand
from research_agent.papers.storage import MinioObjectStorage, ObjectStorage
from research_agent.shared.db import async_session_factory
from research_agent.shared.errors import AppError
from research_agent.shared.ids import EntityId


router = APIRouter()


class UploadResponse(BaseModel):
    document_id: EntityId
    version_id: EntityId
    task_id: EntityId
    duplicate: bool


class DocumentSummaryResponse(BaseModel):
    id: EntityId
    title: str
    status: str
    current_version_id: EntityId
    latest_task_id: EntityId
    created_at: datetime
    updated_at: datetime


class SafeDocumentVersionResponse(BaseModel):
    id: EntityId
    filename: str
    status: str
    pipeline_version: str
    created_at: datetime


class DocumentDetailResponse(DocumentSummaryResponse):
    current_version: SafeDocumentVersionResponse


class DocumentListResponse(BaseModel):
    items: list[DocumentSummaryResponse]
    next_cursor: str | None


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


@lru_cache
def get_object_storage() -> ObjectStorage:
    return MinioObjectStorage.from_settings(get_settings())


def get_paper_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> PaperService:
    return PaperService(SqlPaperRepository(session), storage)


def get_paper_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlPaperRepository:
    return SqlPaperRepository(session)


@router.post("/documents", status_code=202, response_model=UploadResponse)
async def upload_document(
    file: Annotated[UploadFile, File()],
    service: Annotated[PaperService, Depends(get_paper_service)],
) -> UploadResponse:
    receipt = await service.upload_pdf(
        UploadPaperCommand(
            filename=file.filename or "paper.pdf",
            content_type=file.content_type or "",
            stream=file.file,
        )
    )
    return UploadResponse.model_validate(receipt, from_attributes=True)


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    repository: Annotated[SqlPaperRepository, Depends(get_paper_repository)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> DocumentListResponse:
    before_created_at: datetime | None = None
    before_id: EntityId | None = None
    if cursor is not None:
        before_created_at, before_id = _decode_cursor(cursor)
    try:
        records = await repository.list_documents(
            limit=limit + 1,
            before_created_at=before_created_at,
            before_id=before_id,
        )
    except PaperPersistenceError:
        raise _paper_query_error() from None
    has_more = len(records) > limit
    items = records[:limit]
    next_cursor = _encode_cursor(items[-1]) if has_more and items else None
    return DocumentListResponse(
        items=[_summary_response(record) for record in items],
        next_cursor=next_cursor,
    )


@router.get("/documents/{document_id}", response_model=DocumentDetailResponse)
async def get_document_detail(
    document_id: EntityId,
    repository: Annotated[SqlPaperRepository, Depends(get_paper_repository)],
) -> DocumentDetailResponse:
    try:
        record = await repository.get_document_detail(document_id)
    except PaperPersistenceError:
        raise _paper_query_error() from None
    if record is None:
        raise AppError(
            code="DOCUMENT_NOT_FOUND",
            message="Document was not found",
        )
    return _detail_response(record)


def _encode_cursor(record: DocumentSummaryRecord) -> str:
    payload = json.dumps(
        {
            "created_at": record.created_at.isoformat(),
            "id": str(record.id),
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _paper_query_error() -> AppError:
    return AppError(
        code="PAPER_PERSISTENCE_ERROR",
        message="Paper data could not be loaded",
        retryable=True,
    )


def _decode_cursor(cursor: str) -> tuple[datetime, EntityId]:
    try:
        if not cursor or len(cursor) > 1024:
            raise ValueError("invalid cursor length")
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"created_at", "id"}:
            raise ValueError("invalid cursor schema")
        if not isinstance(payload["created_at"], str) or not isinstance(
            payload["id"], str
        ):
            raise ValueError("invalid cursor values")
        created_at = datetime.fromisoformat(payload["created_at"])
        if created_at.tzinfo is None:
            raise ValueError("cursor timestamp must include timezone")
        identifier = UUID(payload["id"])
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        raise AppError(code="INVALID_CURSOR", message="Cursor is invalid") from None
    return created_at, identifier


def _summary_response(record: DocumentSummaryRecord) -> DocumentSummaryResponse:
    return DocumentSummaryResponse.model_validate(record, from_attributes=True)


def _version_response(
    record: SafeDocumentVersionRecord,
) -> SafeDocumentVersionResponse:
    return SafeDocumentVersionResponse.model_validate(record, from_attributes=True)


def _detail_response(record: DocumentDetailRecord) -> DocumentDetailResponse:
    return DocumentDetailResponse(
        **_summary_response(record.summary).model_dump(),
        current_version=_version_response(record.current_version),
    )
