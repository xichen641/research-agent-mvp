import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from research_agent.main import create_app
from research_agent.papers.models import (
    DocumentModel,
    DocumentTaskModel,
    DocumentVersionModel,
    OutboxEventModel,
)
from research_agent.papers.domain import PaperPersistenceError
from research_agent.papers.routes import get_db_session, get_paper_repository
from research_agent.shared.ids import new_id
from research_agent.tasks.domain import TaskStage, TaskStatus


@pytest.fixture(scope="session")
def contract_database_url() -> Iterator[str]:
    with PostgresContainer(
        "pgvector/pgvector:pg17",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", database_url)
        command.upgrade(config, "head")
        yield database_url


@pytest_asyncio.fixture
async def session_factory(
    contract_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(contract_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE TABLE outbox_event, document_task, "
                    "document_version, document CASCADE"
                )
            )
        await engine.dispose()


async def seed_document(
    factory: async_sessionmaker[AsyncSession],
    *,
    title: str,
    created_at: datetime,
    task_status: TaskStatus = TaskStatus.RUNNING,
    error_code: str | None = None,
) -> tuple[DocumentModel, DocumentVersionModel, DocumentTaskModel]:
    document_id = new_id()
    version_id = new_id()
    task_id = new_id()
    async with factory() as session:
        document = DocumentModel(
            id=document_id,
            title=title,
            sha256=str(document_id).replace("-", "") * 2,
            status="FAILED" if task_status is TaskStatus.FAILED else "PROCESSING",
            current_version_id=None,
            created_at=created_at,
            updated_at=created_at,
        )
        session.add(document)
        await session.flush()
        version = DocumentVersionModel(
            id=version_id,
            document_id=document_id,
            filename=f"{title}.pdf",
            source_object_key="papers/private/source.pdf",
            parser_name="docling",
            parser_version="private-parser-version",
            pipeline_version="1",
            status="FAILED" if task_status is TaskStatus.FAILED else "PROCESSING",
            parsed_json_object_key="papers/private/document.json",
            parse_quality={"private": "internal"},
            created_at=created_at,
        )
        session.add(version)
        await session.flush()
        task = DocumentTaskModel(
            id=task_id,
            document_version_id=version_id,
            stage=TaskStage.PARSING,
            status=task_status,
            progress_current=2,
            progress_total=8,
            retry_count=0,
            error_code=error_code,
            error_message=(
                "Source document could not be parsed"
                if task_status is TaskStatus.FAILED
                else None
            ),
            created_at=created_at,
            updated_at=created_at,
        )
        session.add(task)
        await session.flush()
        document.current_version_id = version_id
        await session.commit()
        return document, version, task


def build_client(
    factory: async_sessionmaker[AsyncSession],
    *,
    origin: str | None = None,
) -> httpx.AsyncClient:
    app = create_app(web_origin="http://web.test")

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    headers = {"Origin": origin} if origin else None
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=headers,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_list_uses_stable_opaque_keyset_pagination(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    anchor = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
    seeded = [
        await seed_document(
            session_factory,
            title=f"Paper {index}",
            created_at=anchor - timedelta(minutes=index // 2),
        )
        for index in range(5)
    ]

    async with build_client(session_factory) as client:
        first = await client.get("/api/v1/documents", params={"limit": 2})
        second = await client.get(
            "/api/v1/documents",
            params={"limit": 2, "cursor": first.json()["next_cursor"]},
        )
        third = await client.get(
            "/api/v1/documents",
            params={"limit": 2, "cursor": second.json()["next_cursor"]},
        )

    assert first.status_code == second.status_code == third.status_code == 200
    pages = [first.json(), second.json(), third.json()]
    ids = [item["id"] for page in pages for item in page["items"]]
    expected_ids = [
        str(item[0].id)
        for item in sorted(
            seeded,
            key=lambda item: (item[0].created_at, item[0].id),
            reverse=True,
        )
    ]
    assert ids == expected_ids
    assert len(ids) == len(set(ids)) == 5
    assert pages[0]["next_cursor"]
    assert pages[1]["next_cursor"]
    assert pages[2]["next_cursor"] is None
    assert set(pages[0]["items"][0]) == {
        "id",
        "title",
        "status",
        "current_version_id",
        "latest_task_id",
        "created_at",
        "updated_at",
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_cursor_rejection_and_sanitized_detail(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    document, version, _ = await seed_document(
        session_factory,
        title="Safe paper",
        created_at=datetime.now(UTC),
    )
    async with build_client(session_factory) as client:
        invalid = await client.get(
            "/api/v1/documents", params={"cursor": "not-a-cursor"}
        )
        missing = await client.get(f"/api/v1/documents/{new_id()}")
        detail = await client.get(f"/api/v1/documents/{document.id}")

    assert invalid.status_code == 400
    assert invalid.json()["code"] == "INVALID_CURSOR"
    assert missing.status_code == 404
    assert missing.json()["code"] == "DOCUMENT_NOT_FOUND"
    assert detail.status_code == 200
    assert detail.json()["current_version"] == {
        "id": str(version.id),
        "filename": "Safe paper.pdf",
        "status": "PROCESSING",
        "pipeline_version": "1",
        "created_at": version.created_at.isoformat().replace("+00:00", "Z"),
    }
    serialized = detail.text.lower()
    for secret in ("source_object_key", "parser_name", "parser_version", "parse_quality"):
        assert secret not in serialized
    assert "private" not in serialized


@pytest.mark.integration
@pytest.mark.asyncio
async def test_task_view_computes_retryability_and_missing_is_sanitized(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, version, task = await seed_document(
        session_factory,
        title="Failed",
        created_at=datetime.now(UTC),
        task_status=TaskStatus.FAILED,
        error_code="PARSE_PARSER_FAILED",
    )
    async with build_client(session_factory) as client:
        response = await client.get(f"/api/v1/tasks/{task.id}")
        missing = await client.get(f"/api/v1/tasks/{new_id()}")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(task.id),
        "document_version_id": str(version.id),
        "stage": "PARSING",
        "status": "FAILED",
        "progress_current": 2,
        "progress_total": 8,
        "retry_count": 0,
        "error_code": "PARSE_PARSER_FAILED",
        "error_message": "Source document could not be parsed",
        "retryable": True,
    }
    assert missing.status_code == 404
    assert missing.json()["code"] == "TASK_NOT_FOUND"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retry_preserves_failure_context_and_writes_exact_outbox_payload(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, version, task = await seed_document(
        session_factory,
        title="Retry",
        created_at=datetime.now(UTC),
        task_status=TaskStatus.FAILED,
        error_code="PARSE_PARSER_FAILED",
    )
    async with build_client(session_factory) as client:
        response = await client.post(f"/api/v1/tasks/{task.id}/retry")

    assert response.status_code == 202
    assert response.json() == {
        "id": str(task.id),
        "document_version_id": str(version.id),
        "stage": "PARSING",
        "status": "QUEUED",
        "progress_current": 2,
        "progress_total": 8,
        "retry_count": 1,
        "error_code": "PARSE_PARSER_FAILED",
        "error_message": "Source document could not be parsed",
        "retryable": False,
    }
    async with session_factory() as session:
        stored = await session.get(DocumentTaskModel, task.id)
        outbox = (
            await session.scalars(
                select(OutboxEventModel).where(
                    OutboxEventModel.event_type == "document.retry_requested"
                )
            )
        ).one()
    assert stored is not None
    assert stored.retry_count == 1
    assert stored.stage == "PARSING"
    assert stored.progress_current == 2
    assert stored.progress_total == 8
    assert stored.error_code == "PARSE_PARSER_FAILED"
    assert stored.error_message == "Source document could not be parsed"
    assert outbox.payload == {
        "task_name": "research_agent.ingestion.process_document",
        "queue": "paper_ingestion",
        "kwargs": {"version_id": str(version.id)},
    }


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        (TaskStatus.RUNNING, None),
        (TaskStatus.FAILED, "PERMANENT_FAILURE"),
    ],
)
async def test_retry_rejects_non_failed_and_permanent_failures(
    session_factory: async_sessionmaker[AsyncSession],
    status: TaskStatus,
    error_code: str | None,
) -> None:
    _, _, task = await seed_document(
        session_factory,
        title=f"Reject {status} {error_code}",
        created_at=datetime.now(UTC),
        task_status=status,
        error_code=error_code,
    )
    async with build_client(session_factory) as client:
        response = await client.post(f"/api/v1/tasks/{task.id}/retry")

    assert response.status_code == 409
    assert response.json()["code"] == "TASK_NOT_RETRYABLE"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_retry_accepts_exactly_one_request(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, _, task = await seed_document(
        session_factory,
        title="Concurrent",
        created_at=datetime.now(UTC),
        task_status=TaskStatus.FAILED,
        error_code="PARSE_PARSER_FAILED",
    )

    async def retry() -> httpx.Response:
        async with build_client(session_factory) as client:
            return await client.post(f"/api/v1/tasks/{task.id}/retry")

    responses = await asyncio.gather(retry(), retry())
    assert sorted(response.status_code for response in responses) == [202, 409]
    async with session_factory() as session:
        stored = await session.get(DocumentTaskModel, task.id)
        retry_outbox_count = await session.scalar(
            select(func.count())
            .select_from(OutboxEventModel)
            .where(OutboxEventModel.event_type == "document.retry_requested")
        )
    assert stored is not None
    assert stored.retry_count == 1
    assert retry_outbox_count == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cors_allows_only_configured_web_origin(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with build_client(session_factory, origin="http://web.test") as client:
        allowed = await client.options(
            "/api/v1/documents",
            headers={
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-trace-id",
            },
        )
    async with build_client(session_factory, origin="http://evil.test") as client:
        denied = await client.options(
            "/api/v1/documents",
            headers={"Access-Control-Request-Method": "POST"},
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://web.test"
    assert allowed.headers.get("access-control-allow-credentials") != "true"
    assert set(allowed.headers["access-control-allow-methods"].split(", ")) == {
        "GET",
        "POST",
        "PUT",
        "OPTIONS",
    }
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


class FailingPaperQueryRepository:
    async def list_documents(self, **kwargs: object) -> object:
        del kwargs
        raise PaperPersistenceError() from RuntimeError("private SQL token")

    async def get_document_detail(self, document_id: object) -> object:
        del document_id
        raise PaperPersistenceError() from RuntimeError("private SQL token")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/documents",
        f"/api/v1/documents/{new_id()}",
    ],
)
async def test_document_reads_translate_persistence_failures_to_safe_problem(
    path: str,
) -> None:
    app = create_app(web_origin="http://web.test")
    app.dependency_overrides[get_paper_repository] = FailingPaperQueryRepository
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(path, headers={"X-Trace-ID": "query-trace"})

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "PAPER_PERSISTENCE_ERROR"
    assert response.json()["message"] == "Paper data could not be loaded"
    assert response.json()["retryable"] is True
    assert response.json()["trace_id"] == "query-trace"
    assert "private" not in response.text.lower()
    assert "sql" not in response.text.lower()
    assert "token" not in response.text.lower()
