from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, NotRequired, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from research_agent.retrieval.domain import (
    RetrievalCandidate,
    RetrievalScope,
)


ChatIntent = Literal["fact", "summary", "compare_methods", "background"]
ChatRole = Literal["user", "assistant"]
ChatRunStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
RetrievalMode = Literal["auto", "off", "on"]


class QueryPlan(BaseModel):
    intent: ChatIntent
    original_query: str
    lexical_queries: tuple[str, ...] = ()
    semantic_query: str = ""
    needs_retrieval: bool = True

    @field_validator("original_query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value

    @field_validator("lexical_queries")
    @classmethod
    def reject_blank_lexical_queries(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not query.strip() for query in value):
            raise ValueError("lexical queries must not contain blank values")
        return value


class CitationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: UUID
    document_version_id: UUID | None = None
    quote: str

    @field_validator("quote")
    @classmethod
    def reject_blank_quote(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("citation quote must not be blank")
        return value


class PaperEvidenceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["paper_evidence"] = "paper_evidence"
    text: str
    citations: list[CitationDraft]

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("paper evidence text must not be blank")
        return value


class ModelKnowledgeBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["model_knowledge"] = "model_knowledge"
    text: str

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model knowledge text must not be blank")
        return value


DraftAnswerBlock = Annotated[
    PaperEvidenceBlock | ModelKnowledgeBlock,
    Field(discriminator="type"),
]


class AnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: list[DraftAnswerBlock]

    @field_validator("blocks")
    @classmethod
    def reject_empty_answer(
        cls, value: list[DraftAnswerBlock]
    ) -> list[DraftAnswerBlock]:
        if not value:
            raise ValueError("answer draft must contain at least one block")
        return value


class VerifiedCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    title: str
    section_path: tuple[str, ...]
    page_start: int
    page_end: int
    quote: str


class VerifiedPaperEvidenceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["paper_evidence"] = "paper_evidence"
    text: str
    citations: list[VerifiedCitation]


VerifiedAnswerBlock = Annotated[
    VerifiedPaperEvidenceBlock | ModelKnowledgeBlock,
    Field(discriminator="type"),
]


class VerifiedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: list[VerifiedAnswerBlock]
    warnings: list[str] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)


class GeneralAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=20_000)

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("general answer must not be blank")
        return value


class ConversationMemoryDraft(BaseModel):
    """A compact, non-authoritative projection of earlier conversation turns."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=20_000)
    durable_facts: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("summary")
    @classmethod
    def reject_blank_summary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("memory summary must not be blank")
        return value.strip()

    @field_validator("durable_facts")
    @classmethod
    def normalize_facts(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        return list(dict.fromkeys(normalized))


class ChatGraphState(TypedDict):
    session_id: UUID
    run_id: UUID
    question: str
    scope: RetrievalScope
    retrieval_mode: RetrievalMode
    history_message_count: int
    context: NotRequired[list[dict[str, str]]]
    memory_summary: NotRequired[str]
    memory_compacted: NotRequired[bool]
    plan: NotRequired[QueryPlan]
    evidence: NotRequired[list[RetrievalCandidate]]
    evidence_sufficient: NotRequired[bool]
    draft: NotRequired[AnswerDraft]
    verified_answer: NotRequired[VerifiedAnswer]
    warnings: NotRequired[list[str]]


@dataclass(frozen=True, slots=True)
class ChatSessionRecord:
    id: UUID
    title: str
    scope: RetrievalScope
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ChatMessageRecord:
    id: UUID
    session_id: UUID
    role: ChatRole
    content: dict[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ChatRunRecord:
    id: UUID
    session_id: UUID
    status: ChatRunStatus
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None
    question: str | None = None
    scope: RetrievalScope | None = None
    retrieval_mode: RetrievalMode = "auto"
    history_message_count: int = 0


@dataclass(frozen=True, slots=True)
class ChatRunEventRecord:
    run_id: UUID
    sequence: int
    event_type: str
    payload: dict[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ChatRunResult:
    run_id: UUID
    answer: VerifiedAnswer


@dataclass(frozen=True, slots=True)
class PreparedChatRun:
    run_id: UUID
    session_id: UUID
    question: str
    scope: RetrievalScope
    retrieval_mode: RetrievalMode = "auto"
    history_message_count: int = 0


@dataclass(frozen=True, slots=True)
class ConversationMemoryRecord:
    session_id: UUID
    summary: str
    durable_facts: tuple[str, ...]
    covered_message_count: int
    revision: int
    updated_at: datetime
