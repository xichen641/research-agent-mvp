from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.config import get_settings
from research_agent.papers.repository import SqlPaperRepository
from research_agent.papers.service import PaperService, UploadPaperCommand
from research_agent.papers.storage import MinioObjectStorage, ObjectStorage
from research_agent.shared.db import async_session_factory
from research_agent.shared.ids import EntityId


router = APIRouter()


class UploadResponse(BaseModel):
    document_id: EntityId
    version_id: EntityId
    task_id: EntityId
    duplicate: bool


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


@lru_cache
def get_object_storage() -> ObjectStorage:
    return MinioObjectStorage.from_settings(get_settings())


def get_paper_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> PaperService:
    return PaperService(SqlPaperRepository(session), storage)


@router.post("/documents", status_code=202, response_model=UploadResponse)
async def upload_document(
    file: Annotated[UploadFile, File()],
    service: Annotated[PaperService, Depends(get_paper_service)],
) -> UploadResponse:
    receipt = await service.upload_pdf(
        UploadPaperCommand(
            filename=file.filename or "paper.pdf",
            content_type=file.content_type or "",
            stream=file.file,
        )
    )
    return UploadResponse.model_validate(receipt, from_attributes=True)
