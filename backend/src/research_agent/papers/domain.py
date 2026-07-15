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
