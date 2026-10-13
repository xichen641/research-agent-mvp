"""Add versioned embedding spaces and chunk vectors.

Revision ID: 0004_embeddings
Revises: 0003_chunks
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0004_embeddings"
down_revision: str | None = "0003_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "embedding_space",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(length=256), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("pipeline_version", sa.String(length=128), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "model_id",
            "dimension",
            "pipeline_version",
            name="uq_embedding_space_identity",
        ),
    )
    op.create_index(
        "uq_embedding_space_active",
        "embedding_space",
        ["active"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_table(
        "chunk_embedding",
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("embedding_space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["document_chunk.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["embedding_space_id"], ["embedding_space.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("chunk_id", "embedding_space_id"),
    )
    op.create_index(
        "ix_chunk_embedding_hnsw",
        "chunk_embedding",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_chunk_embedding_hnsw", table_name="chunk_embedding")
    op.drop_table("chunk_embedding")
    op.drop_index("uq_embedding_space_active", table_name="embedding_space")
    op.drop_table("embedding_space")
