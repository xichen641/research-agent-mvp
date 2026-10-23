"""Stable chat message sequence, request deduplication and retry snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_chat_idempotency"
down_revision: str | None = "0010_research_spaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_message", sa.Column("sequence", sa.Integer(), nullable=True))
    op.execute(
        "WITH ordered AS (SELECT id, row_number() OVER (PARTITION BY session_id ORDER BY created_at, id) AS position FROM chat_message) UPDATE chat_message SET sequence = ordered.position FROM ordered WHERE chat_message.id = ordered.id"
    )
    op.alter_column("chat_message", "sequence", nullable=False)
    op.create_unique_constraint(
        "uq_chat_message_session_sequence", "chat_message", ["session_id", "sequence"]
    )
    op.create_check_constraint(
        "ck_chat_message_sequence", "chat_message", "sequence > 0"
    )
    for name in (
        "client_request_id",
        "user_message_id",
        "assistant_message_id",
        "retry_of_run_id",
    ):
        op.add_column(
            "chat_run", sa.Column(name, postgresql.UUID(as_uuid=True), nullable=True)
        )
    for name in ("memory_snapshot", "context_snapshot"):
        op.add_column("chat_run", sa.Column(name, postgresql.JSONB(), nullable=True))
    op.create_unique_constraint(
        "uq_chat_run_client_request", "chat_run", ["session_id", "client_request_id"]
    )
    op.create_foreign_key(
        "fk_chat_run_user_message",
        "chat_run",
        "chat_message",
        ["user_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_chat_run_assistant_message",
        "chat_run",
        "chat_message",
        ["assistant_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_chat_run_retry_of",
        "chat_run",
        "chat_run",
        ["retry_of_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute(
        "UPDATE chat_run r SET user_message_id = m.id FROM chat_message m WHERE m.session_id = r.session_id AND m.sequence = r.history_message_count + 1 AND m.role = 'user'"
    )
    op.execute(
        "UPDATE chat_run r SET assistant_message_id = m.id FROM chat_run_event e, chat_message m WHERE e.run_id = r.id AND e.event_type = 'run_completed' AND e.payload->>'message_id' = m.id::text AND m.role = 'assistant' AND m.session_id = r.session_id"
    )


def downgrade() -> None:
    for name in (
        "fk_chat_run_retry_of",
        "fk_chat_run_assistant_message",
        "fk_chat_run_user_message",
    ):
        op.drop_constraint(name, "chat_run", type_="foreignkey")
    op.drop_constraint("uq_chat_run_client_request", "chat_run", type_="unique")
    for name in (
        "context_snapshot",
        "memory_snapshot",
        "retry_of_run_id",
        "assistant_message_id",
        "user_message_id",
        "client_request_id",
    ):
        op.drop_column("chat_run", name)
    op.drop_constraint("ck_chat_message_sequence", "chat_message", type_="check")
    op.drop_constraint(
        "uq_chat_message_session_sequence", "chat_message", type_="unique"
    )
    op.drop_column("chat_message", "sequence")
