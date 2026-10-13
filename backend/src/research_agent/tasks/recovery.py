from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.chat.models import ChatRunModel
from research_agent.papers.models import OutboxEventModel
from research_agent.shared.errors import AppError
from research_agent.shared.ids import new_id


class SqlChatRunRecovery:
    """Requeue chat runs whose worker lease expired without a terminal event."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def requeue_expired(self, *, limit: int = 20) -> int:
        if limit <= 0:
            raise ValueError("recovery batch size must be positive")
        try:
            runs = (
                await self._session.scalars(
                    select(ChatRunModel)
                    .where(
                        ChatRunModel.status == "RUNNING",
                        ChatRunModel.lease_expires_at.is_not(None),
                        ChatRunModel.lease_expires_at < datetime.now(UTC),
                    )
                    .order_by(ChatRunModel.lease_expires_at, ChatRunModel.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            now = datetime.now(UTC)
            for run in runs:
                run.status = "QUEUED"
                run.lease_expires_at = None
                self._session.add(
                    OutboxEventModel(
                        id=new_id(),
                        event_type="chat.run.requested",
                        payload={"run_id": str(run.id)},
                        attempts=0,
                        available_at=now,
                        sent_at=None,
                    )
                )
            await self._session.commit()
        except SQLAlchemyError:
            await self._session.rollback()
            raise AppError(
                "CHAT_RECOVERY_FAILED",
                "Expired chat runs could not be recovered",
                retryable=True,
            ) from None
        return len(runs)
