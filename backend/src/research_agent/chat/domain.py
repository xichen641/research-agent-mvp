from dataclasses import dataclass
from datetime import datetime
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from research_agent.retrieval.domain import (
    RetrievalCandidate,
    RetrievalScope,
)


ChatIntent = Literal["fact", "summary", "compare_methods", "background"]
ChatRole = Literal["user", "assistant"]
ChatRunStatus = Literal["RUNNING", "SUCCEEDED", "FAILED"]


class QueryPlan(BaseModel):
    intent: ChatIntent
    original_query: str
    lexical_queries: tuple[str, ...]
    semantic_query: str

    @field_validator("original_query", "semantic_query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value

    @field_validator("lexical_queries")
    @classmethod
    def reject_blank_lexical_queries(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not query.strip() for query in value):
            raise ValueError("lexical queries must not be empty or blank")
        return value


class AnswerDraft(BaseModel):
    """Task-12 draft envelope; Task 13 replaces text with labelled blocks."""

    text: str


class VerifiedAnswer(BaseModel):
    """Task-12 verified envelope; deterministic citation details arrive in Task 13."""

    text: str
    warnings: list[str] = Field(default_factory=list)


class ChatGraphState(TypedDict):
    session_id: UUID
    run_id: UUID
    question: str
    scope: RetrievalScope
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
