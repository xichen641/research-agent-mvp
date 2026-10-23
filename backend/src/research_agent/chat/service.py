from uuid import UUID

from langgraph.graph.state import CompiledStateGraph

from research_agent.chat.domain import (
    ChatRunResult,
    ChatSessionRecord,
    PreparedChatRun,
    RetrievalMode,
    RetrievalScope,
    VerifiedAnswer,
)
from research_agent.chat.repository import ChatRepository, ChatSessionNotFoundError
from research_agent.shared.errors import AppError


class ChatService:
    def __init__(self, repository: ChatRepository, graph: CompiledStateGraph) -> None:
        self._repository = repository
        self._graph = graph

    async def create_session(
        self, *, title: str, scope: RetrievalScope
    ) -> ChatSessionRecord:
        return await self._repository.create_session(title=title, scope=scope)

    async def ask(
        self,
        session_id: UUID,
        question: str,
        *,
        retrieval_mode: RetrievalMode = "auto",
    ) -> ChatRunResult:
        prepared = await self.prepare(
            session_id, question, retrieval_mode=retrieval_mode
        )
        return await self.execute(prepared)

    async def prepare(
        self,
        session_id: UUID,
        question: str,
        *,
        retrieval_mode: RetrievalMode = "auto",
    ) -> PreparedChatRun:
        session = await self._repository.get_session(session_id)
        if session is None:
            raise ChatSessionNotFoundError(f"chat session {session_id} does not exist")
        run = await self._repository.start_run(
            session_id=session_id,
            question=question,
            scope=session.scope,
            retrieval_mode=retrieval_mode,
        )
        return PreparedChatRun(
            run_id=run.id,
            session_id=session_id,
            question=question,
            scope=run.scope or session.scope,
            retrieval_mode=retrieval_mode,
            history_message_count=run.history_message_count,
            research_context=run.research_context,
        )

    async def execute(self, prepared: PreparedChatRun) -> ChatRunResult:
        try:
            result = await self._graph.ainvoke(
                {
                    "session_id": prepared.session_id,
                    "run_id": prepared.run_id,
                    "question": prepared.question,
                    "scope": prepared.scope,
                    "retrieval_mode": prepared.retrieval_mode,
                    "history_message_count": prepared.history_message_count,
                    "research_context": prepared.research_context,
                    "warnings": [],
                },
                config={
                    "configurable": {
                        "thread_id": str(prepared.session_id),
                        "run_id": str(prepared.run_id),
                    },
                    "metadata": {"run_id": str(prepared.run_id)},
                },
            )
            answer = VerifiedAnswer.model_validate(result["verified_answer"])
            return ChatRunResult(run_id=prepared.run_id, answer=answer)
        except Exception as error:
            error_code = (
                error.code if isinstance(error, AppError) else "CHAT_RUN_FAILED"
            )
            await self._repository.fail_run(
                run_id=prepared.run_id, error_code=error_code
            )
            raise
