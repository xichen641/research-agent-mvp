from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


NoteFieldStatus = Literal["AI_GENERATED", "USER_CONFIRMED"]
NoteRevisionSource = Literal["AI_DRAFT", "USER_EDIT"]

NOTE_TEMPLATE: tuple[tuple[str, str], ...] = (
    ("research_question", "研究问题"),
    ("paper_positioning", "论文定位"),
    ("method_overview", "方法概述"),
    ("innovations", "主要创新点"),
    ("datasets_and_metrics", "数据集与指标"),
    ("experimental_setup", "实验设置"),
    ("main_results", "主要结果"),
    ("ablations", "消融实验"),
    ("limitations", "局限性"),
    ("related_papers", "相关论文"),
    ("research_inspiration", "研究启发"),
    ("user_comments", "用户备注"),
)
NOTE_KEYS = tuple(key for key, _ in NOTE_TEMPLATE)


class NoteField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    title: str
    markdown: str
    evidence_chunk_ids: list[UUID] = Field(default_factory=list)
    status: NoteFieldStatus

    @field_validator("key", "title")
    @classmethod
    def reject_blank_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("note field identity must not be blank")
        return value


class PaperNoteContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[NoteField]

    @model_validator(mode="after")
    def enforce_fixed_template(self) -> "PaperNoteContent":
        if tuple(field.key for field in self.fields) != NOTE_KEYS:
            raise ValueError("note fields must match the fixed MVP template")
        expected_titles = dict(NOTE_TEMPLATE)
        if any(field.title != expected_titles[field.key] for field in self.fields):
            raise ValueError("note field titles must match the fixed MVP template")
        return self

    def field(self, key: str) -> NoteField:
        return next(field for field in self.fields if field.key == key)


@dataclass(frozen=True, slots=True)
class NoteEvidence:
    field_key: str
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    title: str
    section_path: tuple[str, ...]
    page_start: int
    page_end: int
    quote: str


@dataclass(frozen=True, slots=True)
class NoteRevisionRecord:
    id: UUID
    note_id: UUID
    revision_number: int
    source: NoteRevisionSource
    content: PaperNoteContent
    created_at: datetime
    evidence: tuple[NoteEvidence, ...]


@dataclass(frozen=True, slots=True)
class PaperNoteRecord:
    id: UUID
    document_id: UUID
    current_revision_id: UUID | None
    created_at: datetime
    updated_at: datetime
    current_revision: NoteRevisionRecord | None = None
