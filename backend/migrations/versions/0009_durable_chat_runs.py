"""Add durable chat input snapshots and worker leases.

Revision ID: 0009_durable_chat_runs
Revises: 0008_paper_discovery
Create Date: 2026-08-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0009_durable_chat_runs"
down_revision: str | None = "0008_paper_discovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_chat_run_status", "chat_run", type_="check")
    op.create_check_constraint(
        "ck_chat_run_status",
        "chat_run",
        "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')",
    )
    op.add_column("chat_run", sa.Column("question", sa.Text(), nullable=True))
    op.add_column(
        "chat_run",
        sa.Column(
            "scope",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "chat_run",
        sa.Column(
            "retrieval_mode",
            sa.String(length=16),
            server_default="auto",
            nullable=False,
        ),
    )
    op.add_column(
        "chat_run",
        sa.Column(
            "history_message_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "chat_run",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_chat_run_retrieval_mode",
        "chat_run",
        "retrieval_mode IN ('auto', 'off', 'on')",
    )
    op.create_check_constraint(
        "ck_chat_run_history_message_count",
        "chat_run",
        "history_message_count >= 0",
    )
    op.create_index(
        "uq_chat_run_one_active_per_session",
        "chat_run",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING')"),
    )
    op.execute(
        "UPDATE chat_run AS run SET scope = session.scope "
        "FROM chat_session AS session WHERE session.id = run.session_id"
    )


def downgrade() -> None:
    op.drop_index("uq_chat_run_one_active_per_session", table_name="chat_run")
    op.drop_constraint("ck_chat_run_history_message_count", "chat_run", type_="check")
    op.drop_constraint("ck_chat_run_retrieval_mode", "chat_run", type_="check")
    op.drop_column("chat_run", "lease_expires_at")
    op.drop_column("chat_run", "history_message_count")
    op.drop_column("chat_run", "retrieval_mode")
    op.drop_column("chat_run", "scope")
    op.drop_column("chat_run", "question")
    op.drop_constraint("ck_chat_run_status", "chat_run", type_="check")
    op.create_check_constraint(
        "ck_chat_run_status",
        "chat_run",
        "status IN ('RUNNING', 'SUCCEEDED', 'FAILED')",
    )
