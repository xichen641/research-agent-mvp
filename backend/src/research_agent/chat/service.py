from uuid import UUID

from langgraph.graph.state import CompiledStateGraph

from research_agent.chat.domain import (
    ChatRunResult,
    ChatSessionRecord,
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

    async def ask(self, session_id: UUID, question: str) -> ChatRunResult:
        session = await self._repository.get_session(session_id)
        if session is None:
            raise ChatSessionNotFoundError(f"chat session {session_id} does not exist")
        run = await self._repository.start_run(
            session_id=session_id, question=question
        )
        try:
            result = await self._graph.ainvoke(
                {
                    "session_id": session_id,
                    "run_id": run.id,
                    "question": question,
                    "scope": session.scope,
                    "warnings": [],
                },
                config={
                    "configurable": {
                        "thread_id": str(session_id),
                        "run_id": str(run.id),
                    },
                    "metadata": {"run_id": str(run.id)},
                },
            )
            answer = VerifiedAnswer.model_validate(result["verified_answer"])
            return ChatRunResult(run_id=run.id, answer=answer)
        except Exception as error:
            error_code = error.code if isinstance(error, AppError) else "CHAT_RUN_FAILED"
            await self._repository.fail_run(run_id=run.id, error_code=error_code)
            raise
