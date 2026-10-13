from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import cast
from uuid import uuid4

import httpx
import pytest

from research_agent.discovery.arxiv import (
    ArxivClient,
    HttpArxivClient,
    parse_arxiv_feed,
)
from research_agent.discovery.domain import (
    ArxivPaper,
    ArxivSearchPage,
    ExternalPaperImportRecord,
)
from research_agent.discovery.repository import DiscoveryRepository
from research_agent.discovery.service import PaperDiscoveryService
from research_agent.papers.service import PaperService, UploadReceipt
from research_agent.shared.errors import AppError


ATOM_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>1</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <opensearch:itemsPerPage>1</opensearch:itemsPerPage>
  <entry>
    <id>http://arxiv.org/abs/2401.12345v2</id>
    <updated>2026-08-20T10:00:00Z</updated>
    <published>2026-08-01T10:00:00Z</published>
    <title>  A   Reliable Research Agent  </title>
    <summary>Long-running research conversations with evidence.</summary>
    <author><name>Alice Example</name></author>
    <author><name>Bob Example</name></author>
    <category term="cs.AI" scheme="http://arxiv.org/schemas/atom" />
    <arxiv:primary_category term="cs.AI" />
    <arxiv:license>https://creativecommons.org/licenses/by/4.0/</arxiv:license>
    <link href="http://arxiv.org/abs/2401.12345v2" rel="alternate" type="text/html" />
    <link title="pdf" href="http://arxiv.org/pdf/2401.12345v2" rel="related" type="application/pdf" />
  </entry>
</feed>"""


def arxiv_paper() -> ArxivPaper:
    return parse_arxiv_feed(ATOM_FEED, topic="research agents").items[0]


def test_atom_feed_is_parsed_into_safe_versioned_metadata() -> None:
    page = parse_arxiv_feed(ATOM_FEED, topic="research agents")

    assert page.total_results == 1
    assert page.items[0].arxiv_id == "2401.12345v2"
    assert page.items[0].title == "A Reliable Research Agent"
    assert page.items[0].authors == ["Alice Example", "Bob Example"]
    assert page.items[0].primary_category == "cs.AI"
    assert page.items[0].abstract_url.startswith("https://arxiv.org/")
    assert page.items[0].pdf_url.startswith("https://arxiv.org/")


def test_atom_parser_rejects_entity_declarations() -> None:
    with pytest.raises(AppError, match="invalid XML"):
        parse_arxiv_feed(
            b'<!DOCTYPE feed [<!ENTITY x "boom">]><feed>&x;</feed>',
            topic="unsafe",
        )


@pytest.mark.asyncio
async def test_http_search_requests_newest_submissions_and_requested_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["search_query"] == 'all:"research agents"'
        assert request.url.params["start"] == "10"
        assert request.url.params["max_results"] == "10"
        assert request.url.params["sortBy"] == "submittedDate"
        assert request.url.params["sortOrder"] == "descending"
        return httpx.Response(200, content=ATOM_FEED)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page = await HttpArxivClient(client).search(
            topic="research agents", start=10, limit=10
        )

    assert page.items[0].title == "A Reliable Research Agent"


class FakeDiscoveryRepository:
    def __init__(self) -> None:
        self.cache: dict[str, object] | None = None
        self.imported: ExternalPaperImportRecord | None = None
        self.reservations = 0

    async def get_cached_search(
        self, *, cache_key: str, now: datetime
    ) -> dict[str, object] | None:
        del cache_key, now
        return self.cache

    async def save_cached_search(
        self,
        *,
        cache_key: str,
        provider: str,
        query: str,
        response: dict[str, object],
        expires_at: datetime,
    ) -> None:
        del cache_key, provider, query, expires_at
        self.cache = response

    async def reserve_request(
        self,
        *,
        provider: str,
        now: datetime,
        interval: timedelta,
    ) -> datetime:
        del provider, interval
        self.reservations += 1
        return now

    async def get_import(
        self, *, provider: str, external_id: str, external_version: int
    ) -> ExternalPaperImportRecord | None:
        del provider, external_id, external_version
        return self.imported

    async def save_import(
        self, record: ExternalPaperImportRecord
    ) -> ExternalPaperImportRecord:
        self.imported = record
        return record


class FakeArxivClient:
    def __init__(self) -> None:
        self.paper = arxiv_paper()
        self.search_calls = 0
        self.lookup_calls = 0

    async def search(self, *, topic: str, start: int, limit: int) -> ArxivSearchPage:
        self.search_calls += 1
        return ArxivSearchPage(
            topic=topic,
            total_results=1,
            start=start,
            items_per_page=limit,
            items=[self.paper],
        )

    async def lookup(self, arxiv_id: str) -> ArxivPaper:
        del arxiv_id
        self.lookup_calls += 1
        return self.paper

    async def download_pdf(self, paper: ArxivPaper, *, max_bytes: int) -> BytesIO:
        del paper, max_bytes
        return BytesIO(b"%PDF-1.7\nselected paper")


class FakePaperService:
    def __init__(self) -> None:
        self.filename = ""

    async def upload_pdf(self, command) -> UploadReceipt:
        self.filename = command.filename
        assert command.stream.read(5) == b"%PDF-"
        return UploadReceipt(
            document_id=uuid4(),
            version_id=uuid4(),
            task_id=uuid4(),
            duplicate=False,
        )


def discovery_service(
    repository: FakeDiscoveryRepository,
    arxiv: FakeArxivClient,
    papers: FakePaperService | None = None,
) -> PaperDiscoveryService:
    return PaperDiscoveryService(
        cast(DiscoveryRepository, repository),
        cast(ArxivClient, arxiv),
        cast(PaperService, papers or FakePaperService()),
        clock=lambda: datetime(2026, 8, 29, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_search_uses_daily_cache_without_a_second_external_request() -> None:
    repository = FakeDiscoveryRepository()
    arxiv = FakeArxivClient()
    service = discovery_service(repository, arxiv)

    first = await service.search(topic=" research   agents ", limit=10)
    second = await service.search(topic="research agents", limit=10)

    assert first.cached is False
    assert second.cached is True
    assert arxiv.search_calls == 1
    assert repository.reservations == 1


@pytest.mark.asyncio
async def test_selected_search_result_downloads_once_and_enters_ingestion() -> None:
    repository = FakeDiscoveryRepository()
    arxiv = FakeArxivClient()
    papers = FakePaperService()
    service = discovery_service(repository, arxiv, papers)

    receipt = await service.import_paper("2401.12345v2")
    duplicate = await service.import_paper("2401.12345v2")

    assert receipt.arxiv_id == "2401.12345v2"
    assert receipt.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.document_id == receipt.document_id
    assert papers.filename == "arxiv-2401.12345-v2.pdf"
    assert arxiv.lookup_calls == 2
    assert repository.imported is not None
    assert repository.imported.external_id == "2401.12345"
    assert repository.imported.external_version == 2
