from typing import NoReturn

from celery.app.task import Task

from research_agent.tasks.celery_app import celery_app


@celery_app.task(
    bind=True,
    name="research_agent.ingestion.process_document",
    max_retries=None,
)
def process_document(self: Task, version_id: str) -> NoReturn:
    """Keep ingestion work pending until Task 7 installs the real pipeline."""
    del version_id
    raise self.retry(countdown=60)
