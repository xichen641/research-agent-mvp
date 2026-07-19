import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
import logging
from uuid import UUID

import httpx
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.chat.domain import (
    ChatSessionRecord,
    PreparedChatRun,
)
from research_agent.chat.graph import (
    GraphDependencies,
    _checkpoint_database_url,
    compile_chat_graph,
)
from research_agent.chat.repository import SqlChatRepository
from research_agent.chat.service import ChatService
from research_agent.config import Settings
from research_agent.providers.openai_embeddings import (
    build_openai_embedding_provider,
)
from research_agent.providers.qwen_llm import QwenLLMProvider
from research_agent.providers.qwen_reranker import QwenRerankerProvider
from research_agent.retrieval.domain import RetrievalScope
from research_agent.retrieval.repository import SqlHybridRetrievalRepository
from research_agent.retrieval.service import RetrievalService
from research_agent.shared.errors import AppError


logger = logging.getLogger(__name__)
GraphBuilder = Callable[[SqlChatRepository, AsyncSession], CompiledStateGraph]


class ChatApplication:
    """Start runs atomically, then execute graphs with fresh DB resources.

    原子创建运行记录，并为每次图执行使用独立数据库资源。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        graph_builder: GraphBuilder,
    ) -> None:
        self._session_factory = session_factory
        self._graph_builder = graph_builder
        self._tasks: set[asyncio.Task[None]] = set()

    async def create_session(
        self, *, title: str, scope: RetrievalScope
    ) -> ChatSessionRecord:
        async with self._session_factory() as session:
            return await SqlChatRepository(session).create_session(
                title=title, scope=scope
            )

    async def start(self, *, session_id: UUID, question: str) -> UUID:
        async with self._session_factory() as session:
            repository = SqlChatRepository(session)
            chat_session = await repository.get_session(session_id)
            if chat_session is None:
                raise AppError("CHAT_SESSION_NOT_FOUND", "Chat session was not found")
            run = await repository.start_run(
                session_id=session_id, question=question
            )
            prepared = PreparedChatRun(
                run_id=run.id,
                session_id=session_id,
                question=question,
                scope=chat_session.scope,
            )

        task = asyncio.create_task(self._execute(prepared))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run.id

    async def shutdown(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def _execute(self, prepared: PreparedChatRun) -> None:
        async with self._session_factory() as session:
            repository = SqlChatRepository(session)
            graph = self._graph_builder(repository, session)
            try:
                await ChatService(repository, graph).execute(prepared)
            except asyncio.CancelledError:
                with suppress(AppError):
                    await repository.fail_run(
                        run_id=prepared.run_id,
                        error_code="CHAT_RUN_CANCELLED",
                    )
                raise
            except Exception as error:
                code = error.code if isinstance(error, AppError) else "CHAT_RUN_FAILED"
                # Never log model output or paper text from the exception object.
                # 绝不把异常中的模型输出或论文原文写入日志。
                logger.warning("Chat run %s failed with code %s", prepared.run_id, code)


def chat_provider_configured(settings: Settings) -> bool:
    llm_key = settings.llm_api_key or settings.embedding_api_key
    return all(
        value.strip()
        for value in (
            llm_key,
            settings.llm_base_url,
            settings.llm_model,
            settings.embedding_api_key,
            settings.embedding_base_url,
            settings.embedding_model,
            settings.reranker_api_key,
            settings.reranker_base_url,
            settings.reranker_model,
        )
    )


@asynccontextmanager
async def production_chat_application(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[ChatApplication]:
    """Own provider clients and one durable saver for the API process.

    在 API 进程生命周期内统一管理模型客户端和持久化检查点。
    """

    if not chat_provider_configured(settings):
        raise ValueError("chat providers are not fully configured")
    embeddings = build_openai_embedding_provider(settings)
    async with (
        httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as llm_client,
        httpx.AsyncClient(timeout=30.0) as reranker_client,
        AsyncPostgresSaver.from_conn_string(
            _checkpoint_database_url(settings.database_url)
        ) as saver,
    ):
        await saver.setup()
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

        def graph_builder(
            chats: SqlChatRepository,
            session: AsyncSession,
        ) -> CompiledStateGraph:
            retrieval = RetrievalService(
                SqlHybridRetrievalRepository(session),
                embeddings,
                reranker,
            )
            return compile_chat_graph(
                GraphDependencies(llm=llm, retrieval=retrieval, chats=chats),
                checkpointer=saver,
            )

        application = ChatApplication(session_factory, graph_builder)
        try:
            yield application
        finally:
            await application.shutdown()
