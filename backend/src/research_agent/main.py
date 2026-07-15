from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from research_agent.papers.routes import router as papers_router
from research_agent.shared.errors import AppError
from research_agent.shared.ids import new_id


def create_app() -> FastAPI:
    app = FastAPI(title="ResearchAgent")

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


app = create_app()
