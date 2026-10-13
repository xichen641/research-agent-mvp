import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
import logging
from uuid import UUID

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.chat.domain import (
    ChatSessionRecord,
    PreparedChatRun,
    RetrievalMode,
)
from research_agent.chat.repository import SqlChatRepository
from research_agent.chat.service import ChatService
from research_agent.config import Settings
from research_agent.retrieval.domain import RetrievalScope
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
        graph_builder: GraphBuilder | None = None,
        *,
        dispatch_outbox: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._graph_builder = graph_builder
        self._dispatch_outbox = dispatch_outbox
        self._tasks: set[asyncio.Task[None]] = set()

    async def create_session(
        self, *, title: str, scope: RetrievalScope
    ) -> ChatSessionRecord:
        async with self._session_factory() as session:
            return await SqlChatRepository(session).create_session(
                title=title, scope=scope
            )

    async def start(
        self,
        *,
        session_id: UUID,
        question: str,
        retrieval_mode: RetrievalMode = "auto",
    ) -> UUID:
        async with self._session_factory() as session:
            repository = SqlChatRepository(session)
            chat_session = await repository.get_session(session_id)
            if chat_session is None:
                raise AppError("CHAT_SESSION_NOT_FOUND", "Chat session was not found")
            run = await repository.start_run(
                session_id=session_id,
                question=question,
                scope=chat_session.scope,
                retrieval_mode=retrieval_mode,
                dispatch=self._dispatch_outbox,
            )
            prepared = PreparedChatRun(
                run_id=run.id,
                session_id=session_id,
                question=question,
                scope=chat_session.scope,
                retrieval_mode=retrieval_mode,
                history_message_count=run.history_message_count,
            )

        if self._dispatch_outbox:
            return run.id
        if self._graph_builder is None:
            raise RuntimeError("inline chat execution requires a graph builder")
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
            graph_builder = self._graph_builder
            if graph_builder is None:
                raise RuntimeError("inline chat execution requires a graph builder")
            graph = graph_builder(repository, session)
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
    """Persist and dispatch runs; workers own model and graph resources.

    API 只负责持久化与投递，模型和图资源由 Worker 独占。
    """

    if not chat_provider_configured(settings):
        raise ValueError("chat providers are not fully configured")
    application = ChatApplication(session_factory, dispatch_outbox=True)
    try:
        yield application
    finally:
        await application.shutdown()
