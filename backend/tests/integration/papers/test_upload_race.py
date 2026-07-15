import asyncio
from datetime import timedelta
from io import BytesIO
from threading import Lock
from typing import BinaryIO

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.papers.domain import DocumentRecord, UploadIds
from research_agent.papers.models import (
    DocumentModel,
    DocumentTaskModel,
    DocumentVersionModel,
    OutboxEventModel,
)
from research_agent.papers.repository import SqlPaperRepository
from research_agent.papers.service import PaperService, UploadPaperCommand
from research_agent.shared.ids import EntityId


class RaceObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[str] = []
        self.delete_calls: list[str] = []
        self._lock = Lock()

    def put(self, *, key: str, stream: BinaryIO, content_type: str) -> None:
        assert content_type == "application/pdf"
        content = stream.read()
        with self._lock:
            self.objects[key] = content
            self.put_calls.append(key)

    def get(self, *, key: str) -> BinaryIO:
        return BytesIO(self.objects[key])

    def delete(self, *, key: str) -> None:
        with self._lock:
            self.objects.pop(key, None)
            self.delete_calls.append(key)

    def presigned_get(self, *, key: str, expires: timedelta) -> str:
        del expires
        return f"https://example.test/{key}"


class BarrierPaperRepository:
    def __init__(
        self,
        repository: SqlPaperRepository,
        barrier: asyncio.Barrier,
    ) -> None:
        self._repository = repository
        self._barrier = barrier
        self._first_lookup = True

    async def find_by_sha256(self, sha256: str) -> DocumentRecord | None:
        result = await self._repository.find_by_sha256(sha256)
        if self._first_lookup:
            self._first_lookup = False
            await self._barrier.wait()
        return result

    async def create_upload(
        self,
        *,
        ids: UploadIds,
        title: str,
        filename: str,
        sha256: str,
        source_object_key: str,
        parser_name: str,
        parser_version: str,
        pipeline_version: str,
    ) -> None:
        await self._repository.create_upload(
            ids=ids,
            title=title,
            filename=filename,
            sha256=sha256,
            source_object_key=source_object_key,
            parser_name=parser_name,
            parser_version=parser_version,
            pipeline_version=pipeline_version,
        )

    async def add_outbox_event(
        self,
        *,
        event_id: EntityId,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        await self._repository.add_outbox_event(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
        )

    async def commit(self) -> None:
        await self._repository.commit()

    async def rollback(self) -> None:
        await self._repository.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_equal_uploads_recover_unique_constraint_loser(
    independent_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    barrier = asyncio.Barrier(2)
    storage = RaceObjectStorage()
    content = b"%PDF-1.7\nforced-race"

    async with (
        independent_session_factory() as first_session,
        independent_session_factory() as second_session,
    ):
        first_service = PaperService(
            BarrierPaperRepository(SqlPaperRepository(first_session), barrier),
            storage,
        )
        second_service = PaperService(
            BarrierPaperRepository(SqlPaperRepository(second_session), barrier),
            storage,
        )
        first, second = await asyncio.gather(
            first_service.upload_pdf(
                UploadPaperCommand(
                    "first.pdf", "application/pdf", BytesIO(content)
                )
            ),
            second_service.upload_pdf(
                UploadPaperCommand(
                    "second.pdf", "application/pdf", BytesIO(content)
                )
            ),
        )

    receipts = [first, second]
    assert sum(not receipt.duplicate for receipt in receipts) == 1
    assert sum(receipt.duplicate for receipt in receipts) == 1
    assert {receipt.document_id for receipt in receipts} == {first.document_id}
    assert {receipt.version_id for receipt in receipts} == {first.version_id}
    assert {receipt.task_id for receipt in receipts} == {first.task_id}
    winner = next(receipt for receipt in receipts if not receipt.duplicate)
    winner_key = (
        f"papers/{winner.document_id}/source/{winner.version_id}.pdf"
    )
    assert len(storage.put_calls) == 2
    assert len(storage.delete_calls) == 1
    losing_key = next(key for key in storage.put_calls if key != winner_key)
    assert storage.delete_calls == [losing_key]
    assert set(storage.objects) == {winner_key}
    assert storage.objects[winner_key] == content

    async with independent_session_factory() as verification_session:
        counts = []
        for model in (
            DocumentModel,
            DocumentVersionModel,
            DocumentTaskModel,
            OutboxEventModel,
        ):
            counts.append(
                await verification_session.scalar(
                    select(func.count()).select_from(model)
                )
            )
        durable_version = await verification_session.get(
            DocumentVersionModel,
            winner.version_id,
        )
    assert counts == [1, 1, 1, 1]
    assert durable_version is not None
    assert durable_version.source_object_key == winner_key
    assert durable_version.source_object_key in storage.objects
