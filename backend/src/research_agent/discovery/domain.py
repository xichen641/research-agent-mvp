from dataclasses import dataclass
from datetime import datetime
import re
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


_ARXIV_ID = re.compile(
    r"^(?P<base>(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5}))"
    r"(?P<version>v\d+)?$",
    re.IGNORECASE,
)


class ArxivPaper(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arxiv_id: str
    title: str = Field(min_length=1, max_length=2_000)
    abstract: str = Field(min_length=1, max_length=50_000)
    authors: list[str] = Field(min_length=1, max_length=500)
    categories: list[str] = Field(default_factory=list, max_length=100)
    primary_category: str | None = None
    published_at: datetime
    updated_at: datetime
    abstract_url: str
    pdf_url: str
    license_url: str | None = None

    @field_validator("arxiv_id")
    @classmethod
    def validate_arxiv_id(cls, value: str) -> str:
        return normalize_arxiv_id(value)

    @field_validator("abstract_url", "pdf_url")
    @classmethod
    def validate_arxiv_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname not in {
            "arxiv.org",
            "export.arxiv.org",
        }:
            raise ValueError("arXiv URLs must use HTTPS on an arxiv.org host")
        return value


class ArxivSearchPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    total_results: int = Field(ge=0)
    start: int = Field(ge=0)
    items_per_page: int = Field(ge=0)
    items: list[ArxivPaper]
    cached: bool = False


@dataclass(frozen=True, slots=True)
class ExternalPaperImportRecord:
    provider: str
    external_id: str
    external_version: int
    document_id: UUID
    document_version_id: UUID
    task_id: UUID
    metadata: dict[str, object]
    license_url: str | None
    imported_at: datetime


@dataclass(frozen=True, slots=True)
class ArxivImportReceipt:
    arxiv_id: str
    document_id: UUID
    document_version_id: UUID
    task_id: UUID
    duplicate: bool


def normalize_arxiv_id(value: str) -> str:
    normalized = value.strip()
    for prefix in (
        "https://arxiv.org/abs/",
        "http://arxiv.org/abs/",
        "https://export.arxiv.org/abs/",
        "http://export.arxiv.org/abs/",
    ):
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix)
            break
    if not _ARXIV_ID.fullmatch(normalized):
        raise ValueError("invalid arXiv identifier")
    return normalized


def split_arxiv_id(value: str) -> tuple[str, int]:
    normalized = normalize_arxiv_id(value)
    match = _ARXIV_ID.fullmatch(normalized)
    if match is None:  # pragma: no cover - normalization already proves this
        raise ValueError("invalid arXiv identifier")
    version = match.group("version")
    return match.group("base"), 1 if version is None else int(version[1:])
