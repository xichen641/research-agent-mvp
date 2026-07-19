from celery import Celery

from research_agent.config import get_settings


def create_celery_app(broker_url: str) -> Celery:
    app = Celery("research_agent", broker=broker_url)
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_ignore_result=True,
        worker_prefetch_multiplier=1,
        broker_transport_options={"visibility_timeout": 7200},
        task_routes={
            "research_agent.ingestion.process_document": {
                "queue": "paper_ingestion"
            },
            "research_agent.notes.generate_note": {"queue": "note_generation"},
        },
        imports=(
            "research_agent.ingestion.celery_tasks",
            "research_agent.notes.celery_tasks",
        ),
    )
    return app


celery_app = create_celery_app(get_settings().redis_url)
