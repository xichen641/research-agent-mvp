"""Add structure-aware document chunks and lexical search vectors.

Revision ID: 0003_chunks
Revises: 0002_parse_artifacts
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0003_chunks"
down_revision: str | None = "0002_parse_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_chunk",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section_path", postgresql.JSONB(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("lexical_text", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("element_ids", postgresql.JSONB(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("pipeline_version", sa.String(length=128), nullable=False),
        sa.Column(
            "search_vector_en",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', content)", persisted=True),
            nullable=False,
        ),
        sa.Column(
            "search_vector_zh",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', lexical_text)", persisted=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_version.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_version_id",
            "chunk_index",
            "pipeline_version",
            name="uq_chunk_version_index_pipeline",
        ),
    )
    op.create_index(
        "ix_chunk_search_en",
        "document_chunk",
        ["search_vector_en"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_chunk_search_zh",
        "document_chunk",
        ["search_vector_zh"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_chunk_search_zh", table_name="document_chunk")
    op.drop_index("ix_chunk_search_en", table_name="document_chunk")
    op.drop_table("document_chunk")
