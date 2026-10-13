"""Add optional versioned paper notes.

Revision ID: 0006_notes
Revises: 0005_chat
Create Date: 2026-07-20
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0006_notes"
down_revision: str | None = "0005_chat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_note",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id"),
    )
    op.create_table(
        "note_revision",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision_number > 0", name="ck_note_revision_number_positive"
        ),
        sa.CheckConstraint(
            "source IN ('AI_DRAFT', 'USER_EDIT')", name="ck_note_revision_source"
        ),
        sa.ForeignKeyConstraint(["note_id"], ["paper_note.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "note_id", "revision_number", name="uq_note_revision_number"
        ),
    )
    op.create_foreign_key(
        "fk_paper_note_current_revision_id",
        "paper_note",
        "note_revision",
        ["current_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "note_evidence",
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_key", sa.Text(), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["document_chunk.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"], ["note_revision.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("revision_id", "field_key", "chunk_id"),
    )


def downgrade() -> None:
    op.drop_table("note_evidence")
    op.drop_constraint(
        "fk_paper_note_current_revision_id", "paper_note", type_="foreignkey"
    )
    op.drop_table("note_revision")
    op.drop_table("paper_note")
