from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from research_agent.shared.db import Base


class ExternalApiThrottleModel(Base):
    __tablename__ = "external_api_throttle"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    next_allowed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PaperDiscoveryCacheModel(Base):
    __tablename__ = "paper_discovery_cache"
    __table_args__ = (Index("ix_paper_discovery_cache_expires", "expires_at"),)

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64))
    query: Mapped[str] = mapped_column(Text)
    response: Mapped[dict[str, object]] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ExternalPaperSourceModel(Base):
    __tablename__ = "external_paper_source"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    external_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("document.id", ondelete="CASCADE"),
    )
    document_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("document_version.id", ondelete="CASCADE"),
    )
    task_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("document_task.id", ondelete="CASCADE"),
    )
    metadata_json: Mapped[dict[str, object]] = mapped_column("metadata", JSONB)
    license_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
