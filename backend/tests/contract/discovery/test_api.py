from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from research_agent.discovery.domain import (
    ArxivImportReceipt,
    ArxivPaper,
    ArxivSearchPage,
)
from research_agent.discovery.routes import get_discovery_service
from research_agent.main import create_app


class FakeDiscoveryService:
    async def search(
        self, *, topic: str, start: int = 0, limit: int = 10
    ) -> ArxivSearchPage:
        now = datetime.now(UTC)
        return ArxivSearchPage(
            topic=topic,
            total_results=1,
            start=start,
            items_per_page=limit,
            items=[
                ArxivPaper(
                    arxiv_id="2401.12345v2",
                    title="Research memory",
                    abstract="Durable compressed context.",
                    authors=["Alice Example"],
                    categories=["cs.AI"],
                    primary_category="cs.AI",
                    published_at=now,
                    updated_at=now,
                    abstract_url="https://arxiv.org/abs/2401.12345v2",
                    pdf_url="https://arxiv.org/pdf/2401.12345v2",
                )
            ],
        )

    async def import_paper(self, arxiv_id: str) -> ArxivImportReceipt:
        return ArxivImportReceipt(
            arxiv_id=arxiv_id,
            document_id=uuid4(),
            document_version_id=uuid4(),
            task_id=uuid4(),
            duplicate=False,
        )


@pytest.mark.asyncio
async def test_search_and_explicit_import_contracts() -> None:
    app = create_app(web_origin="http://web.test")
    app.dependency_overrides[get_discovery_service] = FakeDiscoveryService
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        search = await client.get(
            "/api/v1/discovery/arxiv/search",
            params={"topic": "agent memory", "start": 10},
        )
        imported = await client.post(
            "/api/v1/discovery/arxiv/import",
            json={"arxiv_id": "2401.12345v2"},
        )

    assert search.status_code == 200
    assert search.json()["start"] == 10
    assert search.json()["items_per_page"] == 10
    assert search.json()["items"][0]["arxiv_id"] == "2401.12345v2"
    assert imported.status_code == 202
    assert imported.json()["arxiv_id"] == "2401.12345v2"
    assert imported.json()["duplicate"] is False
