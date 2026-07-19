from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.ingestion.models import DocumentChunkModel
from research_agent.notes.domain import (
    NoteEvidence,
    NoteRevisionRecord,
    NoteRevisionSource,
    PaperNoteContent,
    PaperNoteRecord,
)
from research_agent.notes.models import (
    NoteEvidenceModel,
    NoteRevisionModel,
    PaperNoteModel,
)
from research_agent.papers.domain import DocumentStatus
from research_agent.papers.models import DocumentModel, DocumentVersionModel
from research_agent.shared.ids import new_id


class NoteNotFoundError(Exception):
    pass


class NoteDocumentNotReadyError(Exception):
    pass


class NotePersistenceError(Exception):
    pass


class SqlNoteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_document(self, document_id: UUID) -> PaperNoteRecord | None:
        try:
            note = await self._session.scalar(
                select(PaperNoteModel).where(PaperNoteModel.document_id == document_id)
            )
            if note is None:
                return None
            current = (
                None
                if note.current_revision_id is None
                else await self._load_revision(note.current_revision_id)
            )
            return _note_record(note, current)
        except SQLAlchemyError:
            raise NotePersistenceError() from None

    async def get_note(self, note_id: UUID) -> PaperNoteRecord | None:
        try:
            note = await self._session.get(PaperNoteModel, note_id)
            if note is None:
                return None
            current = (
                None
                if note.current_revision_id is None
                else await self._load_revision(note.current_revision_id)
            )
            return _note_record(note, current)
        except SQLAlchemyError:
            raise NotePersistenceError() from None

    async def list_revisions(self, note_id: UUID) -> list[NoteRevisionRecord]:
        try:
            note_exists = await self._session.scalar(
                select(PaperNoteModel.id).where(PaperNoteModel.id == note_id)
            )
            if note_exists is None:
                raise NoteNotFoundError()
            models = (
                await self._session.scalars(
                    select(NoteRevisionModel)
                    .where(NoteRevisionModel.note_id == note_id)
                    .order_by(NoteRevisionModel.revision_number.desc())
                )
            ).all()
            return [await self._revision_record(model) for model in models]
        except NoteNotFoundError:
            raise
        except SQLAlchemyError:
            raise NotePersistenceError() from None

    async def create_ai_draft(
        self,
        *,
        document_id: UUID,
        content: PaperNoteContent,
        evidence: list[NoteEvidence],
        activate_if_new: bool,
    ) -> NoteRevisionRecord:
        try:
            await self._require_ready_document(document_id)
            note = await self._session.scalar(
                select(PaperNoteModel)
                .where(PaperNoteModel.document_id == document_id)
                .with_for_update()
            )
            if note is None:
                note = PaperNoteModel(id=new_id(), document_id=document_id)
                self._session.add(note)
                await self._session.flush()
            revision = await self._append_revision(
                note=note,
                source="AI_DRAFT",
                content=content,
                evidence=evidence,
            )
            # Regeneration is intentionally non-destructive; only a first draft
            # may become current automatically after the explicit generate call.
            # 重新生成刻意保持非破坏性；仅首次显式生成的草稿可自动成为当前版本。
            if activate_if_new and note.current_revision_id is None:
                note.current_revision_id = revision.id
            await self._session.commit()
            return _revision_record_from_values(revision, evidence)
        except (NoteDocumentNotReadyError, NoteNotFoundError):
            await self._session.rollback()
            raise
        except (IntegrityError, SQLAlchemyError):
            await self._session.rollback()
            raise NotePersistenceError() from None

    async def save_user_edit(
        self, *, note_id: UUID, content: PaperNoteContent
    ) -> NoteRevisionRecord:
        try:
            note = await self._session.scalar(
                select(PaperNoteModel)
                .where(PaperNoteModel.id == note_id)
                .with_for_update()
            )
            if note is None:
                raise NoteNotFoundError()
            evidence = await self._trusted_evidence_for_content(note_id, content)
            revision = await self._append_revision(
                note=note,
                source="USER_EDIT",
                content=content,
                evidence=evidence,
            )
            note.current_revision_id = revision.id
            await self._session.commit()
            return _revision_record_from_values(revision, evidence)
        except NoteNotFoundError:
            await self._session.rollback()
            raise
        except (IntegrityError, SQLAlchemyError):
            await self._session.rollback()
            raise NotePersistenceError() from None

    async def _append_revision(
        self,
        *,
        note: PaperNoteModel,
        source: str,
        content: PaperNoteContent,
        evidence: list[NoteEvidence],
    ) -> NoteRevisionModel:
        latest = await self._session.scalar(
            select(func.max(NoteRevisionModel.revision_number)).where(
                NoteRevisionModel.note_id == note.id
            )
        )
        revision = NoteRevisionModel(
            id=new_id(),
            note_id=note.id,
            revision_number=(latest or 0) + 1,
            source=source,
            content=content.model_dump(mode="json"),
        )
        self._session.add(revision)
        await self._session.flush()
        self._session.add_all(
            [
                NoteEvidenceModel(
                    revision_id=revision.id,
                    field_key=item.field_key,
                    chunk_id=item.chunk_id,
                    quote=item.quote,
                )
                for item in evidence
            ]
        )
        await self._session.flush()
        return revision

    async def _trusted_evidence_for_content(
        self, note_id: UUID, content: PaperNoteContent
    ) -> list[NoteEvidence]:
        requested = {
            (field.key, chunk_id)
            for field in content.fields
            for chunk_id in field.evidence_chunk_ids
        }
        if not requested:
            return []
        rows = (
            await self._session.execute(
                _evidence_select()
                .join(
                    NoteRevisionModel,
                    NoteRevisionModel.id == NoteEvidenceModel.revision_id,
                )
                .where(NoteRevisionModel.note_id == note_id)
            )
        ).all()
        trusted = {
            (row.field_key, row.chunk_id): _evidence_record(row) for row in rows
        }
        # Preserve template and citation order for stable API/UI rendering.
        # 保留模板与引用顺序，确保 API 和界面展示稳定。
        return [
            trusted[(field.key, chunk_id)]
            for field in content.fields
            for chunk_id in field.evidence_chunk_ids
            if (field.key, chunk_id) in trusted
        ]

    async def _require_ready_document(self, document_id: UUID) -> None:
        ready = await self._session.scalar(
            select(DocumentModel.id).where(
                DocumentModel.id == document_id,
                DocumentModel.status == DocumentStatus.READY,
                DocumentModel.current_version_id.is_not(None),
            )
        )
        if ready is None:
            raise NoteDocumentNotReadyError()

    async def _load_revision(self, revision_id: UUID) -> NoteRevisionRecord | None:
        model = await self._session.get(NoteRevisionModel, revision_id)
        return None if model is None else await self._revision_record(model)

    async def _revision_record(self, model: NoteRevisionModel) -> NoteRevisionRecord:
        rows = (
            await self._session.execute(
                _evidence_select().where(NoteEvidenceModel.revision_id == model.id)
            )
        ).all()
        return _revision_record_from_values(
            model, [_evidence_record(row) for row in rows]
        )


def _evidence_select():
    return (
        select(
            NoteEvidenceModel.field_key,
            NoteEvidenceModel.chunk_id,
            NoteEvidenceModel.quote,
            DocumentModel.id.label("document_id"),
            DocumentVersionModel.id.label("document_version_id"),
            DocumentModel.title,
            DocumentChunkModel.section_path,
            DocumentChunkModel.page_start,
            DocumentChunkModel.page_end,
        )
        .select_from(NoteEvidenceModel)
        .join(DocumentChunkModel, DocumentChunkModel.id == NoteEvidenceModel.chunk_id)
        .join(
            DocumentVersionModel,
            DocumentVersionModel.id == DocumentChunkModel.document_version_id,
        )
        .join(DocumentModel, DocumentModel.id == DocumentVersionModel.document_id)
    )


def _evidence_record(row: Any) -> NoteEvidence:
    return NoteEvidence(
        field_key=row.field_key,
        chunk_id=row.chunk_id,
        document_id=row.document_id,
        document_version_id=row.document_version_id,
        title=row.title,
        section_path=tuple(row.section_path),
        page_start=row.page_start,
        page_end=row.page_end,
        quote=row.quote,
    )


def _revision_record_from_values(
    model: NoteRevisionModel, evidence: list[NoteEvidence]
) -> NoteRevisionRecord:
    created_at = model.created_at or datetime.now().astimezone()
    return NoteRevisionRecord(
        id=model.id,
        note_id=model.note_id,
        revision_number=model.revision_number,
        source=cast(NoteRevisionSource, model.source),
        content=PaperNoteContent.model_validate(model.content),
        created_at=created_at,
        evidence=tuple(evidence),
    )


def _note_record(
    model: PaperNoteModel, current: NoteRevisionRecord | None
) -> PaperNoteRecord:
    return PaperNoteRecord(
        id=model.id,
        document_id=model.document_id,
        current_revision_id=model.current_revision_id,
        created_at=model.created_at,
        updated_at=model.updated_at,
        current_revision=current,
    )
