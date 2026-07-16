from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from research_agent.shared.ids import EntityId, new_id


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
class UploadIds:
    document_id: EntityId
    version_id: EntityId
    task_id: EntityId
    outbox_event_id: EntityId

    @classmethod
    def create(cls) -> "UploadIds":
        return cls(new_id(), new_id(), new_id(), new_id())


class DuplicateDocumentError(Exception):
    def __init__(self, sha256: str) -> None:
        super().__init__(f"active document with SHA-256 {sha256} already exists")
        self.sha256 = sha256


class PaperPersistenceError(Exception):
    def __init__(self) -> None:
        super().__init__("paper persistence failed")


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
    parsed_json_object_key: str | None = None
    parsed_markdown_object_key: str | None = None
    image_object_keys: tuple[str, ...] | None = None
    parse_quality: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class DocumentSummaryRecord:
    id: EntityId
    title: str
    status: DocumentStatus
    current_version_id: EntityId
    latest_task_id: EntityId
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SafeDocumentVersionRecord:
    id: EntityId
    filename: str
    status: VersionStatus
    pipeline_version: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentDetailRecord:
    summary: DocumentSummaryRecord
    current_version: SafeDocumentVersionRecord
