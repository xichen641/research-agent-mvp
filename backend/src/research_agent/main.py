from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="ResearchAgent")

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
