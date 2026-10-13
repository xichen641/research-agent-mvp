import asyncio
from collections.abc import Awaitable, Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
import hashlib

from research_agent.discovery.arxiv import ArxivClient
from research_agent.discovery.domain import (
    ArxivImportReceipt,
    ArxivSearchPage,
    ExternalPaperImportRecord,
    normalize_arxiv_id,
    split_arxiv_id,
)
from research_agent.discovery.repository import DiscoveryRepository
from research_agent.papers.service import PaperService, UploadPaperCommand


Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]


class PaperDiscoveryService:
    def __init__(
        self,
        repository: DiscoveryRepository,
        arxiv: ArxivClient,
        papers: PaperService,
        *,
        clock: Clock = lambda: datetime.now(UTC),
        sleep: Sleep = asyncio.sleep,
        cache_ttl: timedelta = timedelta(hours=24),
        request_interval: timedelta = timedelta(seconds=3),
        max_pdf_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        if cache_ttl.total_seconds() <= 0:
            raise ValueError("discovery cache TTL must be positive")
        if request_interval.total_seconds() < 3:
            raise ValueError("arXiv request interval must be at least three seconds")
        if max_pdf_bytes <= 0:
            raise ValueError("maximum arXiv PDF size must be positive")
        self._repository = repository
        self._arxiv = arxiv
        self._papers = papers
        self._clock = clock
        self._sleep = sleep
        self._cache_ttl = cache_ttl
        self._request_interval = request_interval
        self._max_pdf_bytes = max_pdf_bytes

    async def search(
        self, *, topic: str, start: int = 0, limit: int = 10
    ) -> ArxivSearchPage:
        normalized_topic = " ".join(topic.split())
        cache_key = _search_cache_key(normalized_topic, start, limit)
        now = self._clock()
        cached = await self._repository.get_cached_search(cache_key=cache_key, now=now)
        if cached is not None:
            return ArxivSearchPage.model_validate(cached).model_copy(
                update={"cached": True}
            )

        await self._wait_for_slot(now)
        result = await self._arxiv.search(
            topic=normalized_topic, start=start, limit=limit
        )
        await self._repository.save_cached_search(
            cache_key=cache_key,
            provider="arxiv",
            query=normalized_topic,
            response=result.model_dump(mode="json"),
            expires_at=self._clock() + self._cache_ttl,
        )
        return result

    async def import_paper(self, arxiv_id: str) -> ArxivImportReceipt:
        normalized_id = normalize_arxiv_id(arxiv_id)
        await self._wait_for_slot(self._clock())
        paper = await self._arxiv.lookup(normalized_id)
        external_id, external_version = split_arxiv_id(paper.arxiv_id)
        existing = await self._repository.get_import(
            provider="arxiv",
            external_id=external_id,
            external_version=external_version,
        )
        if existing is not None:
            return _receipt(existing, paper.arxiv_id, duplicate=True)

        with closing(
            await self._arxiv.download_pdf(paper, max_bytes=self._max_pdf_bytes)
        ) as pdf:
            filename_id = external_id.replace("/", "_")
            uploaded = await self._papers.upload_pdf(
                UploadPaperCommand(
                    filename=f"arxiv-{filename_id}-v{external_version}.pdf",
                    content_type="application/pdf",
                    stream=pdf,
                )
            )
        record = ExternalPaperImportRecord(
            provider="arxiv",
            external_id=external_id,
            external_version=external_version,
            document_id=uploaded.document_id,
            document_version_id=uploaded.version_id,
            task_id=uploaded.task_id,
            metadata=paper.model_dump(mode="json"),
            license_url=paper.license_url,
            imported_at=self._clock(),
        )
        winner = await self._repository.save_import(record)
        return _receipt(
            winner,
            paper.arxiv_id,
            duplicate=uploaded.duplicate or winner != record,
        )

    async def _wait_for_slot(self, now: datetime) -> None:
        scheduled_at = await self._repository.reserve_request(
            provider="arxiv",
            now=now,
            interval=self._request_interval,
        )
        delay = (scheduled_at - now).total_seconds()
        if delay > 0:
            await self._sleep(delay)


def _search_cache_key(topic: str, start: int, limit: int) -> str:
    value = f"arxiv-submitted-date-v1\0{topic.casefold()}\0{start}\0{limit}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _receipt(
    record: ExternalPaperImportRecord, arxiv_id: str, *, duplicate: bool
) -> ArxivImportReceipt:
    return ArxivImportReceipt(
        arxiv_id=arxiv_id,
        document_id=record.document_id,
        document_version_id=record.document_version_id,
        task_id=record.task_id,
        duplicate=duplicate,
    )
