import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.config import Settings, get_settings
from research_agent.ingestion.chunker import StructuralChunker
from research_agent.ingestion.docling_parser import DoclingDocumentParser
from research_agent.ingestion.pipeline import IngestionPipeline
from research_agent.papers.repository import SqlPaperRepository
from research_agent.papers.storage import MinioObjectStorage
from research_agent.shared.db import build_async_engine, build_session_factory
from research_agent.shared.errors import AppError, TransientInfrastructureError
from research_agent.tasks.celery_app import celery_app
from research_agent.tasks.repository import SqlTaskRepository


@celery_app.task(
    name="research_agent.ingestion.process_document",
    bind=True,
    autoretry_for=(TransientInfrastructureError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def process_document(self: object, version_id: str) -> None:
    del self
    parsed_version_id = UUID(version_id)
    try:
        # Each synchronous Celery call owns one event loop; all async resources
        # are created inside that invocation and never cross asyncio.run loops.
        # 每次同步 Celery 调用独占一个事件循环；所有异步资源都在本次调用内创建，不跨 asyncio.run 循环复用。
        asyncio.run(run_ingestion_job(parsed_version_id))
    except AppError as error:
        if error.retryable:
            raise TransientInfrastructureError(
                "Transient ingestion infrastructure failure"
            ) from error
        raise


async def run_ingestion_job(version_id: UUID) -> None:
    settings = get_settings()
    engine = build_async_engine(settings.database_url)
    try:
        session_factory = build_session_factory(engine)
        async with session_factory() as session:
            pipeline = _build_pipeline(session, settings)
            await pipeline.run(version_id)
    finally:
        await engine.dispose()


def _build_pipeline(
    session: AsyncSession,
    settings: Settings,
) -> IngestionPipeline:
    papers = SqlPaperRepository(session)
    tasks = SqlTaskRepository(session)
    storage = MinioObjectStorage.from_settings(settings)
    parser = DoclingDocumentParser()
    chunker = StructuralChunker()
    return IngestionPipeline(parser, storage, papers, tasks, chunker)
