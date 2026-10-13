from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.chat.models import ChatRunModel
from research_agent.papers.models import DocumentTaskModel, OutboxEventModel
from research_agent.shared.errors import AppError


CHAT_STATUSES = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED")
TASK_STATUSES = ("QUEUED", "RUNNING", "READY", "FAILED", "CANCELLED")


@dataclass(frozen=True, slots=True)
class OperationalSnapshot:
    outbox_pending: int
    outbox_attempts: int
    outbox_oldest_age_seconds: float
    expired_chat_leases: int
    chat_runs: dict[str, int]
    document_tasks: dict[str, int]


class SqlOperationalMetrics:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def collect(self) -> OperationalSnapshot:
        try:
            pending, attempts, oldest = (
                await self._session.execute(
                    select(
                        func.count(OutboxEventModel.id),
                        func.coalesce(func.sum(OutboxEventModel.attempts), 0),
                        func.coalesce(
                            func.extract(
                                "epoch",
                                func.clock_timestamp()
                                - func.min(OutboxEventModel.available_at),
                            ),
                            0.0,
                        ),
                    ).where(OutboxEventModel.sent_at.is_(None))
                )
            ).one()
            expired = await self._session.scalar(
                select(func.count(ChatRunModel.id)).where(
                    ChatRunModel.status == "RUNNING",
                    ChatRunModel.lease_expires_at.is_not(None),
                    ChatRunModel.lease_expires_at < func.clock_timestamp(),
                )
            )
            chat_counts = await self._status_counts(ChatRunModel)
            task_counts = await self._status_counts(DocumentTaskModel)
            await self._session.commit()
        except SQLAlchemyError:
            await self._session.rollback()
            raise AppError(
                "METRICS_UNAVAILABLE",
                "Operational metrics could not be collected",
                retryable=True,
            ) from None
        return OperationalSnapshot(
            outbox_pending=int(pending),
            outbox_attempts=int(attempts),
            outbox_oldest_age_seconds=max(0.0, float(oldest)),
            expired_chat_leases=int(expired or 0),
            chat_runs={status: chat_counts.get(status, 0) for status in CHAT_STATUSES},
            document_tasks={
                status: task_counts.get(status, 0) for status in TASK_STATUSES
            },
        )

    async def _status_counts(
        self, model: type[ChatRunModel] | type[DocumentTaskModel]
    ) -> dict[str, int]:
        rows = (
            await self._session.execute(
                select(model.status, func.count(model.id)).group_by(model.status)
            )
        ).all()
        return {str(status): int(count) for status, count in rows}


async def collect_operational_metrics(
    session_factory: async_sessionmaker[AsyncSession],
) -> str:
    async with session_factory() as session:
        snapshot = await SqlOperationalMetrics(session).collect()
    return render_prometheus(snapshot)


def render_prometheus(snapshot: OperationalSnapshot) -> str:
    lines = [
        "# HELP research_agent_up ResearchAgent metrics collector availability.",
        "# TYPE research_agent_up gauge",
        "research_agent_up 1",
        "# HELP research_agent_outbox_pending Durable events waiting for dispatch.",
        "# TYPE research_agent_outbox_pending gauge",
        f"research_agent_outbox_pending {snapshot.outbox_pending}",
        "# HELP research_agent_outbox_attempts Dispatch attempts across pending events.",
        "# TYPE research_agent_outbox_attempts gauge",
        f"research_agent_outbox_attempts {snapshot.outbox_attempts}",
        "# HELP research_agent_outbox_oldest_age_seconds Age of the oldest pending event.",
        "# TYPE research_agent_outbox_oldest_age_seconds gauge",
        (
            "research_agent_outbox_oldest_age_seconds "
            f"{snapshot.outbox_oldest_age_seconds:.3f}"
        ),
        "# HELP research_agent_chat_expired_leases Chat runs needing recovery.",
        "# TYPE research_agent_chat_expired_leases gauge",
        f"research_agent_chat_expired_leases {snapshot.expired_chat_leases}",
        "# HELP research_agent_chat_runs Durable chat runs by status.",
        "# TYPE research_agent_chat_runs gauge",
    ]
    lines.extend(
        f'research_agent_chat_runs{{status="{status}"}} {count}'
        for status, count in snapshot.chat_runs.items()
    )
    lines.extend(
        [
            "# HELP research_agent_document_tasks Durable ingestion tasks by status.",
            "# TYPE research_agent_document_tasks gauge",
        ]
    )
    lines.extend(
        f'research_agent_document_tasks{{status="{status}"}} {count}'
        for status, count in snapshot.document_tasks.items()
    )
    return "\n".join(lines) + "\n"
