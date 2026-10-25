"""Correct terminal progress for previously activated document tasks."""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_ready_task_progress"
down_revision: str | None = "0012_context_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Activation already verified embedding completeness before setting READY.
    # Repair only the old terminal state without reprocessing document data.
    op.execute(
        """
        UPDATE document_task AS task
        SET stage = 'VERIFYING', progress_current = progress_total
        FROM document_version AS version
        WHERE task.document_version_id = version.id
          AND version.status = 'READY'
          AND task.status = 'READY'
          AND task.stage = 'EMBEDDING'
          AND task.progress_current = 6
          AND task.progress_total = 8
        """
    )


def downgrade() -> None:
    # This data correction remains valid with older code; do not restore the bug.
    pass
