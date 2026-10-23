"""Add research spaces, pinned materials and optional legacy session association."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_research_spaces"
down_revision: str | None = "0009_durable_chat_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_space",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *[
            sa.Column(name, sa.Text(), nullable=False)
            for name in (
                "name",
                "goal",
                "task_settings",
                "resource_constraints",
                "current_question",
            )
        ],
        sa.Column("revision", sa.Integer(), nullable=False),
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
        sa.CheckConstraint("revision > 0", name="ck_research_space_revision"),
    )
    op.create_table(
        "research_space_material",
        sa.Column(
            "space_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_space.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_version.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "space_id", "document_id", name="uq_research_space_document"
        ),
    )
    op.add_column(
        "chat_session",
        sa.Column("research_space_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_chat_session_research_space",
        "chat_session",
        "research_space",
        ["research_space_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_chat_session_research_space_id", "chat_session", ["research_space_id"]
    )
    op.add_column(
        "chat_run", sa.Column("research_context", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("chat_run", "research_context")
    op.drop_index("ix_chat_session_research_space_id", table_name="chat_session")
    op.drop_constraint(
        "fk_chat_session_research_space", "chat_session", type_="foreignkey"
    )
    op.drop_column("chat_session", "research_space_id")
    op.drop_table("research_space_material")
    op.drop_table("research_space")
