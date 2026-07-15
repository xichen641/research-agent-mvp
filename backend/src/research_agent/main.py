import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
import logging
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.papers.routes import router as papers_router
from research_agent.shared.db import async_session_factory
from research_agent.shared.errors import AppError
from research_agent.shared.ids import new_id
from research_agent.tasks.celery_app import celery_app
from research_agent.tasks.outbox_relay import (
    CelerySender,
    OutboxRelay,
    SqlOutboxRepository,
)


logger = logging.getLogger(__name__)
RelayPass = Callable[[], Awaitable[object]]
RelaySleep = Callable[[float], Awaitable[object]]


async def run_outbox_relay_pass(
    session_factory: Callable[[], object],
    sender: CelerySender,
) -> int:
    session_context = session_factory()
    async with cast(AsyncSession, session_context) as session:
        repository = SqlOutboxRepository(session)
        try:
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
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if relay_pass is None:
            yield
            return

        task = asyncio.create_task(
            run_outbox_relay_loop(relay_pass, sleep=relay_sleep)
        )
        app.state.outbox_relay_task = task
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="ResearchAgent", lifespan=lifespan)

    @app.middleware("http")
    async def trace_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        trace_header = request.headers.get("X-Trace-ID")
        trace_id = trace_header if trace_header and trace_header.strip() else str(new_id())
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, error: AppError) -> JSONResponse:
        status = 400 if error.code == "INVALID_PDF" else 503 if error.retryable else 500
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

    app.include_router(papers_router, prefix="/api/v1")

    return app


app = create_app(relay_pass=_production_relay_pass)
