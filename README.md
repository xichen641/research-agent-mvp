# ResearchAgent

ResearchAgent is a local research workspace. This MVP foundation provides a
Next.js web application, a FastAPI API, PostgreSQL with pgvector, Redis, and
MinIO. The current API exposes a liveness endpoint at `GET /health/live`.

## MVP scope

The MVP is a single-user, local paper knowledge base for up to 500
born-digital Chinese- and English-language PDF papers. Uploading a paper
automatically parses and indexes it, but uploading does not generate notes;
note generation remains a separate, explicit action.

The MVP explicitly excludes OCR for scanned documents, research graphs and
recommendations, Deep Agents, automated experiments, full-text writing, and
multi-user capabilities. Those remain possible future capabilities rather than
features supported by this MVP.

## Prerequisites

- Docker with Docker Compose
- Python 3.12 and `uv` for local backend development
- Node.js 22, Corepack, and `pnpm` for local frontend development

## Environment setup

Copy the example environment file before starting the services:

```powershell
Copy-Item .env.example .env
```

## Start with Docker Compose

```powershell
docker compose -f infra/compose.yaml up -d --build
```

The web application is available at <http://localhost:3000> and the API at
<http://localhost:8000>.

## Tests and quality checks

Run backend tests and static checks:

```powershell
Set-Location backend
uv run pytest
uv run ruff check .
uv run pyright
```

Run frontend checks:

```powershell
Set-Location frontend
pnpm lint
pnpm build
```
