from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from research_agent.shared.db import Base


class ResearchSpaceModel(Base):
    __tablename__ = "research_space"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_research_space_revision"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    goal: Mapped[str] = mapped_column(Text)
    task_settings: Mapped[str] = mapped_column(Text)
    resource_constraints: Mapped[str] = mapped_column(Text)
    current_question: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ResearchSpaceMaterialModel(Base):
    __tablename__ = "research_space_material"
    __table_args__ = (
        UniqueConstraint("space_id", "document_id", name="uq_research_space_document"),
    )

    space_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("research_space.id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("document_version.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    document_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("document.id", ondelete="RESTRICT")
    )
