"""Public parse quality data, without private storage keys or source text."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

NonNegativeInt = Annotated[int, Field(ge=0)]


class ParseQualityResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    total_pages: PositiveInt
    parsed_pages: NonNegativeInt
    body_characters: NonNegativeInt
    empty_pages: list[PositiveInt]
    section_count: NonNegativeInt
    table_count: NonNegativeInt
    image_count: NonNegativeInt
    warnings: list[str]

    @model_validator(mode="after")
    def check_page_counts(self) -> "ParseQualityResponse":
        if self.parsed_pages > self.total_pages:
            raise ValueError("parsed_pages exceeds total_pages")
        if any(page > self.total_pages for page in self.empty_pages):
            raise ValueError("empty page number exceeds total_pages")
        return self


class DocumentQualityResponse(BaseModel):
    document_id: UUID
    version_id: UUID
    source_sha256: str
    status: str
    parser_name: str
    parser_version: str
    pipeline_version: str
    quality_state: Literal["available", "pending", "unavailable"]
    quality: ParseQualityResponse | None
