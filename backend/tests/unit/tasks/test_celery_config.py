from unittest.mock import AsyncMock
from typing import cast
from uuid import UUID

from celery.app.task import Task
from celery.exceptions import Retry
import pytest

from research_agent.chat import celery_tasks as chat_celery_tasks
from research_agent.chat.celery_tasks import process_chat_run
from research_agent.ingestion import celery_tasks
from research_agent.ingestion.celery_tasks import process_document, run_ingestion_job
from research_agent.shared.errors import AppError, TransientInfrastructureError
from research_agent.tasks.celery_app import create_celery_app


def test_celery_app_uses_durable_delivery_settings_and_exact_routes() -> None:
    app = create_celery_app("redis://localhost:6379/0")

    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.task_ignore_result is True
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.broker_transport_options == {"visibility_timeout": 7200}
    assert app.conf.task_routes == {
        "research_agent.ingestion.process_document": {"queue": "paper_ingestion"},
        "research_agent.notes.generate_note": {"queue": "note_generation"},
        "research_agent.chat.process_run": {"queue": "chat_generation"},
    }
    assert app.conf.imports == (
        "research_agent.ingestion.celery_tasks",
        "research_agent.notes.celery_tasks",
        "research_agent.chat.celery_tasks",
    )


def test_process_document_has_bounded_transient_autoretry_contract() -> None:
    task = cast(Task, process_document)

    assert task.name == "research_agent.ingestion.process_document"
    assert task.max_retries == 5
    assert getattr(task, "autoretry_for") == (TransientInfrastructureError,)
    assert getattr(task, "retry_backoff") is True
    assert getattr(task, "retry_jitter") is True


def test_process_document_runs_async_job_with_valid_uuid(monkeypatch) -> None:
    version_id = "019f6500-0000-7000-8000-000000000001"
    job = AsyncMock()
    monkeypatch.setattr(celery_tasks, "run_ingestion_job", job)

    task = cast(Task, process_document)
    task.apply(kwargs={"version_id": version_id}, throw=True)

    job.assert_awaited_once_with(UUID(version_id))


def test_process_document_rejects_invalid_uuid_without_starting_job(
    monkeypatch,
) -> None:
    job = AsyncMock()
    monkeypatch.setattr(celery_tasks, "run_ingestion_job", job)

    task = cast(Task, process_document)
    with pytest.raises(ValueError):
        task.apply(kwargs={"version_id": "not-a-uuid"}, throw=True)

    job.assert_not_called()


def test_process_chat_run_dispatches_valid_uuid(monkeypatch) -> None:
    run_id = "019f6500-0000-7000-8000-000000000002"
    job = AsyncMock()
    monkeypatch.setattr(chat_celery_tasks, "run_chat_job", job)

    task = cast(Task, process_chat_run)
    task.apply(kwargs={"run_id": run_id}, throw=True)

    job.assert_awaited_once_with(UUID(run_id))


def test_process_chat_run_rejects_invalid_uuid(monkeypatch) -> None:
    job = AsyncMock()
    monkeypatch.setattr(chat_celery_tasks, "run_chat_job", job)

    task = cast(Task, process_chat_run)
    with pytest.raises(ValueError):
        task.apply(kwargs={"run_id": "not-a-uuid"}, throw=True)

    job.assert_not_called()


def test_process_document_does_not_retry_permanent_app_error(monkeypatch) -> None:
    job = AsyncMock(side_effect=AppError("PERMANENT", "Permanent failure"))
    monkeypatch.setattr(celery_tasks, "run_ingestion_job", job)

    task = cast(Task, process_document)
    with pytest.raises(AppError) as raised:
        task.apply(
            kwargs={"version_id": "019f6500-0000-7000-8000-000000000001"},
            throw=True,
        )

    assert raised.value.code == "PERMANENT"


def test_process_document_retries_retryable_app_error(monkeypatch) -> None:
    job = AsyncMock(
        side_effect=AppError("TEMPORARY", "Temporary failure", retryable=True)
    )
    monkeypatch.setattr(celery_tasks, "run_ingestion_job", job)

    task = cast(Task, process_document)
    with pytest.raises(Retry) as raised:
        task.apply(
            kwargs={"version_id": "019f6500-0000-7000-8000-000000000001"},
            throw=True,
        )

    assert isinstance(raised.value.exc, TransientInfrastructureError)


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class FakeSessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        del args


class FailingPipeline:
    async def run(self, version_id: UUID) -> None:
        del version_id
        raise AppError("TEMPORARY", "Temporary failure", retryable=True)


@pytest.mark.asyncio
async def test_run_ingestion_job_disposes_fresh_engine_on_failure(monkeypatch) -> None:
    engine = FakeEngine()
    monkeypatch.setattr(celery_tasks, "build_async_engine", lambda _url: engine)
    monkeypatch.setattr(
        celery_tasks,
        "build_session_factory",
        lambda _engine: lambda: FakeSessionContext(),
    )
    monkeypatch.setattr(
        celery_tasks,
        "_build_pipeline",
        lambda _session, _settings: FailingPipeline(),
    )

    with pytest.raises(AppError, match="Temporary failure"):
        await run_ingestion_job(UUID("019f6500-0000-7000-8000-000000000001"))

    assert engine.disposed is True


@pytest.mark.asyncio
async def test_run_ingestion_job_disposes_engine_when_factory_creation_fails(
    monkeypatch,
) -> None:
    engine = FakeEngine()
    monkeypatch.setattr(celery_tasks, "build_async_engine", lambda _url: engine)

    def fail_factory(_engine: object) -> None:
        raise RuntimeError("session factory could not be created")

    monkeypatch.setattr(celery_tasks, "build_session_factory", fail_factory)

    with pytest.raises(RuntimeError, match="session factory"):
        await run_ingestion_job(UUID("019f6500-0000-7000-8000-000000000001"))

    assert engine.disposed is True
