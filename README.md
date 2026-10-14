# ResearchAgent

ResearchAgent is a private research workspace for discovering, importing,
reading, and discussing papers. The current platform provides:

- topic search against arXiv metadata, followed by explicit user-selected PDF
  import into the private ingestion pipeline;
- durable PDF parsing, chunking, embeddings, hybrid retrieval, reranking, and
  citation verification;
- multi-turn paper discussion with bounded recent context and durable compressed
  conversation memory;
- per-turn retrieval modes: automatic planning, no RAG, or forced RAG over the
  session's paper scope;
- PostgreSQL-backed chat state, transactional Outbox dispatch, Celery worker
  leases and retry-safe recovery;
- liveness and dependency readiness endpoints, plus a Next.js research UI.

Conversation memory is working context, not paper evidence. Claims presented as
paper evidence still require retrieved chunks and citation verification.

The V2 iteration starts with a fixed local ReID corpus. See
[the corpus inventory and acceptance steps](evaluation/README.md) to verify the
six private PDFs against their reviewed versions before importing or evaluating.
The same guide covers `scripts/import-reid-corpus.ps1`, which uploads the fixed
corpus through the existing ingestion API and reports duplicates and parse quality.
It also provides 20 fixed research tasks, their source excerpts and human review
criteria, plus `scripts/check-reid-research-tasks.ps1 -Docker` for source validation.

## Prerequisites

- Docker with Docker Compose
- Python 3.12 and `uv` for backend development
- Node.js 22 and `pnpm` for frontend development

## Start the platform

Copy the environment template, then provide the model and reranker credentials:

```powershell
Copy-Item .env.example .env
docker compose -p research-agent -f infra/compose.yaml up -d --build
```

The default UI is <http://127.0.0.1:3000>, the API is
<http://127.0.0.1:8000>, and readiness is available at
<http://127.0.0.1:8000/health/ready>. The explicit loopback address avoids
Windows `localhost` resolving to another IPv6-bound development service.

Host ports are configurable through `API_HOST_PORT`, `WEB_HOST_PORT`,
`POSTGRES_HOST_PORT`, `MINIO_HOST_PORT`, and `MINIO_CONSOLE_HOST_PORT`, so an
isolated stack can run beside another local project.

Start the optional Prometheus profile when operational dashboards or alerts are
needed:

```powershell
docker compose -p research-agent -f infra/compose.yaml --profile observability up -d
```

Prometheus is then available on `PROMETHEUS_HOST_PORT` (default 9090). It scrapes
the API's bounded-label `/metrics` endpoint and evaluates alerts for API loss,
Outbox delay or dispatch failures, and expired chat worker leases. The worker
also has a Celery ping health check; Compose marks it unhealthy if the process
cannot service its queue.

Run multiple application replicas entirely inside the local Docker project by
adding the HA override. A local Nginx gateway owns the host ports and balances
requests across three API and two production Web replicas; two workers safely
share the durable queues and chat leases:

```powershell
docker compose -p research-agent `
  -f infra/compose.yaml `
  -f infra/compose.ha.yaml `
  up -d --build `
  --scale api=3 --scale web=2 --scale worker=2
```

This mode proves application-replica failover on one local Docker host. It does
not pretend that a single laptop, the Nginx gateway, or the local PostgreSQL,
Redis, and MinIO volumes survive host loss.

## Backup and restore

Create an application-consistent backup of PostgreSQL and the private paper
objects with the API and worker briefly paused:

```powershell
.\scripts\backup.ps1 -Destination D:\Backups\research-agent\2026-08-29
```

The backup contains a PostgreSQL custom-format dump, the MinIO objects, a
SHA-256-protected manifest with durable row and object counts, and a completion
marker. The environment file and credentials are deliberately excluded.

Restore only into the exact Compose project you intend to replace. The explicit
switch acknowledges that the target project's `research` database and paper
bucket will be replaced:

```powershell
.\scripts\restore.ps1 `
  -Source D:\Backups\research-agent\2026-08-29 `
  -ProjectName research-agent `
  -ConfirmDataLoss
```

Restore verifies the dump checksum, runs migrations, and compares all recorded
durable row counts and the object count before restarting the API and worker. If
verification fails, they remain stopped so a partial restore is not served.

## arXiv usage boundary

Search caches metadata and obeys a single global request slot with at least
three seconds between arXiv API calls. Searching never downloads a PDF. A PDF
is downloaded only after the user selects a result, and remains private for
personal research use; the application does not publicly redistribute imported
PDFs. Thank you to arXiv for use of its open access interoperability.

## Verification

```powershell
Set-Location backend
uv run ruff check .
uv run pyright
uv run pytest

Set-Location ../frontend
pnpm test
pnpm lint
pnpm typecheck
pnpm build
pnpm test:e2e
```
