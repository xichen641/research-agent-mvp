from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from research_agent.shared.db import Base


class ChatSessionModel(Base):
    __tablename__ = "chat_session"

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    scope: Mapped[dict[str, object]] = mapped_column(JSONB)
    research_space_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "research_space.id",
            name="fk_chat_session_research_space",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChatMessageModel(Base):
    __tablename__ = "chat_message"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_message_role"),
        Index("ix_chat_message_session_created", "session_id", "created_at", "id"),
        UniqueConstraint(
            "session_id", "sequence", name="uq_chat_message_session_sequence"
        ),
        CheckConstraint("sequence > 0", name="ck_chat_message_sequence"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("chat_session.id", ondelete="CASCADE"),
    )
    role: Mapped[str] = mapped_column(String(16))
    sequence: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChatRunModel(Base):
    __tablename__ = "chat_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_chat_run_status",
        ),
        CheckConstraint(
            "retrieval_mode IN ('auto', 'off', 'on')",
            name="ck_chat_run_retrieval_mode",
        ),
        CheckConstraint(
            "history_message_count >= 0",
            name="ck_chat_run_history_message_count",
        ),
        Index("ix_chat_run_session_created", "session_id", "created_at", "id"),
        UniqueConstraint(
            "session_id", "client_request_id", name="uq_chat_run_client_request"
        ),
        Index(
            "uq_chat_run_one_active_per_session",
            "session_id",
            unique=True,
            postgresql_where=text("status IN ('QUEUED', 'RUNNING')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("chat_session.id", ondelete="CASCADE"),
    )
    status: Mapped[str] = mapped_column(String(16))
    client_request_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    user_message_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "chat_message.id", name="fk_chat_run_user_message", ondelete="SET NULL"
        ),
        nullable=True,
    )
    assistant_message_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "chat_message.id", name="fk_chat_run_assistant_message", ondelete="SET NULL"
        ),
        nullable=True,
    )
    retry_of_run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("chat_run.id", ondelete="RESTRICT"),
        nullable=True,
    )
    memory_snapshot: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    context_snapshot: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    request_snapshots: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    research_context: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    retrieval_mode: Mapped[str] = mapped_column(String(16), default="auto")
    history_message_count: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ChatRunEventModel(Base):
    __tablename__ = "chat_run_event"
    __table_args__ = (
        CheckConstraint("sequence > 0", name="ck_chat_run_event_sequence"),
    )

    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("chat_run.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChatMemoryModel(Base):
    __tablename__ = "chat_memory"
    __table_args__ = (
        CheckConstraint(
            "covered_message_count >= 0",
            name="ck_chat_memory_covered_message_count",
        ),
        CheckConstraint("revision > 0", name="ck_chat_memory_revision"),
    )

    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("chat_session.id", ondelete="CASCADE"),
        primary_key=True,
    )
    summary: Mapped[str] = mapped_column(Text)
    durable_facts: Mapped[list[str]] = mapped_column(JSONB)
    covered_message_count: Mapped[int] = mapped_column(Integer)
    covered_through_sequence: Mapped[int] = mapped_column(Integer, default=0)
    critical_constraints: Mapped[list[str]] = mapped_column(JSONB, default=list)
    revision: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
