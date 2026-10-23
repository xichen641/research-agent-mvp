import asyncio
import logging
from datetime import timedelta
from uuid import UUID

import httpx
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from research_agent.chat.context import ConversationMemoryService
from research_agent.chat.diagnostics import runtime_configuration
from research_agent.chat.domain import PreparedChatRun
from research_agent.chat.graph import (
    GraphDependencies,
    _checkpoint_database_url,
    checkpoint_serializer,
    compile_chat_graph,
)
from research_agent.chat.repository import SqlChatRepository
from research_agent.chat.service import ChatService
from research_agent.config import get_settings
from research_agent.providers.openai_embeddings import build_openai_embedding_provider
from research_agent.providers.qwen_llm import QwenLLMProvider
from research_agent.providers.qwen_reranker import QwenRerankerProvider
from research_agent.providers.telemetry import ModelCallObservation
from research_agent.retrieval.repository import SqlHybridRetrievalRepository
from research_agent.retrieval.service import RetrievalService
from research_agent.shared.db import build_async_engine, build_session_factory
from research_agent.shared.errors import AppError
from research_agent.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="research_agent.chat.process_run")
def process_chat_run(run_id: str) -> None:
    asyncio.run(run_chat_job(UUID(run_id)))


async def run_chat_job(run_id: UUID) -> None:
    settings = get_settings()
    engine = build_async_engine(settings.database_url)
    try:
        session_factory = build_session_factory(engine)
        async with session_factory() as session:
            lease = timedelta(seconds=settings.chat_run_lease_seconds)
            chats = SqlChatRepository(session, lease_duration=lease)
            run = await chats.claim_run(run_id, lease=lease)
            if run is None:
                return
            if run.question is None or run.scope is None:
                await chats.fail_run(
                    run_id=run_id,
                    error_code="CHAT_RUN_INPUT_MISSING",
                )
                return
            prepared = PreparedChatRun(
                run_id=run.id,
                session_id=run.session_id,
                question=run.question,
                scope=run.scope,
                retrieval_mode=run.retrieval_mode,
                history_message_count=run.history_message_count,
                research_context=run.research_context,
            )
            try:
                try:
                    await chats.record_event(
                        run_id=run_id,
                        event_type="runtime_configuration",
                        payload=runtime_configuration(settings).model_dump(mode="json"),
                    )
                except AppError:
                    logger.warning(
                        "Run configuration measurement could not be persisted"
                    )

                async def record_model_call(observation: ModelCallObservation) -> None:
                    await chats.record_event(
                        run_id=run_id,
                        event_type="model_call_observed",
                        payload=observation.model_dump(mode="json"),
                    )

                async with (
                    httpx.AsyncClient(
                        timeout=settings.llm_timeout_seconds
                    ) as llm_client,
                    httpx.AsyncClient(timeout=30.0) as reranker_client,
                    httpx.AsyncClient(timeout=30.0) as embedding_client,
                    AsyncPostgresSaver.from_conn_string(
                        _checkpoint_database_url(settings.database_url),
                        serde=checkpoint_serializer(),
                    ) as saver,
                ):
                    llm = QwenLLMProvider(
                        client=llm_client,
                        base_url=settings.llm_base_url,
                        api_key=settings.llm_api_key or settings.embedding_api_key,
                        model_id=settings.llm_model,
                        observer=record_model_call,
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
                        default_final_limit=settings.chat_evidence_limit,
                    )
                    context = ConversationMemoryService(
                        chats,
                        llm,
                        max_context_tokens=settings.memory_max_context_tokens,
                        recent_message_tokens=settings.memory_recent_message_tokens,
                    )
                    graph = compile_chat_graph(
                        GraphDependencies(
                            llm=llm,
                            retrieval=retrieval,
                            chats=chats,
                            context=context,
                        ),
                        checkpointer=saver,
                    )
                    await ChatService(chats, graph).execute(prepared)
            except AppError:
                # ChatService persists a sanitized terminal failure event.
                return
    finally:
        await engine.dispose()
