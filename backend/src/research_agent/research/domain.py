from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SpaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    goal: str = Field(default="", max_length=4000)
    task_settings: str = Field(default="", max_length=4000)
    resource_constraints: str = Field(default="", max_length=4000)
    current_question: str = Field(default="", max_length=4000)
    material_version_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator("material_version_ids")
    @classmethod
    def unique_versions(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("material versions must be unique")
        return value


class SpaceUpdate(SpaceInput):
    expected_revision: int = Field(ge=1)


class SpaceMaterial(BaseModel):
    document_id: UUID
    document_version_id: UUID
    title: str
    filename: str
    status: str
    is_current_version: bool


class ResearchSpace(BaseModel):
    id: UUID
    name: str
    goal: str
    task_settings: str
    resource_constraints: str
    current_question: str
    revision: int
    created_at: datetime
    updated_at: datetime
    materials: list[SpaceMaterial]
