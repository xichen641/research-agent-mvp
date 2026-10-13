from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.discovery.domain import ExternalPaperImportRecord
from research_agent.discovery.models import (
    ExternalApiThrottleModel,
    ExternalPaperSourceModel,
    PaperDiscoveryCacheModel,
)
from research_agent.shared.errors import AppError


class DiscoveryPersistenceError(AppError):
    def __init__(self) -> None:
        super().__init__(
            "DISCOVERY_PERSISTENCE_FAILED",
            "Paper discovery state could not be persisted",
            retryable=True,
        )


class DiscoveryRepository(Protocol):
    async def get_cached_search(
        self, *, cache_key: str, now: datetime
    ) -> dict[str, object] | None: ...

    async def save_cached_search(
        self,
        *,
        cache_key: str,
        provider: str,
        query: str,
        response: dict[str, object],
        expires_at: datetime,
    ) -> None: ...

    async def reserve_request(
        self,
        *,
        provider: str,
        now: datetime,
        interval: timedelta,
    ) -> datetime: ...

    async def get_import(
        self, *, provider: str, external_id: str, external_version: int
    ) -> ExternalPaperImportRecord | None: ...

    async def save_import(
        self, record: ExternalPaperImportRecord
    ) -> ExternalPaperImportRecord: ...


class SqlDiscoveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_cached_search(
        self, *, cache_key: str, now: datetime
    ) -> dict[str, object] | None:
        statement = select(PaperDiscoveryCacheModel).where(
            PaperDiscoveryCacheModel.cache_key == cache_key,
            PaperDiscoveryCacheModel.expires_at > now,
        )
        try:
            model = (await self._session.scalars(statement)).one_or_none()
        except SQLAlchemyError:
            raise DiscoveryPersistenceError() from None
        return None if model is None else model.response

    async def save_cached_search(
        self,
        *,
        cache_key: str,
        provider: str,
        query: str,
        response: dict[str, object],
        expires_at: datetime,
    ) -> None:
        statement = insert(PaperDiscoveryCacheModel).values(
            cache_key=cache_key,
            provider=provider,
            query=query,
            response=response,
            expires_at=expires_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[PaperDiscoveryCacheModel.cache_key],
            set_={
                "provider": provider,
                "query": query,
                "response": response,
                "expires_at": expires_at,
            },
        )
        try:
            await self._session.execute(statement)
            await self._session.commit()
        except SQLAlchemyError:
            await self._rollback()
            raise DiscoveryPersistenceError() from None

    async def reserve_request(
        self,
        *,
        provider: str,
        now: datetime,
        interval: timedelta,
    ) -> datetime:
        if interval.total_seconds() <= 0:
            raise ValueError("request interval must be positive")
        try:
            await self._session.execute(
                insert(ExternalApiThrottleModel)
                .values(provider=provider, next_allowed_at=now)
                .on_conflict_do_nothing(
                    index_elements=[ExternalApiThrottleModel.provider]
                )
            )
            model = (
                await self._session.scalars(
                    select(ExternalApiThrottleModel)
                    .where(ExternalApiThrottleModel.provider == provider)
                    .with_for_update()
                )
            ).one()
            scheduled_at = max(now, model.next_allowed_at)
            model.next_allowed_at = scheduled_at + interval
            await self._session.commit()
        except SQLAlchemyError:
            await self._rollback()
            raise DiscoveryPersistenceError() from None
        return scheduled_at

    async def get_import(
        self, *, provider: str, external_id: str, external_version: int
    ) -> ExternalPaperImportRecord | None:
        try:
            model = await self._session.get(
                ExternalPaperSourceModel,
                (provider, external_id, external_version),
            )
        except SQLAlchemyError:
            raise DiscoveryPersistenceError() from None
        return None if model is None else _import_record(model)

    async def save_import(
        self, record: ExternalPaperImportRecord
    ) -> ExternalPaperImportRecord:
        statement = (
            insert(ExternalPaperSourceModel)
            .values(
                provider=record.provider,
                external_id=record.external_id,
                external_version=record.external_version,
                document_id=record.document_id,
                document_version_id=record.document_version_id,
                task_id=record.task_id,
                metadata_json=record.metadata,
                license_url=record.license_url,
                imported_at=record.imported_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    ExternalPaperSourceModel.provider,
                    ExternalPaperSourceModel.external_id,
                    ExternalPaperSourceModel.external_version,
                ]
            )
        )
        try:
            await self._session.execute(statement)
            await self._session.commit()
        except SQLAlchemyError:
            await self._rollback()
            raise DiscoveryPersistenceError() from None
        winner = await self.get_import(
            provider=record.provider,
            external_id=record.external_id,
            external_version=record.external_version,
        )
        if winner is None:  # pragma: no cover - protected by the insert contract
            raise DiscoveryPersistenceError()
        return winner

    async def _rollback(self) -> None:
        try:
            await self._session.rollback()
        except SQLAlchemyError:
            pass


def _import_record(model: ExternalPaperSourceModel) -> ExternalPaperImportRecord:
    return ExternalPaperImportRecord(
        provider=model.provider,
        external_id=model.external_id,
        external_version=model.external_version,
        document_id=model.document_id,
        document_version_id=model.document_version_id,
        task_id=model.task_id,
        metadata=model.metadata_json,
        license_url=model.license_url,
        imported_at=model.imported_at,
    )
