from typing import Annotated, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.notes.domain import (
    NoteEvidence,
    NoteRevisionRecord,
    PaperNoteContent,
    PaperNoteRecord,
)
from research_agent.notes.repository import (
    NoteNotFoundError,
    NotePersistenceError,
    SqlNoteRepository,
)
from research_agent.papers.routes import get_db_session
from research_agent.shared.errors import AppError
from research_agent.tasks.celery_app import celery_app


router = APIRouter()


class TaskSender(Protocol):
    def send_task(
        self,
        name: str,
        *,
        kwargs: dict[str, object],
        queue: str,
    ) -> object: ...


class NoteQueuedResponse(BaseModel):
    status: str = "QUEUED"


class NoteEvidenceResponse(BaseModel):
    field_key: str
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    title: str
    section_path: list[str]
    page_start: int
    page_end: int
    quote: str
    pdf_url: str


class NoteRevisionResponse(BaseModel):
    id: UUID
    note_id: UUID
    revision_number: int
    source: str
    content: PaperNoteContent
    created_at: str
    evidence: list[NoteEvidenceResponse]


class PaperNoteResponse(BaseModel):
    id: UUID
    document_id: UUID
    current_revision_id: UUID | None
    current_revision: NoteRevisionResponse | None


class SaveNoteRequest(BaseModel):
    content: PaperNoteContent


class NoteRevisionListResponse(BaseModel):
    items: list[NoteRevisionResponse]


def get_note_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlNoteRepository:
    return SqlNoteRepository(session)


def get_note_task_sender() -> TaskSender:
    return celery_app


@router.post(
    "/documents/{document_id}/notes/generate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=NoteQueuedResponse,
)
async def request_note_generation(
    document_id: UUID,
    sender: Annotated[TaskSender, Depends(get_note_task_sender)],
) -> NoteQueuedResponse:
    _send_generation(sender, document_id=document_id, activate_if_new=True)
    return NoteQueuedResponse()


@router.get(
    "/documents/{document_id}/notes",
    response_model=PaperNoteResponse,
)
async def get_document_note(
    document_id: UUID,
    repository: Annotated[SqlNoteRepository, Depends(get_note_repository)],
) -> PaperNoteResponse:
    try:
        note = await repository.get_by_document(document_id)
    except NotePersistenceError:
        raise _note_persistence_error() from None
    if note is None:
        raise AppError("NOTE_NOT_FOUND", "Paper note was not found")
    return _note_response(note)


@router.put("/notes/{note_id}", response_model=NoteRevisionResponse)
async def save_note(
    note_id: UUID,
    body: SaveNoteRequest,
    repository: Annotated[SqlNoteRepository, Depends(get_note_repository)],
) -> NoteRevisionResponse:
    # A user save is authoritative: accepted or edited fields become confirmed.
    # 用户保存具有权威性：已接受或已编辑的字段统一转为用户确认状态。
    confirmed = PaperNoteContent(
        fields=[
            field.model_copy(update={"status": "USER_CONFIRMED"})
            for field in body.content.fields
        ]
    )
    try:
        revision = await repository.save_user_edit(note_id=note_id, content=confirmed)
    except NoteNotFoundError:
        raise AppError("NOTE_NOT_FOUND", "Paper note was not found") from None
    except NotePersistenceError:
        raise _note_persistence_error() from None
    return _revision_response(revision)


@router.get(
    "/notes/{note_id}/revisions",
    response_model=NoteRevisionListResponse,
)
async def list_note_revisions(
    note_id: UUID,
    repository: Annotated[SqlNoteRepository, Depends(get_note_repository)],
) -> NoteRevisionListResponse:
    try:
        revisions = await repository.list_revisions(note_id)
    except NoteNotFoundError:
        raise AppError("NOTE_NOT_FOUND", "Paper note was not found") from None
    except NotePersistenceError:
        raise _note_persistence_error() from None
    return NoteRevisionListResponse(
        items=[_revision_response(revision) for revision in revisions]
    )


@router.post(
    "/notes/{note_id}/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=NoteQueuedResponse,
)
async def regenerate_note(
    note_id: UUID,
    repository: Annotated[SqlNoteRepository, Depends(get_note_repository)],
    sender: Annotated[TaskSender, Depends(get_note_task_sender)],
) -> NoteQueuedResponse:
    try:
        note = await repository.get_note(note_id)
    except NotePersistenceError:
        raise _note_persistence_error() from None
    if note is None:
        raise AppError("NOTE_NOT_FOUND", "Paper note was not found")
    _send_generation(sender, document_id=note.document_id, activate_if_new=False)
    return NoteQueuedResponse()


def _send_generation(
    sender: TaskSender, *, document_id: UUID, activate_if_new: bool
) -> None:
    try:
        sender.send_task(
            "research_agent.notes.generate_note",
            kwargs={
                "document_id": str(document_id),
                "activate_if_new": activate_if_new,
            },
            queue="note_generation",
        )
    except Exception:
        raise AppError(
            "NOTE_QUEUE_UNAVAILABLE",
            "Note generation could not be queued",
            retryable=True,
        ) from None


def _note_response(note: PaperNoteRecord) -> PaperNoteResponse:
    return PaperNoteResponse(
        id=note.id,
        document_id=note.document_id,
        current_revision_id=note.current_revision_id,
        current_revision=(
            None
            if note.current_revision is None
            else _revision_response(note.current_revision)
        ),
    )


def _revision_response(revision: NoteRevisionRecord) -> NoteRevisionResponse:
    return NoteRevisionResponse(
        id=revision.id,
        note_id=revision.note_id,
        revision_number=revision.revision_number,
        source=revision.source,
        content=revision.content,
        created_at=revision.created_at.isoformat(),
        evidence=[_evidence_response(item) for item in revision.evidence],
    )


def _evidence_response(evidence: NoteEvidence) -> NoteEvidenceResponse:
    return NoteEvidenceResponse(
        field_key=evidence.field_key,
        chunk_id=evidence.chunk_id,
        document_id=evidence.document_id,
        document_version_id=evidence.document_version_id,
        title=evidence.title,
        section_path=list(evidence.section_path),
        page_start=evidence.page_start,
        page_end=evidence.page_end,
        quote=evidence.quote,
        pdf_url=(
            f"/api/v1/documents/{evidence.document_id}/pdf?page={evidence.page_start}"
        ),
    )


def _note_persistence_error() -> AppError:
    return AppError(
        "NOTE_PERSISTENCE_UNAVAILABLE",
        "Paper notes are temporarily unavailable",
        retryable=True,
    )
