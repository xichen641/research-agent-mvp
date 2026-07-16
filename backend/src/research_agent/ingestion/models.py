from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import (
    Computed,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from research_agent.shared.db import Base


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    target_tokens: int = 700
    overlap_tokens: int = 100

    def __post_init__(self) -> None:
        if self.target_tokens <= 0:
            raise ValueError("target_tokens must be positive")
        if self.overlap_tokens < 0:
            raise ValueError("overlap_tokens must be non-negative")
        if self.overlap_tokens >= self.target_tokens:
            raise ValueError("overlap_tokens must be less than target_tokens")


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    chunk_index: int
    section_path: tuple[str, ...]
    content: str
    content_type: str
    page_start: int
    page_end: int
    element_ids: tuple[str, ...]
    token_count: int


class DocumentChunkModel(Base):
    __tablename__ = "document_chunk"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "chunk_index",
            "pipeline_version",
            name="uq_chunk_version_index_pipeline",
        ),
        Index("ix_chunk_search_en", "search_vector_en", postgresql_using="gin"),
        Index("ix_chunk_search_zh", "search_vector_zh", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    document_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("document_version.id", ondelete="CASCADE"),
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    section_path: Mapped[list[str]] = mapped_column(JSONB)
    content: Mapped[str] = mapped_column(Text)
    lexical_text: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(32))
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    element_ids: Mapped[list[str]] = mapped_column(JSONB)
    token_count: Mapped[int] = mapped_column(Integer)
    pipeline_version: Mapped[str] = mapped_column(String(128))
    search_vector_en: Mapped[object] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', content)", persisted=True),
    )
    search_vector_zh: Mapped[object] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', lexical_text)", persisted=True),
    )
