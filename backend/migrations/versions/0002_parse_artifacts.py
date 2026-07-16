"""Add nullable parsed artifact metadata to document versions.

Revision ID: 0002_parse_artifacts
Revises: 0001_papers_tasks
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002_parse_artifacts"
down_revision: str | None = "0001_papers_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_version",
        sa.Column("parsed_json_object_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "document_version",
        sa.Column("parsed_markdown_object_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "document_version",
        sa.Column(
            "image_object_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "document_version",
        sa.Column(
            "parse_quality",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("document_version", "parse_quality")
    op.drop_column("document_version", "image_object_keys")
    op.drop_column("document_version", "parsed_markdown_object_key")
    op.drop_column("document_version", "parsed_json_object_key")
