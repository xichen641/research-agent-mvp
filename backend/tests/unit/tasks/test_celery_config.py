from typing import cast

from celery.app.task import Task
from celery.exceptions import Retry
import pytest

from research_agent.ingestion.celery_tasks import process_document
from research_agent.tasks.celery_app import create_celery_app


def test_celery_app_uses_durable_delivery_settings_and_exact_routes() -> None:
    app = create_celery_app("redis://localhost:6379/0")

    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.task_ignore_result is True
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.broker_transport_options == {"visibility_timeout": 7200}
    assert app.conf.task_routes == {
        "research_agent.ingestion.process_document": {
            "queue": "paper_ingestion"
        },
        "research_agent.notes.generate_note": {"queue": "note_generation"},
    }
    assert app.conf.imports == ("research_agent.ingestion.celery_tasks",)


def test_process_document_safety_bridge_retries_same_version_after_60_seconds(
) -> None:
    version_id = "019f6500-0000-7000-8000-000000000001"
    task = cast(Task, process_document)

    assert task.max_retries is None
    with pytest.raises(Retry) as raised:
        task.apply(kwargs={"version_id": version_id}, throw=True)

    retry = raised.value
    assert retry.when == 60
    assert retry.sig is not None
    assert retry.sig.kwargs == {"version_id": version_id}
