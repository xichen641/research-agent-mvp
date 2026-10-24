"""Read-only, sanitized measurements for reproducible local baseline runs."""

from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ValidationError

from research_agent.chat.domain import ChatRunEventRecord, ChatRunRecord
from research_agent.config import Settings
from research_agent.providers.telemetry import ModelCallObservation
from research_agent.shared.errors import AppError


class Capabilities(BaseModel):
    same_session_history: bool = True
    memory_compaction: bool = True
    research_spaces: bool = True
    cross_session_research_state: bool = False
    correctable_research_records: bool = False


class RuntimeConfiguration(BaseModel):
    instrumentation: str = "t0.4-v1"
    llm_model: str
    provider_host: str | None
    llm_timeout_seconds: float
    structured_output_attempts: int = 2
    enable_thinking: bool = False
    embedding_model: str
    embedding_dimension: int
    reranker_model: str
    chat_evidence_limit: int
    memory_max_context_tokens: int
    memory_recent_message_tokens: int
    context_budget: dict[str, object] | None = None
    capabilities: Capabilities


def runtime_configuration(settings: Settings) -> RuntimeConfiguration:
    return RuntimeConfiguration(
        instrumentation="t1.3-v1",
        llm_model=settings.llm_model,
        provider_host=urlsplit(settings.llm_base_url).hostname,
        llm_timeout_seconds=settings.llm_timeout_seconds,
        embedding_model=settings.embedding_model,
        embedding_dimension=settings.embedding_dimension,
        reranker_model=settings.reranker_model,
        chat_evidence_limit=settings.chat_evidence_limit,
        memory_max_context_tokens=settings.memory_max_context_tokens,
        memory_recent_message_tokens=settings.memory_recent_message_tokens,
        context_budget=settings.chat_context_budget.model_dump(mode="json"),
        capabilities=Capabilities(),
    )


class RunDiagnosticsReader(Protocol):
    async def get_run(self, run_id: UUID) -> ChatRunRecord | None: ...

    async def list_events(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> list[ChatRunEventRecord]: ...


EVENT_FIELDS = {
    "model_result_reused": ("operation",),
    "run_started": (),
    "context_assembled": (
        "recent_message_count",
        "memory_present",
        "memory_compacted",
        "snapshot_reused",
        "history_messages_read",
        "history_max_sequence",
        "warning_count",
    ),
    "model_request_budget": (
        "operation",
        "attempt",
        "estimated_input_units",
        "output_reserved_tokens",
        "context_window_units",
        "estimator",
    ),
    "query_planned": ("intent", "needs_retrieval", "retrieval_mode"),
    "retrieval_started": (),
    "retrieval_completed": ("candidate_count",),
    "rerank_completed": ("candidate_count",),
    "evidence_checked": ("sufficient",),
    "answer_generation_started": ("source",),
    "draft_generated": (),
    "retrieval_skipped": ("retrieval_mode",),
    "clarification_required": ("warning",),
    "answer_verified": ("citation_count", "repaired"),
    "run_completed": ("message_id",),
    "run_failed": ("error_code",),
}


async def read_run_diagnostics(
    reader: RunDiagnosticsReader, run_id: UUID
) -> dict[str, object]:
    run = await reader.get_run(run_id)
    if run is None:
        raise AppError("CHAT_RUN_NOT_FOUND", "Chat run was not found")
    events = await reader.list_events(run_id)
    configuration = None
    calls = []
    timeline = []
    invalid_measurements = 0
    for event in events:
        if event.event_type == "runtime_configuration":
            try:
                configuration = RuntimeConfiguration.model_validate(
                    event.payload
                ).model_dump(mode="json")
            except ValidationError:
                invalid_measurements += 1
        elif event.event_type == "model_call_observed":
            try:
                calls.append(
                    ModelCallObservation.model_validate(event.payload).model_dump(
                        mode="json"
                    )
                )
            except ValidationError:
                invalid_measurements += 1
        elif event.event_type in EVENT_FIELDS:
            data = {
                key: value
                for key in EVENT_FIELDS[event.event_type]
                if isinstance(value := event.payload.get(key), (str, int, float, bool))
            }
            timeline.append(
                {
                    "sequence": event.sequence,
                    "type": event.event_type,
                    "created_at": event.created_at.isoformat(),
                    "data": data,
                }
            )
    return {
        "run_id": str(run.id),
        "session_id": str(run.session_id),
        "status": run.status,
        "error_code": run.error_code,
        "created_at": run.created_at.isoformat(),
        "completed_at": None
        if run.completed_at is None
        else run.completed_at.isoformat(),
        "runtime_configuration": configuration,
        "model_calls": calls,
        "timeline": timeline,
        "invalid_measurements": invalid_measurements,
        "cost": {
            "amount": None,
            "currency": None,
            "availability": "unavailable",
            "reason": "currency_qualified_billing_not_collected",
        },
    }
