FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libxcb1 \
    && rm -rf /var/lib/apt/lists/*
COPY backend/src ./src
COPY backend/README.md ./README.md
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
RUN uv sync --frozen --no-dev
RUN useradd --create-home --uid 10001 researchagent
USER researchagent
CMD ["/app/.venv/bin/uvicorn", "research_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
