# ResearchAgent Paper Knowledge Base MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox - [ ] syntax for tracking.

**Goal:** Build a locally deployed, single-user paper knowledge base that uploads born-digital Chinese and English PDFs, parses and indexes them asynchronously, answers questions with verified page-level citations, and generates optional editable structured notes.

**Architecture:** Use a modular Python monolith with FastAPI for HTTP and SSE, Celery workers for heavy jobs, PostgreSQL plus pgvector as the source of truth, Redis as the Celery broker, and MinIO for objects. Use a Next.js frontend, LangChain only behind provider adapters, and LangGraph for the explicit question-answer workflow.

**Tech Stack:** Python 3.12, uv, FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL with pgvector, Redis, Celery 5.6, MinIO, Docling CPU pipeline, LangChain, LangGraph, Langfuse, pytest, Ruff, Pyright, Next.js, TypeScript, pnpm, Vercel AI SDK, Vitest, Playwright, Docker Compose.

## Global Constraints

- Scope is the paper knowledge base MVP only. Research graphs, related-paper recommendation, Deep Agents, Mem0, Neo4j, Elasticsearch, multi-user auth, billing, OCR, automatic experiment execution, and full-paper writing are excluded.
- The supported corpus is at most 500 born-digital Chinese or English PDFs.
- Uploading a PDF automatically parses and indexes it, but never automatically generates a paper note.
- Paper-grounded claims require document version, chunk, page, and quote provenance.
- Model general knowledge is allowed only in a separately labelled answer block.
- PostgreSQL is the source of truth. Redis data may be lost without losing permanent task, paper, chat, or note state.
- API, worker, and migrations share one Python package. Framework types do not cross domain interfaces.
- All heavy jobs are idempotent and receive IDs rather than PDF bytes.
- Default services bind to localhost. MinIO buckets are private.
- Automatic tests use fake model providers. Real-provider tests are explicit smoke or evaluation commands.
- MVP release gate: at least 30 real papers, 50 labelled questions, parse success at least 95%, Recall@10 at least 85%, citation correctness at least 90%, and model-knowledge labelling at 100%.
- Starting with Product Task 2, the user grants standing authorization for an Agent to stage the exact task-scoped tracked files and execute `git commit` after all required verification passes and independent task review is clean. The Agent must report the committed scope, message, and verification evidence. This standing authorization does not include `git push`, merge, worktree removal, or ignored SDD artifacts; each integration or cleanup operation still requires separate explicit user authorization.

## Bilingual Document Directory Contract (Effective from Product Task 2)

- New formal specifications use `docs/superpowers/specs/EN/<same-filename>.md` and `docs/superpowers/specs/CN/<same-filename>.md`. New formal implementation plans use `docs/superpowers/plans/EN/<same-filename>.md` and `docs/superpowers/plans/CN/<same-filename>.md`.
- SDD workflow artifacts use `.superpowers/sdd/EN/task-N-{brief,report,review}.md` and `.superpowers/sdd/CN/task-N-{brief,report,review}.md`; progress uses `.superpowers/sdd/EN/progress.md` and `.superpowers/sdd/CN/progress.md`. Corresponding English and Chinese files use the same filename and do not use a `.zh-CN` suffix.
- English is the canonical source. Chinese companions must faithfully preserve the English implementation meaning and must keep every code block, command, path, interface name, dependency version, hash, expected result, test evidence, raw log, and task boundary unchanged.
- Each English document links to its Chinese companion with `../CN/<same-filename>`, and each Chinese document links to its English canonical source with `../EN/<same-filename>`.
- The controller owns each bilingual Brief pair and the bilingual Progress pair. The implementer owns each bilingual Report pair. The reviewer owns each bilingual Review pair.
- Synchronization is a handoff gate: the responsible role must create or update both language files and verify their semantic alignment before handing work to the next role.
- After each product task, the controller must stop at a user acceptance checkpoint. The next product task may not start until the user explicitly authorizes it.
- Existing formal documents and Product Task 1 SDD artifacts remain in their current locations. Product Task 1 must not be rerun, retested, re-reviewed, moved, or retroactively backfilled.
- Starting with Product Task 2, the user grants standing authorization for an Agent to stage the exact task-scoped tracked files and execute `git commit` after all required verification passes and independent task review is clean. The Agent must report the committed scope, message, and verification evidence. This standing authorization does not include `git push`, merge, worktree removal, or ignored `.superpowers/sdd` workflow artifacts; each integration or cleanup operation still requires separate explicit user authorization.

## Plan Scope and Checkpoints

This plan deliberately stops after the MVP. It has six review checkpoints:

1. Foundation: local services and health checks run.
2. Ingestion: a PDF reaches READY with persisted artifacts and chunks.
3. Retrieval: hybrid retrieval meets the synthetic and initial labelled tests.
4. Chat: a question returns a verified, labelled, page-cited answer.
5. Notes: the user can optionally generate, edit, and retain a note.
6. Release: the real ReID corpus passes the agreed quality gates.

Future phases receive separate implementation plans after Checkpoint 6.

## Design Traceability

| Approved design area | Implementation tasks | Verification point |
|---|---|---|
| Sections 4.6 and 8: framework-independent domain boundaries | Tasks 2-5, 9-13, 15 | Protocol tests, static typing, no framework types in domain interfaces |
| Sections 6 and 9: MVP ingestion baseline | Tasks 3-9 | Checkpoints 1-2, idempotency tests, READY transition tests |
| Section 10: hybrid retrieval, workflow, answers, citations, streaming | Tasks 10-14 | Checkpoints 3-4, retrieval metrics, citation contract and browser tests |
| Section 11: user-optional structured notes | Task 15 | Checkpoint 5, upload-without-note and revision-preservation tests |
| Sections 13-15: data, API, errors, and reliability | Tasks 3-5, 8-17 | Migration, contract, retry, restart, and end-to-end tests |
| Sections 16-20: security, testing, evaluation, deployment, observability | Tasks 16-17 | Security suite, reproducible evaluation, backup restore, Checkpoint 6 |
| Sections 5.2-5.5 and 12: post-MVP research features | Post-MVP plan boundaries only | Explicitly excluded from this implementation plan |

## Repository Structure

~~~text
ResearchAgent/
├─ .env.example
├─ .gitignore
├─ README.md
├─ infra/
│  ├─ compose.yaml
│  └─ redis.conf
├─ backend/
│  ├─ pyproject.toml
│  ├─ uv.lock
│  ├─ alembic.ini
│  ├─ Dockerfile
│  ├─ migrations/
│  │  ├─ env.py
│  │  └─ versions/
│  ├─ src/research_agent/
│  │  ├─ main.py
│  │  ├─ config.py
│  │  ├─ shared/
│  │  │  ├─ db.py
│  │  │  ├─ errors.py
│  │  │  ├─ ids.py
│  │  │  └─ logging.py
│  │  ├─ providers/
│  │  │  ├─ protocols.py
│  │  │  ├─ fake.py
│  │  │  ├─ langchain_llm.py
│  │  │  ├─ openai_embeddings.py
│  │  │  └─ cohere_reranker.py
│  │  ├─ papers/
│  │  │  ├─ domain.py
│  │  │  ├─ models.py
│  │  │  ├─ repository.py
│  │  │  ├─ service.py
│  │  │  └─ routes.py
│  │  ├─ tasks/
│  │  │  ├─ celery_app.py
│  │  │  ├─ domain.py
│  │  │  ├─ repository.py
│  │  │  └─ outbox_relay.py
│  │  ├─ ingestion/
│  │  │  ├─ contracts.py
│  │  │  ├─ docling_parser.py
│  │  │  ├─ chunker.py
│  │  │  ├─ pipeline.py
│  │  │  └─ celery_tasks.py
│  │  ├─ retrieval/
│  │  │  ├─ domain.py
│  │  │  ├─ repository.py
│  │  │  ├─ rrf.py
│  │  │  └─ service.py
│  │  ├─ chat/
│  │  │  ├─ domain.py
│  │  │  ├─ models.py
│  │  │  ├─ repository.py
│  │  │  ├─ graph.py
│  │  │  ├─ citations.py
│  │  │  ├─ stream.py
│  │  │  ├─ service.py
│  │  │  └─ routes.py
│  │  ├─ notes/
│  │  │  ├─ domain.py
│  │  │  ├─ models.py
│  │  │  ├─ repository.py
│  │  │  ├─ service.py
│  │  │  ├─ celery_tasks.py
│  │  │  └─ routes.py
│  │  └─ evaluation/
│  │     ├─ domain.py
│  │     ├─ dataset.py
│  │     ├─ retrieval_eval.py
│  │     ├─ answer_eval.py
│  │     └─ cli.py
│  └─ tests/
│     ├─ unit/
│     ├─ integration/
│     ├─ contract/
│     ├─ e2e/
│     └─ helpers/
├─ frontend/
│  ├─ package.json
│  ├─ pnpm-lock.yaml
│  ├─ Dockerfile
│  ├─ src/app/
│  ├─ src/features/papers/
│  ├─ src/features/chat/
│  ├─ src/features/notes/
│  └─ src/lib/
├─ evaluation/
│  ├─ datasets/
│  └─ README.md
└─ scripts/
   ├─ verify.ps1
   ├─ backup.ps1
   └─ restore.ps1
~~~

---

### Task 1: Create the executable repository foundation

**Files:**
- Create: .gitignore
- Create: .env.example
- Create: README.md
- Create: backend/pyproject.toml
- Create: backend/src/research_agent/main.py
- Create: backend/src/research_agent/config.py
- Create: backend/tests/unit/test_health.py
- Create: backend/Dockerfile
- Create: frontend/ through create-next-app
- Create: frontend/Dockerfile
- Create: infra/compose.yaml
- Create: infra/redis.conf

**Interfaces:**
- Consumes: none
- Produces: research_agent.main:create_app, Settings, GET /health/live, Docker services web, api, postgres, redis, and minio. The worker service is added in Task 5; the full dependency readiness check is added during Task 17 hardening.

- [ ] **Step 1: Scaffold backend and frontend**

Run from the repository root:

~~~powershell
New-Item -ItemType Directory -Force backend, infra | Out-Null
Push-Location backend
uv init --package --name research-agent --python 3.12
uv add fastapi "uvicorn[standard]" pydantic-settings
uv add --dev pytest pytest-asyncio httpx ruff pyright
Pop-Location
pnpm create next-app@latest frontend --ts --eslint --tailwind --app --src-dir --import-alias "@/*" --use-pnpm --yes
~~~

Expected: backend/pyproject.toml and frontend/package.json exist; both lock files are created after sync/install.

- [ ] **Step 2: Write the failing health test**

Create backend/tests/unit/test_health.py:

~~~python
from fastapi.testclient import TestClient

from research_agent.main import create_app


def test_liveness_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
~~~

- [ ] **Step 3: Run the test and confirm the expected failure**

~~~powershell
Set-Location backend
uv run pytest tests/unit/test_health.py -v
~~~

Expected: FAIL because research_agent.main:create_app does not exist.

- [ ] **Step 4: Add settings and the minimal FastAPI app**

Create backend/src/research_agent/config.py:

~~~python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ResearchAgent"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://research:research@postgres:5432/research"
    redis_url: str = "redis://redis:6379/0"
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "research"
    minio_secret_key: str = "research-secret"
    minio_bucket: str = "research-agent"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
~~~

Create backend/src/research_agent/main.py:

~~~python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="ResearchAgent")

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
~~~

- [ ] **Step 5: Run backend quality gates**

~~~powershell
Set-Location backend
uv run pytest tests/unit/test_health.py -v
uv run ruff check .
uv run pyright
~~~

Expected: one test passes; Ruff and Pyright exit 0.

- [ ] **Step 6: Add repository configuration and container images**

Create .gitignore:

~~~gitignore
.env
.worktrees/
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.pyright/
*.py[cod]
frontend/node_modules/
frontend/.next/
frontend/playwright-report/
frontend/test-results/
.tmp/
backups/
~~~

Create .env.example:

~~~dotenv
ENVIRONMENT=development
DATABASE_URL=postgresql+asyncpg://research:research@postgres:5432/research
REDIS_URL=redis://redis:6379/0
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=research
MINIO_SECRET_KEY=research-secret
MINIO_BUCKET=research-agent
NEXT_PUBLIC_API_URL=http://localhost:8000
~~~

Create backend/Dockerfile:

~~~dockerfile
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev
CMD ["uv", "run", "uvicorn", "research_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
~~~

Create frontend/Dockerfile:

~~~dockerfile
FROM node:22-alpine
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
CMD ["pnpm", "dev", "--hostname", "0.0.0.0"]
~~~

README.md must contain the supported MVP scope, prerequisites, .env.example to .env setup, Docker Compose start command, backend and frontend test commands, and the explicit statement that upload does not generate notes.

- [ ] **Step 7: Add local infrastructure and application services**

Create infra/redis.conf:

~~~conf
appendonly yes
maxmemory-policy noeviction
~~~

Create infra/compose.yaml with these exact service responsibilities:

~~~yaml
services:
  api:
    build:
      context: ..
      dockerfile: backend/Dockerfile
    env_file: ../.env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio:
        condition: service_started
    ports:
      - "127.0.0.1:8000:8000"

  web:
    build:
      context: ../frontend
      dockerfile: Dockerfile
    environment:
      NEXT_PUBLIC_API_URL: http://localhost:8000
    depends_on:
      - api
    ports:
      - "127.0.0.1:3000:3000"

  postgres:
    image: pgvector/pgvector:pg17
    environment:
      POSTGRES_DB: research
      POSTGRES_USER: research
      POSTGRES_PASSWORD: research
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U research -d research"]
      interval: 5s
      timeout: 3s
      retries: 20
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"

  redis:
    image: redis:7-alpine
    command: ["redis-server", "/usr/local/etc/redis/redis.conf"]
    volumes:
      - ./redis.conf:/usr/local/etc/redis/redis.conf:ro
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 20
    ports:
      - "127.0.0.1:6379:6379"

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: research
      MINIO_ROOT_PASSWORD: research-secret
    volumes:
      - minio_data:/data
    ports:
      - "127.0.0.1:9000:9000"
      - "127.0.0.1:9001:9001"

volumes:
  postgres_data:
  redis_data:
  minio_data:
~~~

- [ ] **Step 8: Verify Compose and complete Checkpoint 1**

~~~powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env -ErrorAction Stop
}
docker compose -f infra/compose.yaml config --quiet
docker compose -f infra/compose.yaml up -d --build
docker compose -f infra/compose.yaml ps
~~~

Expected: config exits 0; Postgres and Redis are healthy; GET http://localhost:8000/health/live returns {"status":"ok"}; http://localhost:3000 loads.

- [ ] **Step 9: Prepare the user commit checkpoint**

Show, but do not execute without user authorization:

~~~powershell
git add .gitignore .env.example README.md backend frontend infra
git commit -m "chore: scaffold ResearchAgent services"
~~~

---

### Task 2: Define framework-independent domain and provider contracts

**Files:**
- Create: backend/src/research_agent/shared/ids.py
- Create: backend/src/research_agent/shared/errors.py
- Create: backend/src/research_agent/providers/protocols.py
- Create: backend/src/research_agent/providers/fake.py
- Create: backend/tests/unit/providers/test_fake_providers.py

**Interfaces:**
- Consumes: Settings from Task 1
- Produces: new_id, AppError, ChatTurn, LLMProvider, EmbeddingProvider, RerankerProvider, FakeLLMProvider, FakeEmbeddingProvider, FakeRerankerProvider

- [ ] **Step 1: Write failing provider contract tests**

Create backend/tests/unit/providers/test_fake_providers.py:

~~~python
import pytest
from pydantic import BaseModel

from research_agent.providers.fake import (
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakeRerankerProvider,
)
from research_agent.providers.protocols import ChatTurn, RerankItem


class ExampleOutput(BaseModel):
    label: str


@pytest.mark.asyncio
async def test_fake_llm_returns_registered_structured_output() -> None:
    provider = FakeLLMProvider({"example": {"label": "ok"}})

    result = await provider.generate_structured(
        operation="example",
        messages=[ChatTurn(role="user", content="question")],
        output_type=ExampleOutput,
    )

    assert result == ExampleOutput(label="ok")


@pytest.mark.asyncio
async def test_fake_embedding_is_deterministic() -> None:
    provider = FakeEmbeddingProvider(dimension=4)

    first = await provider.embed_query("same")
    second = await provider.embed_query("same")

    assert first == second
    assert len(first) == 4


@pytest.mark.asyncio
async def test_fake_embedding_supports_dimensions_larger_than_one_digest() -> None:
    provider = FakeEmbeddingProvider(dimension=33)

    result = await provider.embed_query("same")

    assert len(result) == 33


@pytest.mark.parametrize("dimension", [0, -1])
def test_fake_embedding_rejects_non_positive_dimension(dimension: int) -> None:
    with pytest.raises(ValueError, match="dimension must be positive"):
        FakeEmbeddingProvider(dimension=dimension)


@pytest.mark.asyncio
async def test_fake_reranker_orders_by_configured_score() -> None:
    provider = FakeRerankerProvider({"b": 0.9, "a": 0.2})

    ranked = await provider.rerank(
        query="q",
        items=[RerankItem(id="a", text="A"), RerankItem(id="b", text="B")],
        top_n=2,
    )

    assert [item.id for item in ranked] == ["b", "a"]
~~~

- [ ] **Step 2: Run tests and confirm imports fail**

~~~powershell
Set-Location backend
uv run pytest tests/unit/providers/test_fake_providers.py -v
~~~

Expected: collection fails because providers.protocols and providers.fake do not exist.

- [ ] **Step 3: Implement IDs and application errors**

Create backend/src/research_agent/shared/ids.py:

~~~python
from uuid import UUID, uuid4


EntityId = UUID


def new_id() -> EntityId:
    return uuid4()
~~~

Create backend/src/research_agent/shared/errors.py:

~~~python
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message
~~~

- [ ] **Step 4: Implement the provider protocols**

Create backend/src/research_agent/providers/protocols.py:

~~~python
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel


OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ChatTurn:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class RerankItem:
    id: str
    text: str
    score: float = 0.0


class LLMProvider(Protocol):
    async def generate_structured(
        self,
        *,
        operation: str,
        messages: list[ChatTurn],
        output_type: type[OutputT],
    ) -> OutputT: ...


class EmbeddingProvider(Protocol):
    model_id: str
    dimension: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class RerankerProvider(Protocol):
    model_id: str

    async def rerank(
        self,
        *,
        query: str,
        items: list[RerankItem],
        top_n: int,
    ) -> list[RerankItem]: ...
~~~

- [ ] **Step 5: Implement deterministic fake providers**

`FakeEmbeddingProvider` must reject non-positive dimensions and must return exactly the configured length for every positive dimension. Generate domain-separated SHA-256 blocks until the requested length is filled.

Create backend/src/research_agent/providers/fake.py:

~~~python
import hashlib

from pydantic import BaseModel

from research_agent.providers.protocols import (
    ChatTurn,
    OutputT,
    RerankItem,
)


_EMBEDDING_DOMAIN = b"research-agent-fake-embedding\0"


class FakeLLMProvider:
    def __init__(self, responses: dict[str, dict[str, object]]) -> None:
        self._responses = responses

    def set_response(
        self,
        operation: str,
        response: BaseModel | dict[str, object],
    ) -> None:
        self._responses[operation] = (
            response.model_dump() if isinstance(response, BaseModel) else response
        )

    async def generate_structured(
        self,
        *,
        operation: str,
        messages: list[ChatTurn],
        output_type: type[OutputT],
    ) -> OutputT:
        del messages
        return output_type.model_validate(self._responses[operation])


class FakeEmbeddingProvider:
    model_id = "fake-embedding"

    def __init__(self, dimension: int = 8) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.dimension = dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed_query(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        text_bytes = text.encode("utf-8")
        values: list[float] = []
        block_index = 0
        while len(values) < self.dimension:
            digest = hashlib.sha256(
                _EMBEDDING_DOMAIN
                + block_index.to_bytes(8, "big")
                + text_bytes
            ).digest()
            values.extend(byte / 255.0 for byte in digest)
            block_index += 1
        return values[: self.dimension]


class FakeRerankerProvider:
    model_id = "fake-reranker"

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores

    async def rerank(
        self,
        *,
        query: str,
        items: list[RerankItem],
        top_n: int,
    ) -> list[RerankItem]:
        del query
        scored = [
            RerankItem(id=item.id, text=item.text, score=self._scores.get(item.id, 0.0))
            for item in items
        ]
        return sorted(scored, key=lambda item: item.score, reverse=True)[:top_n]
~~~

- [ ] **Step 6: Run provider tests and static checks**

~~~powershell
Set-Location backend
uv run pytest tests/unit/providers/test_fake_providers.py -v
uv run ruff check src tests
uv run pyright
~~~

Expected: six tests pass; static checks exit 0.

- [ ] **Step 7: Prepare the user commit checkpoint**

~~~powershell
git add backend/src/research_agent/shared backend/src/research_agent/providers backend/tests/unit/providers
git commit -m "feat: define model provider contracts"
~~~

---

### Task 3: Add PostgreSQL, pgvector, migrations, and paper persistence

**Files:**
- Modify: backend/pyproject.toml
- Modify: backend/uv.lock
- Create: backend/alembic.ini
- Create: backend/migrations/script.py.mako
- Create: backend/src/research_agent/shared/db.py
- Create: backend/src/research_agent/papers/domain.py
- Create: backend/src/research_agent/papers/models.py
- Create: backend/src/research_agent/papers/repository.py
- Create: backend/src/research_agent/tasks/domain.py
- Create: backend/src/research_agent/tasks/repository.py
- Create: backend/migrations/env.py
- Create: backend/migrations/versions/0001_papers_tasks.py
- Create: backend/tests/integration/conftest.py
- Create: backend/tests/integration/papers/test_repository.py

**Interfaces:**
- Consumes: EntityId, new_id, Settings
- Produces: DocumentStatus, VersionStatus, TaskStage, TaskStatus, DocumentRecord, DocumentVersionRecord, TaskRecord, PaperRepository, TaskRepository, async_session_factory

- [ ] **Step 1: Add persistence dependencies**

~~~powershell
Set-Location backend
uv add sqlalchemy asyncpg alembic pgvector
uv add --dev "testcontainers[postgres]"
~~~

Register the integration marker in backend/pyproject.toml so pytest does not emit
an unknown-marker warning:

~~~toml
[tool.pytest.ini_options]
markers = [
    "integration: requires Docker-backed infrastructure",
]
~~~

Expected: pyproject.toml and uv.lock include the new packages and pytest knows
the integration marker.

- [ ] **Step 2: Write the failing repository integration test**

Create backend/tests/integration/conftest.py with these responsibilities:

1. Start `pgvector/pgvector:pg17` through `PostgresContainer` and expose an
   `asyncpg` SQLAlchemy URL. The Testcontainer is the authoritative database for
   repository integration tests; it must not depend on the Compose project.
2. Build an Alembic `Config` from backend/alembic.ini, inject the container URL,
   and run `upgrade head` twice before yielding a session. The second upgrade
   proves migration idempotency.
3. Yield a function-scoped `AsyncSession` with
   `join_transaction_mode="create_savepoint"`, then roll back the surrounding
   connection transaction and dispose the engine.

Create backend/tests/integration/papers/test_repository.py:

~~~python
import pytest

from research_agent.papers.domain import DocumentStatus, VersionStatus
from research_agent.papers.repository import SqlPaperRepository
from research_agent.tasks.domain import TaskStage, TaskStatus


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_document_version_and_initial_task(db_session) -> None:
    repository = SqlPaperRepository(db_session)

    created = await repository.create_document_with_version(
        title="Example",
        sha256="a" * 64,
        filename="example.pdf",
        source_object_key="papers/doc/source/version.pdf",
        parser_name="docling",
        parser_version="unparsed",
        pipeline_version="1",
    )
    await db_session.commit()

    loaded = await repository.get_document(created.document_id)
    assert loaded is not None
    assert loaded.status is DocumentStatus.PROCESSING
    assert loaded.current_version_id == created.version_id
    assert loaded.latest_task_id == created.task_id

    version = await repository.get_version(created.version_id)
    assert version is not None
    assert version.status is VersionStatus.PROCESSING

    task = await repository.get_task(created.task_id)
    assert task is not None
    assert task.stage is TaskStage.QUEUED
    assert task.status is TaskStatus.QUEUED

    duplicate_lookup = await repository.find_by_sha256("a" * 64)
    assert duplicate_lookup == loaded
~~~

- [ ] **Step 3: Run the test and confirm model imports fail**

~~~powershell
Set-Location backend
uv run pytest tests/integration/papers/test_repository.py -v
~~~

Expected: FAIL because papers.domain and papers.repository do not exist.

- [ ] **Step 4: Define paper and task domain values**

Create backend/src/research_agent/papers/domain.py:

~~~python
from dataclasses import dataclass
from enum import StrEnum

from research_agent.shared.ids import EntityId


class DocumentStatus(StrEnum):
    PROCESSING = "PROCESSING"
    READY = "READY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"
    DELETING = "DELETING"
    DELETED = "DELETED"


class VersionStatus(StrEnum):
    PROCESSING = "PROCESSING"
    READY = "READY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class CreatedDocument:
    document_id: EntityId
    version_id: EntityId
    task_id: EntityId


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    id: EntityId
    title: str
    sha256: str
    status: DocumentStatus
    current_version_id: EntityId
    latest_task_id: EntityId


@dataclass(frozen=True, slots=True)
class DocumentVersionRecord:
    id: EntityId
    document_id: EntityId
    filename: str
    source_object_key: str
    parser_name: str
    parser_version: str
    pipeline_version: str
    status: VersionStatus
~~~

Create backend/src/research_agent/tasks/domain.py:

~~~python
from dataclasses import dataclass
from enum import StrEnum

from research_agent.shared.ids import EntityId


class TaskStage(StrEnum):
    QUEUED = "QUEUED"
    VALIDATING = "VALIDATING"
    PARSING = "PARSING"
    NORMALIZING = "NORMALIZING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    INDEXING = "INDEXING"
    VERIFYING = "VERIFYING"


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    READY = "READY"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class TaskRecord:
    id: EntityId
    document_version_id: EntityId
    stage: TaskStage
    status: TaskStatus
    progress_current: int
    progress_total: int
    retry_count: int
    error_code: str | None
    error_message: str | None
~~~

- [ ] **Step 5: Create SQLAlchemy models and a complete Alembic environment**

Define the shared declarative Base, async engine factory, and
`async_session_factory` in backend/src/research_agent/shared/db.py. Production
defaults continue to use `Settings.database_url`; tests may inject another URL.

Create backend/alembic.ini and backend/migrations/script.py.mako as part of the
Alembic environment. backend/migrations/env.py must:

1. Import every model module before reading `Base.metadata`.
2. Prefer an explicitly injected Alembic connection when present.
3. Otherwise read `DATABASE_URL` through `Settings` and execute migrations via
   an async SQLAlchemy engine.
4. Support both `uv run alembic upgrade head` and the integration fixture's
   programmatic upgrade.

Implement models with these exact durable columns:

~~~python
class DocumentModel(Base):
    __tablename__ = "document"
    id: Mapped[UUID]
    title: Mapped[str]
    sha256: Mapped[str]
    status: Mapped[str]
    current_version_id: Mapped[UUID | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class DocumentVersionModel(Base):
    __tablename__ = "document_version"
    id: Mapped[UUID]
    document_id: Mapped[UUID]
    filename: Mapped[str]
    source_object_key: Mapped[str]
    parser_name: Mapped[str]
    parser_version: Mapped[str]
    pipeline_version: Mapped[str]
    status: Mapped[str]
    created_at: Mapped[datetime]


class DocumentTaskModel(Base):
    __tablename__ = "document_task"
    id: Mapped[UUID]
    document_version_id: Mapped[UUID]
    stage: Mapped[str]
    status: Mapped[str]
    progress_current: Mapped[int]
    progress_total: Mapped[int]
    retry_count: Mapped[int]
    error_code: Mapped[str | None]
    error_message: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class OutboxEventModel(Base):
    __tablename__ = "outbox_event"
    id: Mapped[UUID]
    event_type: Mapped[str]
    payload: Mapped[dict[str, object]]
    attempts: Mapped[int]
    available_at: Mapped[datetime]
    sent_at: Mapped[datetime | None]
~~~

Migration 0001 must:

~~~sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE UNIQUE INDEX uq_document_sha256_active
ON document (sha256)
WHERE status <> 'DELETED';
CREATE UNIQUE INDEX uq_task_version_stage
ON document_task (document_version_id, stage);
CREATE INDEX ix_outbox_unsent
ON outbox_event (available_at)
WHERE sent_at IS NULL;
~~~

- [ ] **Step 6: Implement repository protocols and SQL repositories**

Define runtime-checkable `PaperRepository` and `TaskRepository` Protocols. The
paper protocol exposes every method shown below. The task protocol exposes
`get_task`, `update_task`, and `commit`; concrete SQL repositories must satisfy
the protocols under Pyright without importing FastAPI, Celery, MinIO, Docling,
or LangChain types.

Create SqlPaperRepository with these signatures:

~~~python
class SqlPaperRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_document_with_version(
        self,
        *,
        title: str,
        sha256: str,
        filename: str,
        source_object_key: str,
        parser_name: str,
        parser_version: str,
        pipeline_version: str,
    ) -> CreatedDocument: ...

    async def get_document(self, document_id: EntityId) -> DocumentRecord | None: ...

    async def find_by_sha256(self, sha256: str) -> DocumentRecord | None: ...

    async def get_version(
        self,
        version_id: EntityId,
    ) -> DocumentVersionRecord | None: ...

    async def get_task(self, task_id: EntityId) -> TaskRecord | None: ...

    async def switch_current_version(
        self,
        document_id: EntityId,
        version_id: EntityId,
        status: DocumentStatus,
    ) -> None: ...

    async def commit(self) -> None: ...
~~~

`create_document_with_version` must create a document, its first version, and an
initial task in one unit of work. The task starts with `stage=QUEUED`,
`status=QUEUED`, `progress_current=0`, and `progress_total=len(TaskStage)`. This
makes the non-null `CreatedDocument.task_id` and `DocumentRecord.latest_task_id`
valid immediately.

`get_document` and `find_by_sha256` must join the current version and select the
most recent document task deterministically by `(created_at DESC, id DESC)`.
`get_version` and `get_task` return their complete record types. Implement
`SqlTaskRepository` with these signatures:

~~~python
class SqlTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_task(self, task_id: EntityId) -> TaskRecord | None: ...

    async def update_task(
        self,
        task_id: EntityId,
        *,
        stage: TaskStage,
        status: TaskStatus,
        progress_current: int,
        progress_total: int,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None: ...

    async def commit(self) -> None: ...
~~~

- [ ] **Step 7: Run migrations and repository tests**

~~~powershell
docker compose -f infra/compose.yaml up -d postgres
Set-Location backend
$env:DATABASE_URL = "postgresql+asyncpg://research:research@127.0.0.1:5432/research"
uv run alembic upgrade head
uv run alembic upgrade head
uv run alembic current --check-heads
Remove-Item Env:DATABASE_URL
uv run pytest tests/integration/papers/test_repository.py -v -m integration
uv run ruff check src tests
uv run pyright
~~~

Expected: Compose PostgreSQL is reachable from the host through `127.0.0.1`, the
first migration applies, the second upgrade is a no-op, the current revision is
at all heads, the isolated Testcontainer repository test passes, and static
checks pass. Always remove the temporary host `DATABASE_URL` before running the
Testcontainer test so it cannot accidentally use Compose.

- [ ] **Step 8: Prepare the user commit checkpoint**

~~~powershell
git add backend/pyproject.toml backend/uv.lock backend/alembic.ini backend/migrations backend/src/research_agent/shared/db.py backend/src/research_agent/papers backend/src/research_agent/tasks backend/tests/integration/conftest.py backend/tests/integration/papers
git commit -m "feat: persist papers and processing tasks"
~~~

---

### Task 4: Implement private object storage and idempotent PDF upload

**Files:**
- Modify: backend/pyproject.toml
- Modify: backend/uv.lock
- Modify: backend/src/research_agent/papers/domain.py
- Modify: backend/src/research_agent/papers/repository.py
- Create: backend/src/research_agent/papers/storage.py
- Create: backend/src/research_agent/papers/service.py
- Create: backend/src/research_agent/papers/routes.py
- Modify: backend/src/research_agent/main.py
- Create: backend/tests/unit/papers/test_upload_service.py
- Create: backend/tests/unit/papers/test_storage.py
- Modify: backend/tests/integration/conftest.py
- Create: backend/tests/integration/papers/test_upload_api.py
- Create: backend/tests/integration/papers/test_upload_race.py
- Create: backend/tests/integration/papers/test_minio_smoke.py

**Interfaces:**
- Consumes: DocumentRecord, AppError, Settings, async_session_factory
- Produces: UploadIds, DuplicateDocumentError, PaperPersistenceError, UploadPaperRepository, ObjectStorage, MinioObjectStorage, UploadPaperCommand, UploadReceipt, PaperService.upload_pdf, POST /api/v1/documents

- [ ] **Step 1: Add object storage and multipart dependencies**

~~~powershell
Set-Location backend
uv add minio python-multipart
~~~

- [ ] **Step 2: Write failing upload-service tests**

Create backend/tests/unit/papers/test_upload_service.py:

~~~python
import hashlib
from io import BytesIO
from typing import BinaryIO
from uuid import UUID

import pytest

from research_agent.papers.domain import DocumentRecord, DocumentStatus
from research_agent.papers.service import PaperService, UploadPaperCommand
from research_agent.shared.errors import AppError


class FakePaperRepository:
    def __init__(self) -> None:
        self.existing: DocumentRecord | None = None
        self.created: list[dict[str, object]] = []
        self.outbox: list[dict[str, object]] = []
        self.commit_calls = 0
        self.rollback_calls = 0

    async def find_by_sha256(self, sha256: str) -> DocumentRecord | None:
        if self.existing is not None and self.existing.sha256 == sha256:
            return self.existing
        return None

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


class FakeObjectStorage:
    def __init__(self) -> None:
        self.put_calls: list[str] = []
        self.delete_calls: list[str] = []

    def put(self, *, key: str, stream: BinaryIO, content_type: str) -> None:
        del stream, content_type
        self.put_calls.append(key)

    def delete(self, *, key: str) -> None:
        self.delete_calls.append(key)


@pytest.mark.asyncio
async def test_upload_rejects_non_pdf() -> None:
    fake_paper_repository = FakePaperRepository()
    fake_object_storage = FakeObjectStorage()
    service = PaperService(fake_paper_repository, fake_object_storage)
    command = UploadPaperCommand(
        filename="paper.txt",
        content_type="text/plain",
        stream=BytesIO(b"not-pdf"),
    )

    with pytest.raises(AppError) as error:
        await service.upload_pdf(command)

    assert error.value.code == "INVALID_PDF"


@pytest.mark.asyncio
async def test_duplicate_hash_returns_existing_document() -> None:
    fake_paper_repository = FakePaperRepository()
    fake_object_storage = FakeObjectStorage()
    content = b"%PDF-1.7\ncontent"
    fake_paper_repository.existing = DocumentRecord(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        title="existing",
        sha256=hashlib.sha256(content).hexdigest(),
        status=DocumentStatus.PROCESSING,
        current_version_id=UUID("00000000-0000-0000-0000-000000000002"),
        latest_task_id=UUID("00000000-0000-0000-0000-000000000003"),
    )
    service = PaperService(fake_paper_repository, fake_object_storage)

    receipt = await service.upload_pdf(
        UploadPaperCommand(
            filename="paper.pdf",
            content_type="application/pdf",
            stream=BytesIO(content),
        )
    )

    assert receipt.duplicate is True
    assert fake_object_storage.put_calls == []
~~~

The fake repository also implements the exact `create_upload` and
`add_outbox_event` signatures defined in Step 5, appending their keyword
arguments to `created` and `outbox`. The fake storage implements `get` and
`presigned_get` with the exact ObjectStorage signatures. Tests instantiate
these fakes directly; they must not depend on undeclared pytest fixtures.

Add separate RED cases before implementation for:

- `application/pdf` with a non-PDF signature;
- successful upload creating one object, one document/version/task, one
  `document.uploaded` outbox event, and one commit;
- a persistence failure rolling back and deleting only the generated object;
- `hash_stream` processing a stream larger than 1 MiB and restoring its original
  position;
- the MinIO adapter creating a missing bucket without installing an anonymous
  bucket policy, and forwarding put/get/delete/presign calls to the configured
  private bucket.

- [ ] **Step 3: Run tests and verify the missing service failure**

~~~powershell
Set-Location backend
uv run pytest tests/unit/papers/test_upload_service.py -v
~~~

Expected: FAIL because papers.service does not exist.

- [ ] **Step 4: Define storage and upload interfaces**

Create backend/src/research_agent/papers/storage.py:

~~~python
from datetime import timedelta
from typing import BinaryIO, Protocol


class ObjectStorage(Protocol):
    def put(self, *, key: str, stream: BinaryIO, content_type: str) -> None: ...

    def get(self, *, key: str) -> BinaryIO: ...

    def delete(self, *, key: str) -> None: ...

    def presigned_get(self, *, key: str, expires: timedelta) -> str: ...
~~~

Create `UploadIds` in papers/domain.py so the repository layer never imports the
application service module:

~~~python
from dataclasses import dataclass

from research_agent.shared.ids import EntityId, new_id


@dataclass(frozen=True, slots=True)
class UploadIds:
    document_id: EntityId
    version_id: EntityId
    task_id: EntityId
    outbox_event_id: EntityId

    @classmethod
    def create(cls) -> "UploadIds":
        return cls(new_id(), new_id(), new_id(), new_id())
~~~

Create the command/result types in papers/service.py:

~~~python
from dataclasses import dataclass
from typing import BinaryIO

from research_agent.shared.ids import EntityId


@dataclass(slots=True)
class UploadPaperCommand:
    filename: str
    content_type: str
    stream: BinaryIO


@dataclass(frozen=True, slots=True)
class UploadReceipt:
    document_id: EntityId
    version_id: EntityId
    task_id: EntityId
    duplicate: bool
~~~

Define `DuplicateDocumentError` in papers/domain.py with the duplicate SHA-256
value and define `PaperPersistenceError` for every other SQL integrity failure.
These are persistence-neutral signals consumed by PaperService; do not expose
SQLAlchemy `IntegrityError` outside the SQL repository.

`MinioObjectStorage` accepts an injected `Minio` client and bucket name, plus a
`from_settings(Settings)` constructor. Before the first write it idempotently
creates a missing bucket. A newly created MinIO bucket is private by default;
the adapter must never install an anonymous bucket policy. `get()` returns a
binary stream whose `close()` also releases the MinIO HTTP response connection.

- [ ] **Step 5: Implement upload validation, hashing, cleanup, and outbox creation**

Extend SqlPaperRepository with upload-specific methods. They accept metadata and generated IDs, never framework UploadFile objects or byte streams:

~~~python
async def create_upload(
    self,
    *,
    ids: UploadIds,
    title: str,
    filename: str,
    sha256: str,
    source_object_key: str,
    parser_name: str,
    parser_version: str,
    pipeline_version: str,
) -> None: ...

async def add_outbox_event(
    self,
    *,
    event_id: EntityId,
    event_type: str,
    payload: dict[str, object],
) -> None: ...

async def rollback(self) -> None: ...
~~~

Define a runtime-checkable `UploadPaperRepository` Protocol containing
`find_by_sha256`, `create_upload`, `add_outbox_event`, `commit`, and `rollback`.
`PaperService` depends only on this narrow protocol. `SqlPaperRepository`
implements it with an explicit Pyright conformance witness; its existing
`PaperRepository` conformance remains intact. `create_upload` inserts the document,
version, and initial `QUEUED` task using the caller-provided IDs and flushes in
the same unit of work. It catches only the PostgreSQL constraint
`uq_document_sha256_active` and raises `DuplicateDocumentError(sha256)`; every
other `IntegrityError` is wrapped as `PaperPersistenceError` without SQL or
driver details. `add_outbox_event`
adds the event with `attempts=0`, `available_at=now()` and `sent_at=None`.

PaperService.upload_pdf must execute in this order:

~~~python
async def upload_pdf(self, command: UploadPaperCommand) -> UploadReceipt:
    if command.content_type != "application/pdf":
        raise AppError(code="INVALID_PDF", message="Only PDF files are supported")
    command.stream.seek(0)
    sha256 = await asyncio.to_thread(validate_and_hash_pdf, command.stream)
    command.stream.seek(0)
    existing = await self._papers.find_by_sha256(sha256)
    if existing is not None:
        return UploadReceipt(
            document_id=existing.id,
            version_id=existing.current_version_id,
            task_id=existing.latest_task_id,
            duplicate=True,
        )
    ids = UploadIds.create()
    key = "papers/" + str(ids.document_id) + "/source/" + str(ids.version_id) + ".pdf"
    await asyncio.to_thread(
        self._storage.put,
        key=key,
        stream=command.stream,
        content_type="application/pdf",
    )
    try:
        await self._papers.create_upload(
            ids=ids,
            title=command.filename.removesuffix(".pdf"),
            filename=command.filename,
            sha256=sha256,
            source_object_key=key,
            parser_name="docling",
            parser_version="unparsed",
            pipeline_version="1",
        )
        await self._papers.add_outbox_event(
            event_id=ids.outbox_event_id,
            event_type="document.uploaded",
            payload={"version_id": str(ids.version_id), "task_id": str(ids.task_id)},
        )
        await self._papers.commit()
    except DuplicateDocumentError:
        await self._papers.rollback()
        await asyncio.to_thread(self._storage.delete, key=key)
        existing = await self._papers.find_by_sha256(sha256)
        if existing is None:
            raise AppError(
                code="DUPLICATE_DOCUMENT_UNAVAILABLE",
                message="The duplicate document could not be reloaded",
                retryable=True,
            )
        return UploadReceipt(
            document_id=existing.id,
            version_id=existing.current_version_id,
            task_id=existing.latest_task_id,
            duplicate=True,
        )
    except PaperPersistenceError as error:
        await self._papers.rollback()
        await asyncio.to_thread(self._storage.delete, key=key)
        raise AppError(
            code="PERSISTENCE_ERROR",
            message="The upload could not be persisted",
            retryable=True,
        ) from error
    except Exception:
        await self._papers.rollback()
        await asyncio.to_thread(self._storage.delete, key=key)
        raise
    return UploadReceipt(
        document_id=ids.document_id,
        version_id=ids.version_id,
        task_id=ids.task_id,
        duplicate=False,
    )
~~~

`validate_and_hash_pdf` seeks to offset zero, validates `%PDF-`, reads SHA-256 in
1 MiB blocks, and restores the original stream position in `finally`, including
when validation or reading raises. The complete validation/hash operation and
all synchronous MinIO operations run through `asyncio.to_thread`; no upload
stream or MinIO operation may block the event loop.

If two equal PDFs pass the initial hash lookup concurrently, the unique active-sha256 constraint is authoritative: roll back the losing transaction, delete its generated MinIO object, reload the winning DocumentRecord in the new transaction implicitly opened by the next repository query, and return it with duplicate=True. Never expose SQLAlchemy `IntegrityError` to PaperService, the route, or the caller. A two-session integration test must force both initial lookups to observe no existing document, then verify one first-upload receipt, one duplicate receipt with the winner's IDs, one durable document/version/task/outbox set, and deletion of the one generated key that differs from the durable winner key. The winner key must remain stored.

- [ ] **Step 6: Add the upload route and error mapping**

Create POST /api/v1/documents:

~~~python
@router.post("/documents", status_code=202, response_model=UploadResponse)
async def upload_document(
    file: UploadFile,
    service: Annotated[PaperService, Depends(get_paper_service)],
) -> UploadResponse:
    receipt = await service.upload_pdf(
        UploadPaperCommand(
            filename=file.filename or "paper.pdf",
            content_type=file.content_type or "",
            stream=file.file,
        )
    )
    return UploadResponse.model_validate(receipt, from_attributes=True)
~~~

Map AppError to application/problem+json with code, message, retryable, trace_id, and details.

Define explicit dependencies in papers/routes.py:

- `get_db_session()` yields and closes one `AsyncSession` from
  `async_session_factory`;
- `get_object_storage()` builds a cached `MinioObjectStorage` from `Settings`;
- `get_paper_service()` combines a request session and object storage;
- tests override the database and object-storage dependencies, never production
  globals.

In main.py, install one lightweight HTTP middleware that accepts a non-empty
`X-Trace-ID` header or generates `str(new_id())`, stores it at
`request.state.trace_id`, and returns the same header. Register the papers router
under `/api/v1`. Register an `AppError` exception handler returning status 400
for `INVALID_PDF`, 503 for retryable errors, and 500 otherwise. Its
`application/problem+json` body contains `type`, `title`, `status`, `code`,
`message`, `retryable`, `trace_id`, and `details`.

API integration tests use the migrated pgvector Testcontainer and an in-memory
ObjectStorage override. They verify invalid MIME/signature responses, a valid
202 response, durable document/version/task/outbox rows, duplicate IDs, no
second object write, and matching response/body trace IDs.

- [ ] **Step 7: Run unit and API integration tests**

~~~powershell
Set-Location backend
uv run pytest tests/unit/papers/test_upload_service.py tests/unit/papers/test_storage.py -v
uv run pytest tests/integration/papers/test_upload_api.py tests/integration/papers/test_upload_race.py -v -m integration
uv run pytest -q -p no:cacheprovider
uv run ruff check src tests
uv run pyright
uv lock --check
~~~

Expected: invalid files return 400, valid files return 202, duplicates return the original IDs, and only valid first uploads create MinIO objects.

Run the real adapter smoke against Compose MinIO from the repository root. Use
`MINIO_ENDPOINT=127.0.0.1:9000` only for this host-side command:

~~~powershell
docker compose -f infra/compose.yaml up -d minio
Set-Location backend
$env:MINIO_ENDPOINT = "127.0.0.1:9000"
uv run pytest tests/integration/papers/test_minio_smoke.py -v -m integration
Remove-Item Env:MINIO_ENDPOINT
~~~

The smoke test creates a unique object key, verifies authenticated put/get and a
presigned URL, verifies anonymous bucket listing is denied, then deletes only
its object. It never removes the shared bucket or unrelated objects.

- [ ] **Step 8: Prepare the user commit checkpoint**

~~~powershell
git add backend/pyproject.toml backend/uv.lock backend/src/research_agent/papers backend/src/research_agent/main.py backend/tests/unit/papers backend/tests/integration/conftest.py backend/tests/integration/papers docs/superpowers/plans/2026-07-14-research-agent-mvp-implementation.md docs/superpowers/plans/2026-07-14-research-agent-mvp-implementation.zh-CN.md
git commit -m "feat: upload and deduplicate PDF papers"
~~~

---

### Task 5: Dispatch durable Celery jobs through Redis

**Files:**
- Modify: backend/pyproject.toml
- Create: backend/src/research_agent/tasks/celery_app.py
- Create: backend/src/research_agent/tasks/outbox_relay.py
- Create: backend/src/research_agent/ingestion/celery_tasks.py
- Modify: backend/src/research_agent/main.py
- Modify: infra/compose.yaml
- Create: backend/tests/unit/tasks/test_celery_config.py
- Create: backend/tests/integration/tasks/test_outbox_relay.py

**Interfaces:**
- Consumes: outbox_event rows, TaskRepository, Settings
- Produces: celery_app, process_document(version_id: str), OutboxRelay.run_once

- [ ] **Step 1: Add Celery with Redis transport**

~~~powershell
Set-Location backend
uv add "celery[redis]"
~~~

- [ ] **Step 2: Write failing Celery configuration tests**

Create backend/tests/unit/tasks/test_celery_config.py:

~~~python
from research_agent.tasks.celery_app import create_celery_app


def test_celery_uses_late_ack_and_single_prefetch() -> None:
    app = create_celery_app("redis://redis:6379/0")

    assert app.conf.task_acks_late is True
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.task_ignore_result is True
    assert app.conf.broker_transport_options["visibility_timeout"] == 7200
~~~

- [ ] **Step 3: Confirm the configuration test fails**

~~~powershell
Set-Location backend
uv run pytest tests/unit/tasks/test_celery_config.py -v
~~~

Expected: FAIL because tasks.celery_app does not exist.

- [ ] **Step 4: Implement Celery configuration**

Create backend/src/research_agent/tasks/celery_app.py:

~~~python
from celery import Celery


def create_celery_app(broker_url: str) -> Celery:
    app = Celery("research_agent", broker=broker_url)
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_ignore_result=True,
        worker_prefetch_multiplier=1,
        broker_transport_options={"visibility_timeout": 7200},
        task_routes={
            "research_agent.ingestion.process_document": {"queue": "paper_ingestion"},
            "research_agent.notes.generate_note": {"queue": "note_generation"},
        },
        imports=("research_agent.ingestion.celery_tasks",),
    )
    return app


celery_app = create_celery_app(get_settings().redis_url)
~~~

Import get_settings from research_agent.config. When Task 15 adds note generation, extend imports with research_agent.notes.celery_tasks.

Task 5 deliberately precedes the real ingestion orchestration completed in Task 7. Until Task 7 replaces it, register `process_document` as a safety bridge that calls `self.retry(countdown=60)` with task-level `max_retries=None`; it must never report successful processing or acknowledge-and-drop a paper job. Add a focused unit test proving the bridge raises Celery `Retry` and retains the same `version_id` in the retry path. This temporary bridge performs no PDF work and is removed when Task 7 installs the real pipeline entry point.

- [ ] **Step 5: Implement the transactional outbox relay**

OutboxRelay.run_once must:

~~~python
async def run_once(self, limit: int = 20) -> int:
    events = await self._repository.lock_unsent(limit=limit)
    sent = 0
    for event in events:
        try:
            self._celery.send_task(
                "research_agent.ingestion.process_document",
                kwargs={"version_id": event.payload["version_id"]},
                queue="paper_ingestion",
            )
        except Exception:
            await self._repository.record_failure(event.id)
            continue
        await self._repository.mark_sent(event.id)
        sent += 1
    await self._repository.commit()
    return sent
~~~

Start a lightweight relay loop in FastAPI lifespan. It sleeps one second between runs and stops cleanly on shutdown. It performs no PDF work in the API process.

The SQLAlchemy outbox repository must select due unsent rows with `FOR UPDATE SKIP LOCKED`, increment `attempts` after a broker send failure, and set `sent_at` only after `send_task` returns. Each lifespan pass owns a fresh async session/transaction; an unexpected pass failure is rolled back and logged without permanently killing the relay loop. Domain-facing relay interfaces expose project records and protocols rather than SQLAlchemy or Celery framework types.

- [ ] **Step 6: Add worker service**

Extend infra/compose.yaml:

~~~yaml
  worker:
    build:
      context: ..
      dockerfile: backend/Dockerfile
    command:
      - uv
      - run
      - celery
      - -A
      - research_agent.tasks.celery_app:celery_app
      - worker
      - --loglevel=INFO
      - --queues=paper_ingestion,note_generation
      - --concurrency=1
    env_file: ../.env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
~~~

- [ ] **Step 7: Test relay delivery and duplicate safety**

~~~powershell
Set-Location backend
uv run pytest tests/unit/tasks/test_celery_config.py tests/integration/tasks/test_outbox_relay.py -v
docker compose -f ../infra/compose.yaml up -d --build worker
docker compose -f ../infra/compose.yaml logs --tail 50 worker
~~~

Expected: one outbox event produces one Celery send call, failed sends remain unsent, and the worker reports readiness on both queues.

- [ ] **Step 8: Prepare the user commit checkpoint**

~~~powershell
git add backend/pyproject.toml backend/uv.lock backend/src/research_agent/tasks backend/src/research_agent/ingestion/celery_tasks.py backend/src/research_agent/main.py backend/tests/unit/tasks backend/tests/integration/tasks infra/compose.yaml
git commit -m "feat: dispatch durable paper jobs"
~~~

---

### Task 6: Parse PDFs into versioned Docling artifacts

**Files:**
- Modify: backend/pyproject.toml
- Modify: backend/uv.lock
- Modify: backend/src/research_agent/papers/domain.py
- Modify: backend/src/research_agent/papers/models.py
- Modify: backend/src/research_agent/papers/repository.py
- Modify: backend/src/research_agent/tasks/repository.py
- Create: backend/src/research_agent/ingestion/contracts.py
- Create: backend/src/research_agent/ingestion/docling_parser.py
- Create: backend/src/research_agent/ingestion/pipeline.py
- Create: backend/migrations/versions/0002_parse_artifacts.py
- Create: backend/tests/helpers/pdf_factory.py
- Create: backend/tests/unit/ingestion/test_dependency_lock.py
- Create: backend/tests/unit/ingestion/test_pipeline.py
- Create: backend/tests/integration/ingestion/test_docling_parser.py

**Interfaces:**
- Consumes: ObjectStorage, TaskRepository, PaperRepository
- Produces: ParsedElement, ParsedDocument, ParseQualityReport, DocumentParser, DoclingDocumentParser, IngestionPipeline.parse

- [ ] **Step 1: Add CPU Docling and PDF-test dependencies**

Add the official CPU PyTorch index and source pin to backend/pyproject.toml:

~~~toml
[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true

[tool.uv.sources]
torch = [{ index = "pytorch-cpu" }]
~~~

Then run:

~~~powershell
Set-Location backend
uv add docling
uv add --dev reportlab
~~~

- [ ] **Step 2: Write a failing parser-contract test**

Create backend/tests/unit/ingestion/test_pipeline.py:

~~~python
from research_agent.ingestion.contracts import ParsedDocument, ParsedElement
from research_agent.ingestion.pipeline import IngestionPipeline


async def test_parse_persists_versioned_artifacts(
    fake_parser,
    fake_object_storage,
    fake_paper_repository,
    fake_task_repository,
) -> None:
    fake_parser.result = ParsedDocument(
        markdown="# Title\n\nBody",
        json_bytes=b'{"name":"paper"}',
        elements=[
            ParsedElement(
                element_id="e1",
                kind="text",
                text="Body",
                section_path=("Title",),
                page_number=1,
                bbox=None,
            )
        ],
        images=[],
        page_count=1,
    )
    pipeline = IngestionPipeline(
        parser=fake_parser,
        storage=fake_object_storage,
        papers=fake_paper_repository,
        tasks=fake_task_repository,
    )

    result = await pipeline.parse(version_id=fake_paper_repository.version_id)

    assert result.quality.parsed_pages == 1
    assert fake_object_storage.has_suffix("document.json")
    assert fake_object_storage.has_suffix("document.md")
~~~

- [ ] **Step 3: Confirm the parser contract test fails**

~~~powershell
Set-Location backend
uv run pytest tests/unit/ingestion/test_pipeline.py -v
~~~

Expected: FAIL because ingestion.contracts and ingestion.pipeline do not exist.

- [ ] **Step 4: Define normalized parse contracts**

Create backend/src/research_agent/ingestion/contracts.py:

~~~python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BoundingBox:
    left: float
    top: float
    right: float
    bottom: float


@dataclass(frozen=True, slots=True)
class ParsedElement:
    element_id: str
    kind: str
    text: str
    section_path: tuple[str, ...]
    page_number: int
    bbox: BoundingBox | None


@dataclass(frozen=True, slots=True)
class ParsedImage:
    image_id: str
    content: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    markdown: str
    json_bytes: bytes
    elements: list[ParsedElement]
    images: list[ParsedImage]
    page_count: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParseQualityReport:
    total_pages: int
    parsed_pages: int
    body_characters: int
    empty_pages: tuple[int, ...]
    section_count: int
    table_count: int
    image_count: int
    warnings: tuple[str, ...]
~~~

- [ ] **Step 5: Implement Docling conversion behind DocumentParser**

DocumentParser accepts a local Path and returns only project types:

~~~python
class DocumentParser(Protocol):
    name: str
    version: str

    def parse(self, source: Path) -> ParsedDocument: ...
~~~

DoclingDocumentParser must configure:

~~~python
pipeline_options = PdfPipelineOptions()
pipeline_options.do_ocr = False
pipeline_options.do_table_structure = True
pipeline_options.generate_picture_images = True
converter = DocumentConverter(
    allowed_formats=[InputFormat.PDF],
    format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
    },
)
~~~

Normalize Docling text, table, picture, section path, page number, top-left-origin bounding box, Markdown, and lossless JSON into the contracts above. Use Docling `self_ref` values as stable element/image IDs where available; use `page_number=0` as the explicit unknown-provenance sentinel instead of inventing a positive page number, and add a warning to `ParsedDocument.warnings` for every normalized content item that lacks page provenance. No Docling class leaves this adapter. Picture extraction must emit deterministic PNG bytes and content type `image/png`.

- [ ] **Step 6: Implement artifact keys and quality rules**

IngestionPipeline.parse must:

1. Set task stage PARSING.
2. Download source to TemporaryDirectory.
3. Parse with DocumentParser.
4. Store document.json, document.md, and images under the versioned keys.
5. Calculate ParseQualityReport.
6. Persist artifact metadata.
7. Mark NEEDS_REVIEW when parsed pages are below 95% or body text is below 100 characters per parsed page.

Use the design-specified deterministic keys:

~~~text
papers/{document_id}/parsed/{version_id}/document.json
papers/{document_id}/parsed/{version_id}/document.md
papers/{document_id}/images/{version_id}/{image_id}.png
~~~

Migration `0002_parse_artifacts` extends `document_version` with nullable `parsed_json_object_key`, `parsed_markdown_object_key`, `image_object_keys` JSONB, and `parse_quality` JSONB columns so pre-existing unparsed rows remain valid. PostgreSQL stores the durable object-key manifest and quality report; MinIO stores the large artifact bytes. Extend the project repository interfaces with the narrow operations required to load a version, persist this manifest plus parser/status metadata, and load the current task by `document_version_id`. Do not expose SQLAlchemy, Docling, MinIO, or Celery types through those interfaces.

All synchronous object-storage and Docling operations run through `asyncio.to_thread`. Close the downloaded MinIO stream, always remove the temporary directory, and use deterministic object keys so a retry overwrites the same artifacts instead of creating duplicates. Persist the manifest only after every artifact upload succeeds. On parser/storage failure, roll back partial database state and durably mark the task, version, and document `FAILED` without leaking credentials or full paper text in the error. When the quality rule triggers, persist document/version `NEEDS_REVIEW` and terminate the current task as non-retryable `FAILED` with error code `PARSE_QUALITY_REVIEW`; otherwise leave the version/document processing state ready for Task 7 to continue and keep the task at `PARSING/RUNNING`.

Use this deterministic rule:

~~~python
needs_review = (
    report.parsed_pages / max(report.total_pages, 1) < 0.95
    or report.body_characters < report.parsed_pages * 100
)
~~~

- [ ] **Step 7: Run unit and one real Docling integration test**

~~~powershell
Set-Location backend
uv run pytest tests/unit/ingestion/test_pipeline.py -v
uv run pytest tests/integration/ingestion/test_docling_parser.py -v -m integration
~~~

Expected: normalized unit tests cover deterministic keys, manifest persistence, quality thresholds, idempotent overwrite, stream/temp cleanup, and durable failure state; generated two-page PDF produces two parsed pages, Markdown containing both page texts, lossless JSON, and non-empty page/bounding-box provenance. The real Docling test must run with OCR disabled and must not require a real provider key.

- [ ] **Step 8: Prepare the user commit checkpoint**

~~~powershell
git add backend/pyproject.toml backend/uv.lock backend/src/research_agent/ingestion backend/src/research_agent/papers backend/src/research_agent/tasks/repository.py backend/migrations/versions/0002_parse_artifacts.py backend/tests/helpers/pdf_factory.py backend/tests/unit/ingestion backend/tests/integration/ingestion
git commit -m "feat: parse PDFs into versioned artifacts"
~~~

---

### Task 7: Create structure-aware chunks and finish the ingestion pipeline

**Files:**
- Create: backend/src/research_agent/ingestion/chunker.py
- Modify: backend/src/research_agent/ingestion/pipeline.py
- Modify: backend/src/research_agent/ingestion/celery_tasks.py
- Create: backend/src/research_agent/ingestion/models.py
- Modify: backend/src/research_agent/papers/repository.py
- Modify: backend/src/research_agent/shared/errors.py
- Modify: backend/migrations/env.py
- Create: backend/migrations/versions/0003_chunks.py
- Create: backend/tests/unit/ingestion/test_chunker.py
- Create: backend/tests/integration/ingestion/test_pipeline_ready.py
- Modify: backend/tests/unit/tasks/test_celery_config.py

**Interfaces:**
- Consumes: ParsedElement, ParsedDocument, PaperRepository, TaskRepository
- Produces: ChunkingConfig, ChunkDraft, StructuralChunker.chunk, document_chunk rows, process_document(version_id)

- [ ] **Step 1: Write failing structure-aware chunker tests**

Create backend/tests/unit/ingestion/test_chunker.py:

~~~python
from research_agent.ingestion.chunker import ChunkingConfig, StructuralChunker
from research_agent.ingestion.contracts import ParsedElement


def element(element_id: str, section: str, text: str, page: int) -> ParsedElement:
    return ParsedElement(
        element_id=element_id,
        kind="text",
        text=text,
        section_path=(section,),
        page_number=page,
        bbox=None,
    )


def test_chunker_does_not_mix_sections() -> None:
    chunker = StructuralChunker(
        ChunkingConfig(target_tokens=8, overlap_tokens=2),
        token_counter=lambda text: len(text.split()),
    )

    chunks = chunker.chunk(
        [
            element("a", "Introduction", "one two three four", 1),
            element("b", "Method", "five six seven eight", 2),
        ]
    )

    assert [chunk.section_path for chunk in chunks] == [
        ("Introduction",),
        ("Method",),
    ]


def test_chunk_keeps_page_and_element_provenance() -> None:
    chunker = StructuralChunker(
        ChunkingConfig(target_tokens=20, overlap_tokens=2),
        token_counter=lambda text: len(text.split()),
    )

    chunks = chunker.chunk(
        [
            element("a", "Method", "one two", 3),
            element("b", "Method", "three four", 4),
        ]
    )

    assert chunks[0].page_start == 3
    assert chunks[0].page_end == 4
    assert chunks[0].element_ids == ("a", "b")
~~~

- [ ] **Step 2: Run tests and verify the chunker is missing**

~~~powershell
Set-Location backend
uv run pytest tests/unit/ingestion/test_chunker.py -v
~~~

Expected: FAIL because ingestion.chunker does not exist.

- [ ] **Step 3: Implement chunk contracts and algorithm**

Create:

~~~python
@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    target_tokens: int = 700
    overlap_tokens: int = 100


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    chunk_index: int
    section_path: tuple[str, ...]
    content: str
    content_type: str
    page_start: int
    page_end: int
    element_ids: tuple[str, ...]
    token_count: int
~~~

StructuralChunker must:

- flush when section_path changes;
- flush before exceeding target_tokens;
- repeat only trailing elements whose combined tokens do not exceed overlap_tokens;
- keep tables and figure captions with their section path;
- mark bibliography chunks as content_type reference.

Validate `target_tokens > 0`, `overlap_tokens >= 0`, and
`overlap_tokens < target_tokens`. Whitespace-only elements are ignored. A
single oversized table or element remains atomic. `reference` overrides the
other content types; otherwise table-only chunks use `table`, picture-only
chunks use `figure`, and mixed/ordinary chunks use `text`. Bibliography labels
are matched case-insensitively for `References`/`Bibliography` and explicitly
for `参考文献`.

- [ ] **Step 4: Add chunk persistence**

Migration 0003 creates:

~~~sql
CREATE TABLE document_chunk (
    id uuid PRIMARY KEY,
    document_version_id uuid NOT NULL REFERENCES document_version(id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    section_path jsonb NOT NULL,
    content text NOT NULL,
    lexical_text text NOT NULL,
    content_type text NOT NULL,
    page_start integer NOT NULL,
    page_end integer NOT NULL,
    element_ids jsonb NOT NULL,
    token_count integer NOT NULL,
    pipeline_version text NOT NULL,
    search_vector_en tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(content, ''))
    ) STORED,
    search_vector_zh tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(lexical_text, ''))
    ) STORED,
    UNIQUE (document_version_id, chunk_index, pipeline_version)
);
CREATE INDEX ix_chunk_search_en ON document_chunk USING gin (search_vector_en);
CREATE INDEX ix_chunk_search_zh ON document_chunk USING gin (search_vector_zh);
~~~

Use application-side Chinese tokenization to populate lexical_text; English text remains unchanged.

`DocumentChunkModel` must be imported by `migrations/env.py` so Alembic
autogenerate and schema-drift checks see the table. Chunk persistence uses one
transactional PostgreSQL upsert keyed by
`(document_version_id, chunk_index, pipeline_version)`, preserves existing row
IDs on retries, and deletes stale indexes for the same version/pipeline. The
repository translates SQLAlchemy failures into the existing sanitized project
error boundary.

- [ ] **Step 5: Complete process_document orchestration**

The Celery entry point must only open dependencies and call the application pipeline:

~~~python
@celery_app.task(
    name="research_agent.ingestion.process_document",
    bind=True,
    autoretry_for=(TransientInfrastructureError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def process_document(self, version_id: str) -> None:
    run_async(run_ingestion_job(UUID(version_id)))
~~~

Pipeline.run transitions through VALIDATING, PARSING, NORMALIZING, CHUNKING, then stops at EMBEDDING until Task 9 supplies the embedding stage.

`run_ingestion_job` creates a fresh async engine and shared session for each
Celery invocation, builds the parser/storage/paper/task/chunk dependencies on
that session, and disposes the engine in `finally`; it must not reuse the API's
global async engine across repeated `asyncio.run` loops. Retryable project
errors are mapped to `TransientInfrastructureError`; invalid UUIDs and
non-retryable quality/application errors are not autoretried.

The durable stage sequence uses `RUNNING` with progress values 1 through 5 for
`VALIDATING`, `PARSING`, `NORMALIZING`, `CHUNKING`, and `EMBEDDING`. After
chunk persistence, document/version remain `PROCESSING` and the task remains
`EMBEDDING/RUNNING`; only Task 9 may set `READY` and switch
`document.current_version_id`. A `NEEDS_REVIEW` parse stops before chunking.
Chunking/persistence failures roll back partial writes and durably mark the
document, version, and task `FAILED` at the correct stage with sanitized error
codes/messages. Re-entering a version already at `EMBEDDING` or later is a
no-op until Task 9 exists.

- [ ] **Step 6: Verify retries do not duplicate chunks**

~~~powershell
Set-Location backend
uv run alembic upgrade head
uv run pytest tests/unit/ingestion/test_chunker.py tests/integration/ingestion/test_pipeline_ready.py -v
~~~

Expected: running the same version twice produces the same chunk count, row
IDs, and unique chunk indexes without duplicate rows; the task ends at
`EMBEDDING/RUNNING`, not `READY`.

- [ ] **Step 7: Prepare the user commit checkpoint**

~~~powershell
git add backend/src/research_agent/ingestion backend/migrations/versions/0003_chunks.py backend/tests/unit/ingestion backend/tests/integration/ingestion
git commit -m "feat: create structure-aware paper chunks"
~~~

---

### Task 8: Build the paper library, upload, and durable progress UI

**Files:**
- Create: frontend/src/lib/api.ts
- Create: frontend/src/features/papers/types.ts
- Create: frontend/src/features/papers/api.ts
- Create: frontend/src/features/papers/upload-panel.tsx
- Create: frontend/src/features/papers/task-progress.tsx
- Create: frontend/src/app/papers/page.tsx
- Modify: backend/src/research_agent/papers/routes.py
- Create: frontend/src/features/papers/upload-panel.test.tsx
- Create: frontend/e2e/paper-upload.spec.ts

**Interfaces:**
- Consumes: POST /api/v1/documents, GET /api/v1/documents, GET /api/v1/tasks/{task_id}
- Produces: UploadPanel, TaskProgress, papers page, list and task response schemas

- [ ] **Step 1: Write the failing upload component test**

Create frontend/src/features/papers/upload-panel.test.tsx:

~~~tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { UploadPanel } from "./upload-panel";

describe("UploadPanel", () => {
  it("rejects a non-PDF before calling the API", async () => {
    const upload = vi.fn();
    render(<UploadPanel upload={upload} />);
    const input = screen.getByLabelText("选择 PDF");
    const file = new File(["text"], "paper.txt", { type: "text/plain" });

    fireEvent.change(input, { target: { files: [file] } });

    expect(await screen.findByText("仅支持 PDF 文件")).toBeVisible();
    expect(upload).not.toHaveBeenCalled();
  });
});
~~~

- [ ] **Step 2: Run the component test and verify the missing component**

~~~powershell
Set-Location frontend
pnpm add -D vitest jsdom @testing-library/react @testing-library/jest-dom
pnpm vitest run src/features/papers/upload-panel.test.tsx
~~~

Expected: FAIL because UploadPanel does not exist.

- [ ] **Step 3: Implement typed paper API calls**

Define:

~~~typescript
export type UploadReceipt = {
  documentId: string;
  versionId: string;
  taskId: string;
  duplicate: boolean;
};

export type TaskState = {
  id: string;
  stage: string;
  status: "QUEUED" | "RUNNING" | "READY" | "FAILED" | "CANCELLED";
  progressCurrent: number;
  progressTotal: number;
  errorCode: string | null;
  errorMessage: string | null;
};
~~~

api.ts must throw a typed ApiProblem for non-2xx responses and must never expose backend secrets.

- [ ] **Step 4: Implement upload and progress components**

UploadPanel requirements:

- accept only application/pdf;
- show file name and size;
- disable submit while uploading;
- display duplicate notice without creating a second progress card;
- start TaskProgress after a 202 response.

TaskProgress polls PostgreSQL-backed GET /tasks/{id} every two seconds and stops on READY, FAILED, or CANCELLED. It displays stage, fraction, retry action, and persistent error message. Redis progress caching is not required.

- [ ] **Step 5: Add backend list and task endpoints**

Implement:

~~~text
GET /api/v1/documents?limit=50&cursor=<opaque>
GET /api/v1/documents/{document_id}
GET /api/v1/tasks/{task_id}
POST /api/v1/tasks/{task_id}/retry
~~~

Retry is allowed only for FAILED tasks with retryable error codes and creates a new outbox event without resetting durable history.

- [ ] **Step 6: Run frontend and API contract tests**

~~~powershell
Set-Location frontend
pnpm vitest run
pnpm lint
pnpm tsc --noEmit
Set-Location ..\backend
uv run pytest tests/contract tests/integration/papers -v
~~~

Expected: component, schema, and API tests pass.

- [ ] **Step 7: Run the first vertical smoke test**

~~~powershell
docker compose -f infra/compose.yaml up -d --build
pnpm --dir frontend exec playwright test e2e/paper-upload.spec.ts
~~~

Expected: upload returns a task ID, progress survives a browser refresh, and a deliberately invalid PDF shows a durable failure.

- [ ] **Step 8: Ingestion UI durability review**

Verify before continuing:

~~~powershell
docker compose -f infra/compose.yaml restart api worker
pnpm --dir frontend exec playwright test e2e/paper-upload.spec.ts
~~~

Expected: the previously uploaded paper and task state still appear after restart.

- [ ] **Step 9: Prepare the user commit checkpoint**

~~~powershell
git add frontend backend/src/research_agent/papers/routes.py backend/tests/contract
git commit -m "feat: show paper upload and processing progress"
~~~

---

### Task 9: Add versioned embedding spaces and complete READY transitions

**Files:**
- Modify: backend/pyproject.toml
- Modify: backend/src/research_agent/config.py
- Create: backend/src/research_agent/providers/openai_embeddings.py
- Create: backend/src/research_agent/retrieval/models.py
- Create: backend/migrations/versions/0004_embeddings.py
- Modify: backend/src/research_agent/ingestion/pipeline.py
- Create: backend/tests/unit/providers/test_openai_embeddings.py
- Create: backend/tests/integration/ingestion/test_embedding_stage.py

**Interfaces:**
- Consumes: EmbeddingProvider, document_chunk rows, TaskRepository
- Produces: EmbeddingSpace, ChunkEmbeddingModel, OpenAICompatibleEmbeddingProvider, IngestionPipeline.embed_and_activate

- [ ] **Step 1: Add LangChain OpenAI integration**

~~~powershell
Set-Location backend
uv add langchain-openai
~~~

Add settings:

~~~python
embedding_base_url: str = "https://api.openai.com/v1"
embedding_api_key: str = ""
embedding_model: str = "text-embedding-3-small"
embedding_dimension: int = 1536
embedding_batch_size: int = 32
~~~

- [ ] **Step 2: Write failing embedding adapter tests**

Create backend/tests/unit/providers/test_openai_embeddings.py:

~~~python
from unittest.mock import AsyncMock

from research_agent.providers.openai_embeddings import OpenAICompatibleEmbeddingProvider


async def test_adapter_rejects_wrong_dimension() -> None:
    client = AsyncMock()
    client.aembed_documents.return_value = [[0.1, 0.2]]
    provider = OpenAICompatibleEmbeddingProvider(
        client=client,
        model_id="model",
        dimension=3,
    )

    try:
        await provider.embed_documents(["text"])
    except ValueError as error:
        assert "dimension" in str(error)
    else:
        raise AssertionError("dimension mismatch must fail")
~~~

- [ ] **Step 3: Run the adapter test and confirm failure**

~~~powershell
Set-Location backend
uv run pytest tests/unit/providers/test_openai_embeddings.py -v
~~~

Expected: FAIL because providers.openai_embeddings does not exist.

- [ ] **Step 4: Implement the LangChain adapter without leaking LangChain types**

Create:

~~~python
class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        client: OpenAIEmbeddings,
        model_id: str,
        dimension: int,
    ) -> None:
        self._client = client
        self.model_id = model_id
        self.dimension = dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = await self._client.aembed_documents(texts)
        self._validate(vectors)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        vector = await self._client.aembed_query(text)
        self._validate([vector])
        return vector

    def _validate(self, vectors: list[list[float]]) -> None:
        if any(len(vector) != self.dimension for vector in vectors):
            raise ValueError("embedding dimension does not match active space")
~~~

- [ ] **Step 5: Create embedding tables and HNSW index**

Migration 0004:

~~~sql
CREATE TABLE embedding_space (
    id uuid PRIMARY KEY,
    model_id text NOT NULL,
    dimension integer NOT NULL,
    pipeline_version text NOT NULL,
    active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (model_id, dimension, pipeline_version)
);

CREATE TABLE chunk_embedding (
    chunk_id uuid NOT NULL REFERENCES document_chunk(id) ON DELETE CASCADE,
    embedding_space_id uuid NOT NULL REFERENCES embedding_space(id) ON DELETE CASCADE,
    embedding vector(1536) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, embedding_space_id)
);

CREATE INDEX ix_chunk_embedding_hnsw
ON chunk_embedding USING hnsw (embedding vector_cosine_ops);
~~~

The first MVP migration fixes the active dimension at 1536. A different dimension requires a new migration and shadow table; configuration startup fails if it disagrees with the schema.

- [ ] **Step 6: Batch embeddings idempotently**

IngestionPipeline.embed_and_activate must:

1. load chunks missing the active embedding space;
2. call embed_documents in batches of 32;
3. upsert by chunk_id and embedding_space_id;
4. verify every non-reference chunk has an embedding;
5. set version READY and atomically switch document.current_version_id;
6. set task READY.

Use one transaction for the final three state changes, not for external API calls.

- [ ] **Step 7: Test restart and duplicate execution**

~~~powershell
Set-Location backend
uv run alembic upgrade head
uv run pytest tests/unit/providers/test_openai_embeddings.py tests/integration/ingestion/test_embedding_stage.py -v
~~~

Expected: two pipeline executions produce one embedding per chunk and end with one active READY version.

- [ ] **Step 8: Checkpoint 2 review**

~~~powershell
docker compose -f infra/compose.yaml up -d --build
docker compose -f infra/compose.yaml restart api worker
~~~

Expected: a valid test PDF progresses from QUEUED to READY, its Markdown and JSON exist in MinIO, and chunks plus embeddings exist after restart.

- [ ] **Step 9: Prepare the user commit checkpoint**

~~~powershell
git add backend/pyproject.toml backend/uv.lock backend/src/research_agent/config.py backend/src/research_agent/providers/openai_embeddings.py backend/src/research_agent/retrieval backend/src/research_agent/ingestion/pipeline.py backend/migrations/versions/0004_embeddings.py backend/tests
git commit -m "feat: embed and activate paper versions"
~~~

---

### Task 10: Implement scoped lexical and vector retrieval

**Files:**
- Create: backend/src/research_agent/retrieval/domain.py
- Create: backend/src/research_agent/retrieval/repository.py
- Create: backend/tests/unit/retrieval/test_scope.py
- Create: backend/tests/integration/retrieval/test_postgres_retrieval.py

**Interfaces:**
- Consumes: EmbeddingProvider, READY document versions, active embedding space
- Produces: RetrievalScope, RetrievalQuery, RetrievalCandidate, SqlHybridRetrievalRepository.lexical_search, vector_search

- [ ] **Step 1: Write failing retrieval-scope tests**

Create backend/tests/unit/retrieval/test_scope.py:

~~~python
from uuid import uuid4

from research_agent.retrieval.domain import RetrievalScope


def test_scope_rejects_duplicate_document_ids() -> None:
    document_id = uuid4()
    try:
        RetrievalScope(document_ids=(document_id, document_id))
    except ValueError as error:
        assert "duplicates" in str(error)
    else:
        raise AssertionError("duplicate document scope must fail")
~~~

- [ ] **Step 2: Confirm the domain test fails**

~~~powershell
Set-Location backend
uv run pytest tests/unit/retrieval/test_scope.py -v
~~~

Expected: FAIL because retrieval.domain does not exist.

- [ ] **Step 3: Define retrieval domain types**

Create:

~~~python
@dataclass(frozen=True, slots=True)
class RetrievalScope:
    document_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError("document scope contains duplicates")


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    original: str
    lexical_queries: tuple[str, ...]
    semantic_query: str
    scope: RetrievalScope


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    title: str
    section_path: tuple[str, ...]
    content: str
    page_start: int
    page_end: int
    lexical_rank: int | None = None
    vector_rank: int | None = None
    fused_score: float = 0.0
    rerank_score: float = 0.0
~~~

- [ ] **Step 4: Implement English, Chinese, and vector SQL**

lexical_search executes both vectors and keeps the better rank:

~~~sql
WITH lexical AS (
    SELECT
        c.id,
        row_number() OVER (
            ORDER BY GREATEST(
                ts_rank_cd(c.search_vector_en, websearch_to_tsquery('english', :query)),
                ts_rank_cd(c.search_vector_zh, plainto_tsquery('simple', :segmented_query))
            ) DESC
        ) AS rank
    FROM document_chunk c
    JOIN document_version v ON v.id = c.document_version_id
    WHERE v.status = 'READY'
      AND (
        c.search_vector_en @@ websearch_to_tsquery('english', :query)
        OR c.search_vector_zh @@ plainto_tsquery('simple', :segmented_query)
      )
)
SELECT * FROM lexical ORDER BY rank LIMIT :limit;
~~~

vector_search:

~~~sql
SELECT
    c.id,
    row_number() OVER (ORDER BY e.embedding <=> :query_vector) AS rank
FROM chunk_embedding e
JOIN document_chunk c ON c.id = e.chunk_id
JOIN document_version v ON v.id = c.document_version_id
JOIN embedding_space s ON s.id = e.embedding_space_id
WHERE v.status = 'READY'
  AND s.active = true
ORDER BY e.embedding <=> :query_vector
LIMIT :limit;
~~~

Append document or collection predicates from typed scope parameters only. Never interpolate model text into SQL.

- [ ] **Step 5: Test scope and READY filtering**

~~~powershell
Set-Location backend
uv run pytest tests/unit/retrieval/test_scope.py tests/integration/retrieval/test_postgres_retrieval.py -v
~~~

Expected: non-READY versions never appear; document scope excludes all other papers; Chinese segmented terms and English queries both return labelled fixtures.

- [ ] **Step 6: Prepare the user commit checkpoint**

~~~powershell
git add backend/src/research_agent/retrieval backend/tests/unit/retrieval backend/tests/integration/retrieval
git commit -m "feat: retrieve scoped lexical and vector candidates"
~~~

---

### Task 11: Fuse, rerank, and evaluate retrieval

**Files:**
- Modify: backend/pyproject.toml
- Create: backend/src/research_agent/retrieval/rrf.py
- Create: backend/src/research_agent/providers/cohere_reranker.py
- Create: backend/src/research_agent/retrieval/service.py
- Create: backend/src/research_agent/evaluation/domain.py
- Create: backend/src/research_agent/evaluation/dataset.py
- Create: backend/src/research_agent/evaluation/retrieval_eval.py
- Create: backend/src/research_agent/evaluation/cli.py
- Create: backend/tests/unit/retrieval/test_rrf.py
- Create: backend/tests/unit/retrieval/test_service.py
- Create: backend/tests/unit/evaluation/test_retrieval_metrics.py
- Create: evaluation/datasets/synthetic_retrieval.jsonl

**Interfaces:**
- Consumes: SqlHybridRetrievalRepository, EmbeddingProvider, RerankerProvider
- Produces: reciprocal_rank_fusion, RetrievalService.retrieve, RetrievalMetrics, evaluation CLI

- [ ] **Step 1: Write failing RRF tests**

Create backend/tests/unit/retrieval/test_rrf.py:

~~~python
from research_agent.retrieval.rrf import reciprocal_rank_fusion


def test_rrf_rewards_items_found_by_both_retrievers() -> None:
    fused = reciprocal_rank_fusion(
        lexical=["a", "b"],
        vector=["b", "c"],
        k=60,
    )

    assert fused[0][0] == "b"
    assert fused[0][1] == 1 / 62 + 1 / 61
~~~

- [ ] **Step 2: Run RRF tests and confirm failure**

~~~powershell
Set-Location backend
uv run pytest tests/unit/retrieval/test_rrf.py -v
~~~

Expected: FAIL because retrieval.rrf does not exist.

- [ ] **Step 3: Implement RRF exactly**

~~~python
def reciprocal_rank_fusion(
    *,
    lexical: list[str],
    vector: list[str],
    k: int = 60,
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in (lexical, vector):
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
~~~

- [ ] **Step 4: Add a concrete reranker adapter**

~~~powershell
Set-Location backend
uv add cohere
~~~

CohereRerankerProvider converts project RerankItem values to the SDK request and converts results back. It validates every returned index, preserves candidate IDs, records model_id, and raises retryable AppError for rate limits or timeouts.

- [ ] **Step 5: Implement the retrieval service**

~~~python
class RetrievalService:
    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        lexical_limit: int = 40,
        vector_limit: int = 40,
        fused_limit: int = 25,
        final_limit: int = 10,
    ) -> list[RetrievalCandidate]:
        query_vector = await self._embeddings.embed_query(query.semantic_query)
        lexical, vector = await asyncio.gather(
            self._repository.lexical_search(query, limit=lexical_limit),
            self._repository.vector_search(query, query_vector, limit=vector_limit),
        )
        fused = merge_candidates(
            reciprocal_rank_fusion(
                lexical=[str(item.chunk_id) for item in lexical],
                vector=[str(item.chunk_id) for item in vector],
            ),
            lexical,
            vector,
        )[:fused_limit]
        reranked = await self._reranker.rerank(
            query=query.original,
            items=[RerankItem(id=str(item.chunk_id), text=item.content) for item in fused],
            top_n=final_limit,
        )
        return apply_rerank_scores(fused, reranked)
~~~

- [ ] **Step 6: Implement retrieval metrics**

For each evaluation case calculate:

~~~python
recall_at_5 = len(relevant_ids & set(retrieved_ids[:5])) / len(relevant_ids)
recall_at_10 = len(relevant_ids & set(retrieved_ids[:10])) / len(relevant_ids)
reciprocal_rank = next(
    (1.0 / rank for rank, item in enumerate(retrieved_ids, 1) if item in relevant_ids),
    0.0,
)
~~~

The CLI command is:

~~~powershell
Set-Location backend
uv run python -m research_agent.evaluation.cli retrieval --dataset ../evaluation/datasets/synthetic_retrieval.jsonl
~~~

- [ ] **Step 7: Run retrieval tests and synthetic evaluation**

~~~powershell
Set-Location backend
uv run pytest tests/unit/retrieval tests/unit/evaluation tests/integration/retrieval -v
uv run python -m research_agent.evaluation.cli retrieval --dataset ../evaluation/datasets/synthetic_retrieval.jsonl
~~~

Expected: tests pass and the synthetic dataset reports Recall@10 equal to 1.0.

- [ ] **Step 8: Checkpoint 3 review**

Run retrieval on at least five user-selected ReID papers and inspect the top ten chunks for ten questions. Record false positives by query and content type before changing chunk or ranking parameters.

- [ ] **Step 9: Prepare the user commit checkpoint**

~~~powershell
git add backend/pyproject.toml backend/uv.lock backend/src/research_agent/retrieval backend/src/research_agent/providers/cohere_reranker.py backend/src/research_agent/evaluation backend/tests evaluation/datasets/synthetic_retrieval.jsonl
git commit -m "feat: fuse rerank and evaluate retrieval"
~~~

---

### Task 12: Persist chat sessions and build the explicit LangGraph query workflow

**Files:**
- Modify: backend/pyproject.toml
- Create: backend/src/research_agent/chat/domain.py
- Create: backend/src/research_agent/chat/models.py
- Create: backend/src/research_agent/chat/repository.py
- Create: backend/src/research_agent/chat/graph.py
- Create: backend/src/research_agent/chat/service.py
- Create: backend/migrations/versions/0005_chat.py
- Create: backend/tests/unit/chat/test_graph.py
- Create: backend/tests/integration/chat/test_chat_persistence.py

**Interfaces:**
- Consumes: LLMProvider, RetrievalService, PostgreSQL
- Produces: QueryPlan, ChatGraphState, ChatService.ask, chat_session, chat_message, chat_run, chat_run_event

- [ ] **Step 1: Add LangChain, LangGraph, and PostgreSQL checkpoint packages**

~~~powershell
Set-Location backend
uv add langchain langgraph langgraph-checkpoint-postgres
~~~

- [ ] **Step 2: Write the failing graph-routing test**

Create backend/tests/unit/chat/test_graph.py:

~~~python
from research_agent.chat.domain import QueryPlan
from research_agent.chat.graph import route_after_evidence_check


def test_insufficient_evidence_routes_to_clarification() -> None:
    state = {
        "plan": QueryPlan(
            intent="compare_methods",
            original_query="compare",
            lexical_queries=("compare",),
            semantic_query="compare",
        ),
        "evidence": [],
        "evidence_sufficient": False,
    }

    assert route_after_evidence_check(state) == "clarify"
~~~

- [ ] **Step 3: Run the graph test and confirm failure**

~~~powershell
Set-Location backend
uv run pytest tests/unit/chat/test_graph.py -v
~~~

Expected: FAIL because chat.domain and chat.graph do not exist.

- [ ] **Step 4: Create chat persistence**

Migration 0005 creates:

~~~sql
CREATE TABLE chat_session (
    id uuid PRIMARY KEY,
    title text NOT NULL,
    scope jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE chat_message (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
    role text NOT NULL,
    content jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE chat_run (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
    status text NOT NULL,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
CREATE TABLE chat_run_event (
    run_id uuid NOT NULL REFERENCES chat_run(id) ON DELETE CASCADE,
    sequence integer NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, sequence)
);
~~~

- [ ] **Step 5: Define project-owned graph state**

~~~python
class QueryPlan(BaseModel):
    intent: Literal["fact", "summary", "compare_methods", "background"]
    original_query: str
    lexical_queries: tuple[str, ...]
    semantic_query: str


class ChatGraphState(TypedDict, total=False):
    session_id: UUID
    run_id: UUID
    question: str
    scope: RetrievalScope
    plan: QueryPlan
    evidence: list[RetrievalCandidate]
    evidence_sufficient: bool
    draft: AnswerDraft
    verified_answer: VerifiedAnswer
    warnings: list[str]
~~~

No LangChain Document, AIMessage, Celery result, or MinIO response is stored in these domain values.

- [ ] **Step 6: Build explicit graph nodes**

Compile:

~~~text
START
→ plan_query
→ retrieve
→ check_evidence
→ generate_draft OR clarify
→ verify_citations
→ persist_answer
→ END
~~~

No generic ReAct loop is allowed. Each node receives dependencies through a GraphDependencies object. Query planning uses LLMProvider.generate_structured with QueryPlan.

- [ ] **Step 7: Add PostgreSQL checkpointing**

Create one AsyncPostgresSaver from the application connection string, run its setup in migration/bootstrap, and compile the graph with the saver. Use session_id as thread_id and run_id in configurable metadata.

- [ ] **Step 8: Run graph and persistence tests**

~~~powershell
Set-Location backend
uv run alembic upgrade head
uv run pytest tests/unit/chat/test_graph.py tests/integration/chat/test_chat_persistence.py -v
~~~

Expected: insufficient evidence routes to clarification; a successful run survives process restart and retains messages plus graph checkpoint.

- [ ] **Step 9: Prepare the user commit checkpoint**

~~~powershell
git add backend/pyproject.toml backend/uv.lock backend/src/research_agent/chat backend/migrations/versions/0005_chat.py backend/tests/unit/chat backend/tests/integration/chat
git commit -m "feat: orchestrate persistent paper chat"
~~~

---

### Task 13: Generate labelled answer blocks and verify every citation

**Files:**
- Create: backend/src/research_agent/chat/citations.py
- Modify: backend/src/research_agent/chat/domain.py
- Modify: backend/src/research_agent/chat/graph.py
- Create: backend/tests/unit/chat/test_citations.py
- Create: backend/tests/unit/chat/test_answer_labels.py

**Interfaces:**
- Consumes: AnswerDraft, RetrievalCandidate
- Produces: PaperEvidenceBlock, ModelKnowledgeBlock, CitationDraft, VerifiedCitation, VerifiedAnswer, CitationVerifier.verify

- [ ] **Step 1: Write failing citation tests**

Create backend/tests/unit/chat/test_citations.py:

~~~python
from uuid import uuid4

from research_agent.chat.citations import CitationVerifier
from research_agent.chat.domain import CitationDraft


def test_quote_must_exist_in_retrieved_chunk(candidate_factory) -> None:
    candidate = candidate_factory(content="The method uses visible regions.", page_start=4)
    verifier = CitationVerifier()

    result = verifier.verify(
        citations=[
            CitationDraft(
                chunk_id=candidate.chunk_id,
                quote="a sentence that is absent",
            )
        ],
        candidates=[candidate],
    )

    assert result.valid == []
    assert result.invalid[0].reason == "QUOTE_NOT_FOUND"
~~~

- [ ] **Step 2: Run citation tests and verify failure**

~~~powershell
Set-Location backend
uv run pytest tests/unit/chat/test_citations.py -v
~~~

Expected: FAIL because chat.citations does not exist.

- [ ] **Step 3: Define answer types**

~~~python
class CitationDraft(BaseModel):
    chunk_id: UUID
    quote: str


class PaperEvidenceBlock(BaseModel):
    type: Literal["paper_evidence"] = "paper_evidence"
    text: str
    citations: list[CitationDraft]


class ModelKnowledgeBlock(BaseModel):
    type: Literal["model_knowledge"] = "model_knowledge"
    text: str


class AnswerDraft(BaseModel):
    blocks: list[PaperEvidenceBlock | ModelKnowledgeBlock]


class VerifiedCitation(BaseModel):
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    title: str
    section_path: tuple[str, ...]
    page_start: int
    page_end: int
    quote: str
~~~

- [ ] **Step 4: Implement deterministic validation**

CitationVerifier verifies:

1. cited chunk is in the final retrieved set;
2. cited document version is the candidate version;
3. normalized quote is a substring of normalized chunk content;
4. page_start and page_end are positive;
5. every paper_evidence block has at least one valid citation;
6. model_knowledge blocks have no citations.

Normalization collapses whitespace and applies Unicode NFKC but does not paraphrase.

- [ ] **Step 5: Add one controlled repair pass**

If any paper evidence block fails, call LLMProvider once with:

~~~text
Remove or rewrite unsupported claims. Use only the supplied chunk IDs and verbatim
quotes. Do not convert unsupported content into paper evidence. General background
may appear only in a model_knowledge block.
~~~

Verify again. If the second pass fails, persist a warning and return evidence-insufficient rather than unverified claims.

- [ ] **Step 6: Run answer and citation tests**

~~~powershell
Set-Location backend
uv run pytest tests/unit/chat/test_citations.py tests/unit/chat/test_answer_labels.py tests/unit/chat/test_graph.py -v
~~~

Expected: invented quotes fail, stale versions fail, model knowledge is labelled, and a verified answer contains only valid citations.

- [ ] **Step 7: Prepare the user commit checkpoint**

~~~powershell
git add backend/src/research_agent/chat backend/tests/unit/chat
git commit -m "feat: verify labelled cited answers"
~~~

---

### Task 14: Stream verified answers to the Next.js chat UI

**Files:**
- Create: backend/src/research_agent/chat/stream.py
- Create: backend/src/research_agent/chat/routes.py
- Modify: backend/src/research_agent/main.py
- Create: backend/tests/contract/chat/test_stream_protocol.py
- Create: frontend/src/features/chat/types.ts
- Create: frontend/src/features/chat/chat-panel.tsx
- Create: frontend/src/features/chat/citation-panel.tsx
- Create: frontend/src/features/chat/use-research-chat.ts
- Create: frontend/src/app/chat/[sessionId]/page.tsx
- Create: frontend/src/features/chat/chat-panel.test.tsx
- Create: frontend/e2e/cited-chat.spec.ts

**Interfaces:**
- Consumes: ChatService.ask, chat_run_event, Vercel AI SDK UI stream protocol
- Produces: POST /api/v1/chat/sessions, POST /api/v1/chat/sessions/{id}/messages, GET /api/v1/chat/runs/{id}/stream, ChatPanel, CitationPanel

- [ ] **Step 1: Write the failing stream contract test**

Create backend/tests/contract/chat/test_stream_protocol.py:

~~~python
def test_verified_answer_stream_has_required_parts(client, seeded_verified_run) -> None:
    response = client.get(
        "/api/v1/chat/runs/" + str(seeded_verified_run.id) + "/stream",
        headers={"Accept": "text/event-stream"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    assert '"type":"start"' in response.text
    assert '"type":"text-start"' in response.text
    assert '"type":"text-delta"' in response.text
    assert '"type":"data-citations"' in response.text
    assert "data: [DONE]" in response.text
~~~

- [ ] **Step 2: Run the contract test and confirm route failure**

~~~powershell
Set-Location backend
uv run pytest tests/contract/chat/test_stream_protocol.py -v
~~~

Expected: FAIL because the stream route does not exist.

- [ ] **Step 3: Implement persisted SSE events**

Use this order:

~~~text
start
data-workflow for plan
data-workflow for retrieval
data-workflow for rerank
data-workflow for citation verification
text-start
one or more text-delta parts from the already verified answer
text-end
data-citations
finish
[DONE]
~~~

Every database event has a monotonically increasing sequence. GET stream accepts Last-Event-ID and emits only later events. Keepalive comments are sent every 15 seconds. Internal model reasoning is never emitted.

- [ ] **Step 4: Implement message submission**

POST messages:

1. validates question and scope;
2. creates user message and chat_run in one transaction;
3. starts ChatService in an application task;
4. immediately returns the SSE stream when the client accepts text/event-stream;
5. persists every emitted event before sending it.

The verified answer may be split into display-sized text deltas after validation; the raw unverified model stream is never sent.

- [ ] **Step 5: Write the failing chat component test**

Create frontend/src/features/chat/chat-panel.test.tsx:

~~~tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ChatPanel } from "./chat-panel";

describe("ChatPanel", () => {
  it("renders paper evidence separately from model knowledge", () => {
    render(
      <ChatPanel
        messages={[
          {
            id: "m1",
            blocks: [
              { type: "paper_evidence", text: "Paper claim", citationIds: ["c1"] },
              { type: "model_knowledge", text: "Background" },
            ],
          },
        ]}
      />,
    );

    expect(screen.getByText("论文证据")).toBeVisible();
    expect(screen.getByText("模型补充")).toBeVisible();
  });
});
~~~

- [ ] **Step 6: Implement chat and citation UI**

The UI must:

- show high-level workflow status before answer text;
- render paper_evidence and model_knowledge with different labels;
- open CitationPanel with title, section, page range, and verbatim quote;
- link to the PDF route with page query parameter;
- reconnect with Last-Event-ID;
- show evidence-insufficient and provider-failure states explicitly.

- [ ] **Step 7: Run contract, component, and browser tests**

~~~powershell
Set-Location backend
uv run pytest tests/contract/chat tests/unit/chat tests/integration/chat -v
Set-Location ..\frontend
pnpm vitest run src/features/chat/chat-panel.test.tsx
pnpm lint
pnpm tsc --noEmit
pnpm exec playwright test e2e/cited-chat.spec.ts
~~~

Expected: the answer appears only after citation verification; clicking a citation displays the correct paper and page; model knowledge is visibly separate.

- [ ] **Step 8: Checkpoint 4 review**

Ask ten questions across at least five papers. Reject the checkpoint if any paper claim lacks a clickable quote or if model background is displayed as paper evidence.

- [ ] **Step 9: Prepare the user commit checkpoint**

~~~powershell
git add backend/src/research_agent/chat backend/src/research_agent/main.py backend/tests/contract/chat frontend/src/features/chat frontend/src/app/chat frontend/e2e/cited-chat.spec.ts
git commit -m "feat: stream verified cited paper answers"
~~~

---

### Task 15: Generate optional, editable, versioned paper notes

**Files:**
- Create: backend/src/research_agent/notes/domain.py
- Create: backend/src/research_agent/notes/models.py
- Create: backend/src/research_agent/notes/repository.py
- Create: backend/src/research_agent/notes/service.py
- Create: backend/src/research_agent/notes/celery_tasks.py
- Create: backend/src/research_agent/notes/routes.py
- Create: backend/migrations/versions/0006_notes.py
- Create: backend/tests/unit/notes/test_note_generation.py
- Create: backend/tests/integration/notes/test_note_revisions.py
- Create: frontend/src/features/notes/note-editor.tsx
- Create: frontend/src/features/notes/note-diff.tsx
- Create: frontend/src/app/papers/[documentId]/notes/page.tsx
- Create: frontend/src/features/notes/note-editor.test.tsx

**Interfaces:**
- Consumes: LLMProvider, RetrievalService, verified paper evidence, Celery
- Produces: PaperNoteContent, NoteField, NoteEvidence, NoteService.request_generation, GET and PUT note APIs, NoteEditor

- [ ] **Step 1: Write failing note-generation tests**

Create backend/tests/unit/notes/test_note_generation.py:

~~~python
from research_agent.notes.domain import NoteField, PaperNoteContent
from research_agent.notes.service import NoteService


async def test_generation_is_explicit_and_evidence_backed(
    fake_llm,
    fake_retrieval,
    fake_note_repository,
) -> None:
    fake_llm.set_response("paper_note", PaperNoteContent(
        fields=[
            NoteField(
                key="innovations",
                title="主要创新点",
                markdown="Uses visible-region matching.",
                evidence_chunk_ids=[fake_retrieval.candidate.chunk_id],
                status="AI_GENERATED",
            )
        ]
    ))
    service = NoteService(fake_llm, fake_retrieval, fake_note_repository)

    revision = await service.generate_draft(fake_retrieval.document_id)

    assert revision.content.fields[0].status == "AI_GENERATED"
    assert revision.content.fields[0].evidence_chunk_ids
~~~

- [ ] **Step 2: Run the note test and confirm failure**

~~~powershell
Set-Location backend
uv run pytest tests/unit/notes/test_note_generation.py -v
~~~

Expected: FAIL because notes.domain and notes.service do not exist.

- [ ] **Step 3: Create note persistence**

Migration 0006:

~~~sql
CREATE TABLE paper_note (
    id uuid PRIMARY KEY,
    document_id uuid NOT NULL UNIQUE REFERENCES document(id) ON DELETE CASCADE,
    current_revision_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE note_revision (
    id uuid PRIMARY KEY,
    note_id uuid NOT NULL REFERENCES paper_note(id) ON DELETE CASCADE,
    revision_number integer NOT NULL,
    source text NOT NULL,
    content jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (note_id, revision_number)
);
CREATE TABLE note_evidence (
    revision_id uuid NOT NULL REFERENCES note_revision(id) ON DELETE CASCADE,
    field_key text NOT NULL,
    chunk_id uuid NOT NULL REFERENCES document_chunk(id),
    quote text NOT NULL,
    PRIMARY KEY (revision_id, field_key, chunk_id)
);
~~~

- [ ] **Step 4: Implement the fixed MVP template**

PaperNoteContent contains these keys:

~~~text
research_question
paper_positioning
method_overview
innovations
datasets_and_metrics
experimental_setup
main_results
ablations
limitations
related_papers
research_inspiration
user_comments
~~~

AI-generated factual fields require at least one valid evidence chunk. user_comments is never generated by AI.

- [ ] **Step 5: Implement explicit generation and safe regeneration**

Endpoints:

~~~text
POST /api/v1/documents/{document_id}/notes/generate
GET  /api/v1/documents/{document_id}/notes
PUT  /api/v1/notes/{note_id}
GET  /api/v1/notes/{note_id}/revisions
POST /api/v1/notes/{note_id}/regenerate
~~~

Rules:

- upload and ingestion never call note generation;
- generate creates a Celery task only after the user request;
- user save creates a USER_EDIT revision;
- regenerate creates an AI_DRAFT revision;
- regenerate never changes current_revision_id until the user accepts it;
- field evidence uses the same citation verifier as chat.

- [ ] **Step 6: Implement the editor and revision diff**

NoteEditor renders each field, evidence links, and AI_GENERATED or USER_CONFIRMED status. NoteDiff compares the current revision to a generated draft field by field and offers accept field, reject field, accept all, and discard draft.

- [ ] **Step 7: Run note tests**

~~~powershell
Set-Location backend
uv run alembic upgrade head
uv run pytest tests/unit/notes tests/integration/notes -v
Set-Location ..\frontend
pnpm vitest run src/features/notes/note-editor.test.tsx
pnpm lint
pnpm tsc --noEmit
~~~

Expected: no note exists after upload; explicit generation creates a draft; regeneration preserves user edits until acceptance.

- [ ] **Step 8: Checkpoint 5 review**

Generate notes for three different papers, edit each, restart services, and confirm current revisions plus evidence links remain intact.

- [ ] **Step 9: Prepare the user commit checkpoint**

~~~powershell
git add backend/src/research_agent/notes backend/migrations/versions/0006_notes.py backend/tests frontend/src/features/notes frontend/src/app/papers
git commit -m "feat: generate optional versioned paper notes"
~~~

---

### Task 16: Add observability, model usage, and reproducible evaluation runs

**Files:**
- Modify: backend/pyproject.toml
- Create: backend/src/research_agent/shared/logging.py
- Create: backend/src/research_agent/shared/observability.py
- Create: backend/src/research_agent/evaluation/answer_eval.py
- Modify: backend/src/research_agent/evaluation/cli.py
- Create: backend/migrations/versions/0007_usage_evaluation.py
- Create: backend/tests/unit/evaluation/test_answer_metrics.py
- Create: backend/tests/integration/evaluation/test_run_persistence.py
- Create: evaluation/README.md
- Create: evaluation/datasets/synthetic_mvp.jsonl

**Interfaces:**
- Consumes: request trace ID, model callbacks, RetrievalMetrics, VerifiedAnswer
- Produces: model_usage, evaluation_dataset, evaluation_case, evaluation_run, evaluation_score, structured logs, optional Langfuse traces

- [ ] **Step 1: Add observability dependencies**

~~~powershell
Set-Location backend
uv add structlog langfuse
~~~

- [ ] **Step 2: Write failing answer metric tests**

Create backend/tests/unit/evaluation/test_answer_metrics.py:

~~~python
from research_agent.evaluation.answer_eval import deterministic_answer_scores


def test_model_knowledge_label_score_fails_unlabelled_background() -> None:
    scores = deterministic_answer_scores(
        answer_blocks=[
            {"type": "paper_evidence", "text": "General background", "citations": []}
        ]
    )

    assert scores["model_knowledge_labelling"] == 0.0
    assert scores["citation_schema_valid"] == 0.0
~~~

- [ ] **Step 3: Run metrics test and verify failure**

~~~powershell
Set-Location backend
uv run pytest tests/unit/evaluation/test_answer_metrics.py -v
~~~

Expected: FAIL because evaluation.answer_eval does not exist.

- [ ] **Step 4: Add durable usage and evaluation schema**

Migration 0007 creates:

~~~sql
CREATE TABLE model_usage (
    id uuid PRIMARY KEY,
    trace_id uuid NOT NULL,
    operation text NOT NULL,
    provider text NOT NULL,
    model_id text NOT NULL,
    prompt_version text NOT NULL,
    input_tokens integer NOT NULL,
    output_tokens integer NOT NULL,
    latency_ms integer NOT NULL,
    estimated_cost numeric(12, 6) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE evaluation_dataset (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    version text NOT NULL,
    item_count integer NOT NULL,
    UNIQUE (name, version)
);
CREATE TABLE evaluation_case (
    id uuid PRIMARY KEY,
    dataset_id uuid NOT NULL REFERENCES evaluation_dataset(id) ON DELETE CASCADE,
    question text NOT NULL,
    language text NOT NULL,
    question_type text NOT NULL,
    relevant_chunk_ids jsonb NOT NULL
);
CREATE TABLE evaluation_run (
    id uuid PRIMARY KEY,
    dataset_id uuid NOT NULL REFERENCES evaluation_dataset(id),
    configuration jsonb NOT NULL,
    started_at timestamptz NOT NULL,
    completed_at timestamptz
);
CREATE TABLE evaluation_score (
    run_id uuid NOT NULL REFERENCES evaluation_run(id) ON DELETE CASCADE,
    case_id uuid NOT NULL REFERENCES evaluation_case(id) ON DELETE CASCADE,
    metric text NOT NULL,
    value double precision NOT NULL,
    details jsonb NOT NULL,
    PRIMARY KEY (run_id, case_id, metric)
);
~~~

- [ ] **Step 5: Propagate trace IDs and redact content**

Every HTTP request, Celery task, retrieval run, reranker request, and model call logs trace_id. Structured logs contain IDs, stages, counts, latency, models, and errors, but default redaction excludes full PDF text, full prompts, API keys, and MinIO secrets.

Langfuse is disabled unless LANGFUSE_ENABLED=true. When enabled, send metadata and sampled excerpts only; do not send full paper chunks by default.

- [ ] **Step 6: Implement reproducible evaluation commands**

~~~powershell
Set-Location backend
uv run python -m research_agent.evaluation.cli import --dataset ../evaluation/datasets/synthetic_mvp.jsonl
uv run python -m research_agent.evaluation.cli retrieval --dataset-name synthetic-mvp --version 1
uv run python -m research_agent.evaluation.cli answers --dataset-name synthetic-mvp --version 1
uv run python -m research_agent.evaluation.cli report --latest
~~~

synthetic_mvp.jsonl is a repository-owned fixture built from the test PDFs and contains answerable, no-answer, bilingual, and citation-trap cases. The user-annotated reid_mvp dataset is created only in Task 17.

Each run stores:

- chunking parameters;
- embedding and reranker model IDs;
- prompt versions;
- retrieval limits;
- Recall@5, Recall@10, MRR;
- citation schema and quote validity;
- human scores when entered;
- latency, tokens, and cost.

- [ ] **Step 7: Run observability and evaluation tests**

~~~powershell
Set-Location backend
uv run alembic upgrade head
uv run pytest tests/unit/evaluation tests/integration/evaluation -v
uv run ruff check src tests
uv run pyright
~~~

Expected: evaluation run is reproducible from stored configuration; logs contain trace IDs and do not contain seeded secret values.

- [ ] **Step 8: Prepare the user commit checkpoint**

~~~powershell
git add backend/pyproject.toml backend/uv.lock backend/src/research_agent/shared backend/src/research_agent/evaluation backend/migrations/versions/0007_usage_evaluation.py backend/tests evaluation/README.md evaluation/datasets/synthetic_mvp.jsonl
git commit -m "feat: trace and evaluate RAG quality"
~~~

---

### Task 17: Harden, back up, and pass the MVP release gate

**Files:**
- Modify: backend/src/research_agent/main.py
- Modify: backend/src/research_agent/config.py
- Modify: infra/compose.yaml
- Create: backend/tests/security/test_upload_limits.py
- Create: backend/tests/security/test_prompt_injection.py
- Create: backend/tests/e2e/test_full_mvp_flow.py
- Create: frontend/e2e/full-mvp.spec.ts
- Create: scripts/backup.ps1
- Create: scripts/restore.ps1
- Create: scripts/verify.ps1
- Modify: README.md
- Create: evaluation/datasets/reid_mvp.jsonl through the controlled annotation workflow

**Interfaces:**
- Consumes: all prior tasks and the user-supplied ReID paper corpus
- Produces: verified local release, backup and restore commands, final quality report

- [ ] **Step 1: Add explicit operational limits**

Settings:

~~~python
max_pdf_bytes: int = 50 * 1024 * 1024
max_pdf_pages: int = 200
allowed_origins: tuple[str, ...] = ("http://localhost:3000",)
local_access_token: str = ""
model_timeout_seconds: int = 120
paper_job_soft_time_limit_seconds: int = 1800
paper_job_hard_time_limit_seconds: int = 2100
~~~

Reject oversized files before MinIO upload. Run the worker as a non-root user with CPU and memory limits. Bind all host ports to 127.0.0.1.

Add GET /health/ready. It returns 200 only when PostgreSQL accepts a query, Redis answers PING, and the private MinIO bucket is reachable; otherwise it returns 503 with only dependency names and states, never credentials or connection strings.

- [ ] **Step 2: Write security regression tests**

test_upload_limits.py covers:

- fake PDF extension with invalid signature;
- valid signature with wrong MIME;
- file above 50 MiB;
- path traversal filename;
- object key contains only generated IDs.

test_prompt_injection.py seeds a chunk containing “ignore prior instructions” and asserts it remains quoted evidence data, never changes system instructions, scope, SQL, or tool permissions.

- [ ] **Step 3: Add backup and restore scripts**

backup.ps1 must create a PostgreSQL custom-format dump, mirror the private bucket, and save a count manifest without using PowerShell binary redirection:

~~~powershell
param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [string]$MinioUser = "research",
    [string]$MinioPassword = "research-secret"
)
$ErrorActionPreference = "Stop"
$resolved = [System.IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $resolved | Out-Null
docker compose -f infra/compose.yaml exec -T postgres pg_dump -U research -Fc -f /tmp/research.dump research
docker compose -f infra/compose.yaml cp postgres:/tmp/research.dump (Join-Path $resolved "research.dump")
docker compose -f infra/compose.yaml exec -T postgres rm -f /tmp/research.dump
$counts = docker compose -f infra/compose.yaml exec -T postgres psql -U research -d research -At -c "SELECT concat_ws(',', (SELECT count(*) FROM document), (SELECT count(*) FROM document_version), (SELECT count(*) FROM document_chunk), (SELECT count(*) FROM chat_session), (SELECT count(*) FROM chat_message), (SELECT count(*) FROM chat_run), (SELECT count(*) FROM paper_note), (SELECT count(*) FROM note_revision));"
Set-Content -LiteralPath (Join-Path $resolved "counts.csv") -Value "documents,versions,chunks,chats,messages,runs,notes,revisions`n$counts" -Encoding utf8
docker run --rm --entrypoint /bin/sh --mount "type=bind,source=$resolved,target=/backup" minio/mc -c 'mc alias set local http://host.docker.internal:9000 "$1" "$2" >/dev/null && mc mirror --overwrite local/research-agent /backup/minio' -- $MinioUser $MinioPassword
Copy-Item -LiteralPath ".env" -Destination (Join-Path $resolved "env.backup") -ErrorAction Stop
~~~

Protect env.backup as a secret-bearing file. restore.ps1 does not overwrite the active .env; it validates the backup, restores PostgreSQL into an empty database, mirrors objects back to the private bucket, runs migrations, and compares durable counts:

~~~powershell
param(
    [Parameter(Mandatory = $true)][string]$Source,
    [string]$MinioUser = "research",
    [string]$MinioPassword = "research-secret"
)
$ErrorActionPreference = "Stop"
$resolved = [System.IO.Path]::GetFullPath($Source)
$dump = Join-Path $resolved "research.dump"
$objects = Join-Path $resolved "minio"
if (-not (Test-Path -LiteralPath $dump -PathType Leaf)) { throw "research.dump is missing" }
if (-not (Test-Path -LiteralPath $objects -PathType Container)) { throw "MinIO backup is missing" }
docker compose -f infra/compose.yaml up -d postgres redis minio
docker compose -f infra/compose.yaml stop api worker
docker compose -f infra/compose.yaml cp $dump postgres:/tmp/research.dump
docker compose -f infra/compose.yaml exec -T postgres dropdb -U research --if-exists --force research
docker compose -f infra/compose.yaml exec -T postgres createdb -U research research
docker compose -f infra/compose.yaml exec -T postgres pg_restore -U research -d research /tmp/research.dump
docker compose -f infra/compose.yaml exec -T postgres rm -f /tmp/research.dump
docker run --rm --entrypoint /bin/sh --mount "type=bind,source=$resolved,target=/backup" minio/mc -c 'mc alias set local http://host.docker.internal:9000 "$1" "$2" >/dev/null && mc mirror --overwrite /backup/minio local/research-agent' -- $MinioUser $MinioPassword
Push-Location backend
uv run alembic upgrade head
Pop-Location
$counts = docker compose -f infra/compose.yaml exec -T postgres psql -U research -d research -At -c "SELECT concat_ws(',', (SELECT count(*) FROM document), (SELECT count(*) FROM document_version), (SELECT count(*) FROM document_chunk), (SELECT count(*) FROM chat_session), (SELECT count(*) FROM chat_message), (SELECT count(*) FROM chat_run), (SELECT count(*) FROM paper_note), (SELECT count(*) FROM note_revision));"
if (-not (Select-String -LiteralPath (Join-Path $resolved "counts.csv") -SimpleMatch $counts)) { throw "Restored counts do not match backup manifest" }
docker compose -f infra/compose.yaml up -d api worker
~~~

- [ ] **Step 4: Create the real evaluation dataset through user annotation**

Use this controlled process:

1. User selects at least 30 representative ReID PDFs.
2. System ingests them and exports document, section, page, and chunk IDs.
3. User writes or approves at least 50 questions covering fact, summary, comparison, results, limitations, Chinese-to-English retrieval, no-answer, and citation traps.
4. User identifies all acceptable evidence chunk IDs for each question.
5. The import command rejects fewer than 30 distinct documents, fewer than 50 cases, missing evidence for answerable cases, or missing no-answer labels.

JSONL schema:

~~~json
{"id":"q001","question":"该方法如何处理遮挡？","language":"zh","question_type":"method","scope":{"document_ids":["uuid"]},"relevant_chunk_ids":["uuid"],"answerable":true}
~~~

- [ ] **Step 5: Write the full backend end-to-end test**

test_full_mvp_flow.py executes with fake providers:

~~~text
upload PDF
→ outbox dispatch
→ parse
→ chunk
→ embed
→ READY
→ ask question
→ verify quote and page
→ request note generation
→ edit note
→ restart application dependencies
→ reload paper, chat, citation, and note revision
~~~

The test asserts IDs and version links at every boundary.

- [ ] **Step 6: Create one Windows verification entry point**

scripts/verify.ps1 runs:

~~~powershell
$ErrorActionPreference = "Stop"
docker compose -f infra/compose.yaml config --quiet
Push-Location backend
uv sync --frozen
uv run ruff check src tests
uv run pyright
uv run pytest tests/unit tests/contract tests/integration tests/security tests/e2e -v
Pop-Location
Push-Location frontend
pnpm install --frozen-lockfile
pnpm lint
pnpm tsc --noEmit
pnpm vitest run
pnpm exec playwright test
Pop-Location
~~~

- [ ] **Step 7: Run the complete automated verification**

~~~powershell
.\scripts\verify.ps1
~~~

Expected: every command exits 0; no real provider key is required.

- [ ] **Step 8: Run the real-provider smoke test**

With explicit user-supplied keys:

~~~powershell
Set-Location backend
uv run pytest tests/smoke/test_real_embedding.py tests/smoke/test_real_reranker.py tests/smoke/test_real_llm.py -v -m real_provider
~~~

Expected: one embedding batch has dimension 1536, reranker returns valid candidate IDs, and LLM structured output validates against the Pydantic schema.

- [ ] **Step 9: Run the real ReID release evaluation**

~~~powershell
Set-Location backend
uv run python -m research_agent.evaluation.cli retrieval --dataset-name reid-mvp --version 1
uv run python -m research_agent.evaluation.cli answers --dataset-name reid-mvp --version 1
uv run python -m research_agent.evaluation.cli report --latest --fail-under-recall10 0.85 --fail-under-citation-correctness 0.90 --require-model-label-compliance 1.0
~~~

Expected: command exits 0 only when all agreed thresholds pass.

- [ ] **Step 10: Verify backup restoration**

~~~powershell
.\scripts\backup.ps1 -Destination .\.tmp\backup-check
docker compose -f infra/compose.yaml down
docker compose -f infra/compose.yaml up -d
.\scripts\restore.ps1 -Source .\.tmp\backup-check
~~~

Expected: document count, READY versions, sampled PDF objects, chats, citations, and note revisions match the pre-backup snapshot.

- [ ] **Step 11: Checkpoint 6 release review**

Reject release if any condition fails:

- supported-PDF parse success below 95%;
- Recall@10 below 85%;
- citation correctness below 90%;
- model-knowledge label compliance below 100%;
- restart or restore loses durable state;
- a paper-grounded claim lacks a verified quote and page;
- upload generates a note without a user request.

- [ ] **Step 12: Prepare the user release commit checkpoint**

~~~powershell
git add .gitignore .env.example README.md infra backend frontend evaluation scripts
git commit -m "feat: complete ResearchAgent paper knowledge MVP"
~~~

## Post-MVP Plan Boundaries

After the MVP release gate passes, create separate design-aligned plans in this order:

1. Paper comparison and research lineage in PostgreSQL.
2. Persistent human-AI research sessions, branches, snapshots, evidence cards, and hypothesis state.
3. Lightweight related-paper recommendation.
4. Hypothesis and experiment cards.
5. Bounded Deep Agents exploration with tool, time, token, and cost limits.
6. Experiment-result analysis.
7. Evidence-grounded writing assistance.

Do not start any of these while the MVP release command fails.

## Official Implementation References

- [Docling installation](https://docling-project.github.io/docling/getting_started/installation/) for uv and CPU PyTorch configuration.
- [Celery Redis broker](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html) for Redis transport, visibility timeout, and acknowledgement caveats.
- [pgvector](https://github.com/pgvector/pgvector) for vector extension, cosine search, HNSW, and Docker support.
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview/) for explicit state workflows and persistence.
- [Vercel AI SDK stream protocol](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol) for Python custom-backend SSE parts.
- [Langfuse scores](https://langfuse.com/docs/evaluation/scores/overview) for reproducible evaluation and trace-linked scores.

## Execution Handoff

Plan execution must begin from Task 1 and stop at every checkpoint for review and user acceptance. Starting with Product Task 2, task-scoped staging and `git commit` are covered by the user's standing authorization only after required verification passes and independent task review is clean. `git push`, merge, worktree removal, and other integration or cleanup operations remain separately authorized.
