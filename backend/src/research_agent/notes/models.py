from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from research_agent.shared.db import Base


class PaperNoteModel(Base):
    __tablename__ = "paper_note"

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("document.id", ondelete="CASCADE"),
        unique=True,
    )
    current_revision_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "note_revision.id",
            name="fk_paper_note_current_revision_id",
            use_alter=True,
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NoteRevisionModel(Base):
    __tablename__ = "note_revision"
    __table_args__ = (
        UniqueConstraint("note_id", "revision_number", name="uq_note_revision_number"),
        CheckConstraint("revision_number > 0", name="ck_note_revision_number_positive"),
        CheckConstraint(
            "source IN ('AI_DRAFT', 'USER_EDIT')",
            name="ck_note_revision_source",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    note_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("paper_note.id", ondelete="CASCADE"),
    )
    revision_number: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16))
    content: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class NoteEvidenceModel(Base):
    __tablename__ = "note_evidence"

    revision_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("note_revision.id", ondelete="CASCADE"),
        primary_key=True,
    )
    field_key: Mapped[str] = mapped_column(Text, primary_key=True)
    chunk_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("document_chunk.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    quote: Mapped[str] = mapped_column(Text)
