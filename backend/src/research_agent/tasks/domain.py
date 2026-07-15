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
