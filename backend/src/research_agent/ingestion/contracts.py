from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BoundingBox:
    left: float
    top: float
    right: float
    bottom: float


@dataclass(frozen=True, slots=True)
class ParsedElement:
    element_id: str
    kind: str
    text: str
    section_path: tuple[str, ...]
    page_number: int
    bbox: BoundingBox | None


@dataclass(frozen=True, slots=True)
class ParsedImage:
    image_id: str
    content: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    markdown: str
    json_bytes: bytes
    elements: list[ParsedElement]
    images: list[ParsedImage]
    page_count: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParseQualityReport:
    total_pages: int
    parsed_pages: int
    body_characters: int
    empty_pages: tuple[int, ...]
    section_count: int
    table_count: int
    image_count: int
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParseResult:
    quality: ParseQualityReport
    json_object_key: str
    markdown_object_key: str
    image_object_keys: tuple[str, ...]
    needs_review: bool


class DocumentParser(Protocol):
    name: str
    version: str

    def parse(self, source: Path) -> ParsedDocument: ...
