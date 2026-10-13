from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
import httpx
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.config import get_settings
from research_agent.discovery.arxiv import ArxivClient, HttpArxivClient
from research_agent.discovery.domain import ArxivSearchPage
from research_agent.discovery.repository import SqlDiscoveryRepository
from research_agent.discovery.service import PaperDiscoveryService
from research_agent.papers.routes import get_db_session, get_paper_service
from research_agent.papers.service import PaperService


router = APIRouter(prefix="/discovery/arxiv")


class ImportArxivPaperRequest(BaseModel):
    arxiv_id: str = Field(min_length=1, max_length=128)


class ImportArxivPaperResponse(BaseModel):
    arxiv_id: str
    document_id: UUID
    document_version_id: UUID
    task_id: UUID
    duplicate: bool


async def get_arxiv_client() -> AsyncIterator[ArxivClient]:
    settings = get_settings()
    limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    async with httpx.AsyncClient(
        timeout=settings.arxiv_timeout_seconds,
        limits=limits,
        headers={"User-Agent": settings.arxiv_user_agent},
        follow_redirects=True,
    ) as client:
        yield HttpArxivClient(client, api_url=settings.arxiv_api_url)


def get_discovery_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    arxiv: Annotated[ArxivClient, Depends(get_arxiv_client)],
    papers: Annotated[PaperService, Depends(get_paper_service)],
) -> PaperDiscoveryService:
    settings = get_settings()
    return PaperDiscoveryService(
        SqlDiscoveryRepository(session),
        arxiv,
        papers,
        cache_ttl=timedelta(hours=settings.arxiv_cache_hours),
        max_pdf_bytes=settings.arxiv_max_pdf_bytes,
    )


@router.get("/search", response_model=ArxivSearchPage)
async def search_arxiv(
    service: Annotated[PaperDiscoveryService, Depends(get_discovery_service)],
    topic: Annotated[str, Query(min_length=1, max_length=500)],
    start: Annotated[int, Query(ge=0, le=10_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> ArxivSearchPage:
    return await service.search(topic=topic, start=start, limit=limit)


@router.post(
    "/import",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ImportArxivPaperResponse,
)
async def import_arxiv_paper(
    body: ImportArxivPaperRequest,
    service: Annotated[PaperDiscoveryService, Depends(get_discovery_service)],
) -> ImportArxivPaperResponse:
    receipt = await service.import_paper(body.arxiv_id)
    return ImportArxivPaperResponse.model_validate(receipt, from_attributes=True)
