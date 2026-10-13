import asyncio
from uuid import UUID

import httpx

from research_agent.config import get_settings
from research_agent.notes.repository import SqlNoteRepository
from research_agent.notes.service import NoteService
from research_agent.providers.openai_embeddings import build_openai_embedding_provider
from research_agent.providers.qwen_llm import QwenLLMProvider
from research_agent.providers.qwen_reranker import QwenRerankerProvider
from research_agent.retrieval.repository import SqlHybridRetrievalRepository
from research_agent.retrieval.service import RetrievalService
from research_agent.shared.db import build_async_engine, build_session_factory
from research_agent.shared.errors import AppError, TransientInfrastructureError
from research_agent.tasks.celery_app import celery_app


@celery_app.task(
    name="research_agent.notes.generate_note",
    bind=True,
    autoretry_for=(TransientInfrastructureError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def generate_note(
    self: object,
    document_id: str,
    activate_if_new: bool = True,
) -> None:
    del self
    try:
        # Celery is synchronous; each job owns and closes its async resources.
        # Celery 为同步执行模型；每个任务独占并关闭自己的异步资源。
        asyncio.run(_run_generation(UUID(document_id), activate_if_new))
    except AppError as error:
        if error.retryable:
            raise TransientInfrastructureError(
                "Transient note generation infrastructure failure"
            ) from error
        raise


async def _run_generation(document_id: UUID, activate_if_new: bool) -> None:
    settings = get_settings()
    engine = build_async_engine(settings.database_url)
    try:
        session_factory = build_session_factory(engine)
        async with (
            session_factory() as session,
            httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as llm_client,
            httpx.AsyncClient(timeout=30.0) as reranker_client,
            httpx.AsyncClient(timeout=30.0) as embedding_client,
        ):
            llm = QwenLLMProvider(
                client=llm_client,
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key or settings.embedding_api_key,
                model_id=settings.llm_model,
            )
            reranker = QwenRerankerProvider(
                client=reranker_client,
                base_url=settings.reranker_base_url,
                api_key=settings.reranker_api_key,
                model_id=settings.reranker_model,
            )
            retrieval = RetrievalService(
                SqlHybridRetrievalRepository(session),
                build_openai_embedding_provider(
                    settings,
                    http_async_client=embedding_client,
                ),
                reranker,
            )
            service = NoteService(llm, retrieval, SqlNoteRepository(session))
            await service.generate_draft(
                document_id,
                activate_if_new=activate_if_new,
            )
    finally:
        await engine.dispose()
