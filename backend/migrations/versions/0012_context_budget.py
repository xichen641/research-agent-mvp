"""Retain private immutable model inputs and incremental memory boundaries."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_context_budget"
down_revision: str | None = "0011_chat_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_run", sa.Column("request_snapshots", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "chat_memory",
        sa.Column(
            "covered_through_sequence", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "chat_memory",
        sa.Column(
            "critical_constraints",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.execute(
        "WITH ordered AS (SELECT session_id, sequence, row_number() OVER (PARTITION BY session_id ORDER BY sequence) AS position FROM chat_message) UPDATE chat_memory m SET covered_through_sequence = o.sequence FROM ordered o WHERE o.session_id = m.session_id AND o.position = m.covered_message_count"
    )
    op.alter_column("chat_memory", "covered_through_sequence", server_default=None)
    op.alter_column("chat_memory", "critical_constraints", server_default=None)


def downgrade() -> None:
    op.drop_column("chat_memory", "critical_constraints")
    op.drop_column("chat_memory", "covered_through_sequence")
    op.drop_column("chat_run", "request_snapshots")
