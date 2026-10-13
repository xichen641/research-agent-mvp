import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager, suppress
import logging
from typing import AsyncContextManager, cast

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.chat.application import (
    chat_provider_configured,
    production_chat_application,
)
from research_agent.chat.routes import ChatCommands, router as chat_router
from research_agent.discovery.routes import router as discovery_router
from research_agent.health import ReadinessStates, check_readiness
from research_agent.notes.routes import router as notes_router
from research_agent.operations.metrics import collect_operational_metrics
from research_agent.papers.routes import router as papers_router
from research_agent.config import get_settings
from research_agent.shared.db import async_session_factory
from research_agent.shared.errors import AppError
from research_agent.shared.ids import new_id
from research_agent.tasks.celery_app import celery_app
from research_agent.tasks.outbox_relay import (
    CelerySender,
    OutboxRelay,
    SqlOutboxRepository,
)
from research_agent.tasks.recovery import SqlChatRunRecovery
from research_agent.tasks.routes import router as tasks_router


logger = logging.getLogger(__name__)
RelayPass = Callable[[], Awaitable[object]]
RelaySleep = Callable[[float], Awaitable[object]]
ChatFactory = Callable[[], AsyncContextManager[ChatCommands]]
ReadinessProbe = Callable[[], Awaitable[ReadinessStates]]
MetricsProbe = Callable[[], Awaitable[str]]


async def run_outbox_relay_pass(
    session_factory: Callable[[], object],
    sender: CelerySender,
) -> int:
    session_context = session_factory()
    async with cast(AsyncSession, session_context) as session:
        repository = SqlOutboxRepository(session)
        try:
            await SqlChatRunRecovery(session).requeue_expired(
                limit=get_settings().chat_recovery_batch_size
            )
            return await OutboxRelay(repository, sender).run_once()
        except BaseException:
            await repository.rollback()
            raise


async def run_outbox_relay_loop(
    relay_pass: RelayPass,
    *,
    sleep: RelaySleep = asyncio.sleep,
) -> None:
    while True:
        try:
            await relay_pass()
        except Exception:
            logger.exception("Unexpected outbox relay pass failure")
        await sleep(1)


async def _production_relay_pass() -> int:
    return await run_outbox_relay_pass(async_session_factory, celery_app)


def create_app(
    *,
    relay_pass: RelayPass | None = None,
    relay_sleep: RelaySleep = asyncio.sleep,
    web_origin: str | None = None,
    chat_commands: ChatCommands | None = None,
    chat_factory: ChatFactory | None = None,
    readiness_probe: ReadinessProbe | None = None,
    metrics_probe: MetricsProbe | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            if chat_factory is not None:
                app.state.chat_commands = await stack.enter_async_context(
                    chat_factory()
                )
            task = None
            if relay_pass is not None:
                task = asyncio.create_task(
                    run_outbox_relay_loop(relay_pass, sleep=relay_sleep)
                )
                app.state.outbox_relay_task = task
            try:
                yield
            finally:
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    app = FastAPI(title="ResearchAgent", lifespan=lifespan)
    if chat_commands is not None:
        app.state.chat_commands = chat_commands
    resolved_origin = web_origin or get_settings().web_origin
    allowed_origins = [resolved_origin]
    if "localhost" in resolved_origin:
        allowed_origins.append(resolved_origin.replace("localhost", "127.0.0.1"))
    elif "127.0.0.1" in resolved_origin:
        allowed_origins.append(resolved_origin.replace("127.0.0.1", "localhost"))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type", "X-Trace-ID", "Last-Event-ID"],
    )

    @app.middleware("http")
    async def trace_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        trace_header = request.headers.get("X-Trace-ID")
        trace_id = (
            trace_header if trace_header and trace_header.strip() else str(new_id())
        )
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, error: AppError) -> JSONResponse:
        status_by_code = {
            "INVALID_PDF": 400,
            "INVALID_CURSOR": 400,
            "DOCUMENT_NOT_FOUND": 404,
            "TASK_NOT_FOUND": 404,
            "TASK_NOT_RETRYABLE": 409,
            "CHAT_RUN_NOT_FOUND": 404,
            "CHAT_SESSION_NOT_FOUND": 404,
            "CHAT_RUN_IN_PROGRESS": 409,
            "INVALID_LAST_EVENT_ID": 400,
            "INVALID_CHAT_SCOPE": 400,
            "NOTE_NOT_FOUND": 404,
        }
        status = status_by_code.get(error.code, 503 if error.retryable else 500)
        return JSONResponse(
            status_code=status,
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": error.code.replace("_", " ").title(),
                "status": status,
                "code": error.code,
                "message": error.message,
                "retryable": error.retryable,
                "trace_id": request.state.trace_id,
                "details": error.details,
            },
        )

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def readiness() -> JSONResponse:
        probe = readiness_probe or _production_readiness_probe
        dependencies = dict(await probe())
        ready = all(state == "ok" for state in dependencies.values())
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "status": "ready" if ready else "unavailable",
                "dependencies": dependencies,
            },
        )

    @app.get("/metrics")
    async def metrics() -> Response:
        probe = metrics_probe or _production_metrics_probe
        return Response(
            content=await probe(),
            media_type="text/plain; version=0.0.4",
        )

    app.include_router(papers_router, prefix="/api/v1")
    app.include_router(tasks_router, prefix="/api/v1")
    app.include_router(chat_router, prefix="/api/v1")
    app.include_router(discovery_router, prefix="/api/v1")
    app.include_router(notes_router, prefix="/api/v1")

    return app


def _production_chat_factory() -> AsyncContextManager[ChatCommands]:
    settings = get_settings()
    return production_chat_application(settings, async_session_factory)


async def _production_readiness_probe() -> ReadinessStates:
    return await check_readiness(get_settings(), async_session_factory)


async def _production_metrics_probe() -> str:
    return await collect_operational_metrics(async_session_factory)


_settings = get_settings()
app = create_app(
    relay_pass=_production_relay_pass,
    chat_factory=(
        _production_chat_factory if chat_provider_configured(_settings) else None
    ),
)
