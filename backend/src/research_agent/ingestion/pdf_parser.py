from collections.abc import Callable
from dataclasses import asdict
from importlib.metadata import version as package_version
import json
from pathlib import Path
import re

import pypdfium2 as pdfium

from research_agent.ingestion.contracts import (
    DocumentParser,
    ParsedDocument,
    ParsedElement,
)


_NUMBERED_HEADING_PATTERN = re.compile(
    r"^(?:[IVXLCDM]+\.|\d{1,2}(?:\.\d+)*\.)\s+"
    r"[A-Z][A-Za-z0-9 /&,:()'’-]{2,100}$"
)
_KNOWN_HEADING_PATTERN = re.compile(
    r"^(?:ABSTRACT|INTRODUCTION|RELATED WORKS?|BACKGROUND|"
    r"METHOD(?:OLOGY|S)?|PROPOSED METHOD|APPROACH|EXPERIMENTS?|"
    r"EXPERIMENTAL SETTINGS?|RESULTS?|DISCUSSION|CONCLUSION(?:S)?|"
    r"REFERENCES|BIBLIOGRAPHY|ACKNOWLEDGMENTS?|"
    r"COMPARISON WITH STATE-OF-THE-ART METHODS|IMPLEMENTATION DETAILS|"
    r"ABLATION STUDY)$",
    re.IGNORECASE,
)
_PAGE_NUMBER_PATTERN = re.compile(r"^\d{1,4}$")


class PdfiumTextDocumentParser:
    """Fast parser for born-digital papers that already contain a text layer."""

    name = "pdfium-text"
    version = package_version("pypdfium2")

    def parse(self, source: Path) -> ParsedDocument:
        document = pdfium.PdfDocument(source)
        page_texts: list[str] = []
        try:
            for page in document:
                text_page = page.get_textpage()
                try:
                    page_texts.append(_normalize_text(text_page.get_text_range()))
                finally:
                    text_page.close()
                    page.close()
        finally:
            document.close()

        elements: list[ParsedElement] = []
        current_section: tuple[str, ...] = ()
        for page_number, page_text in enumerate(page_texts, start=1):
            page_elements, current_section = _elements_for_page(
                page_text,
                page_number=page_number,
                initial_section=current_section,
            )
            elements.extend(page_elements)

        markdown = _markdown(elements)
        json_bytes = json.dumps(
            {
                "schema_name": "PdfiumTextDocument",
                "parser_version": self.version,
                "page_count": len(page_texts),
                "pages": [
                    {"page_number": index, "text": text}
                    for index, text in enumerate(page_texts, start=1)
                ],
                "elements": [asdict(element) for element in elements],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return ParsedDocument(
            markdown=markdown,
            json_bytes=json_bytes,
            elements=elements,
            images=[],
            page_count=len(page_texts),
        )


class AdaptivePdfDocumentParser:
    """Use the fast text layer when complete, otherwise retain Docling fallback."""

    name = "adaptive-pdf"
    version = f"pdfium-{package_version('pypdfium2')}+docling-{package_version('docling')}"

    def __init__(
        self,
        *,
        fast_parser: DocumentParser | None = None,
        fallback_factory: Callable[[], DocumentParser] | None = None,
    ) -> None:
        self._fast_parser = fast_parser or PdfiumTextDocumentParser()
        self._fallback_factory = fallback_factory or _docling_parser

    def parse(self, source: Path) -> ParsedDocument:
        try:
            parsed = self._fast_parser.parse(source)
        except Exception:
            return self._fallback_factory().parse(source)
        if _has_complete_text_layer(parsed):
            return parsed
        fallback = self._fallback_factory().parse(source)
        return ParsedDocument(
            markdown=fallback.markdown,
            json_bytes=fallback.json_bytes,
            elements=fallback.elements,
            images=fallback.images,
            page_count=fallback.page_count,
            warnings=(
                *fallback.warnings,
                "native PDF text layer was incomplete; used layout parser fallback",
            ),
        )


def _has_complete_text_layer(parsed: ParsedDocument) -> bool:
    if parsed.page_count <= 0:
        return False
    page_characters = {page: 0 for page in range(1, parsed.page_count + 1)}
    for element in parsed.elements:
        if element.page_number in page_characters:
            page_characters[element.page_number] += len(
                "".join(element.text.split())
            )
    # Figure-only and appendix divider pages can legitimately contain little text;
    # scanned pages normally expose none through the native text layer.
    covered = sum(characters >= 20 for characters in page_characters.values())
    body_characters = sum(page_characters.values())
    return (
        covered / parsed.page_count >= 0.95
        and body_characters >= covered * 250
    )


def _docling_parser() -> DocumentParser:
    # Keep the heavyweight layout stack out of the normal born-digital path.
    from research_agent.ingestion.docling_parser import DoclingDocumentParser

    return DoclingDocumentParser()


def _normalize_text(value: str) -> str:
    return (
        value.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\ufffe", "")
        .replace("\u00ad", "")
        .replace("\ufffd", "")
    )


def _elements_for_page(
    text: str,
    *,
    page_number: int,
    initial_section: tuple[str, ...],
) -> tuple[list[ParsedElement], tuple[str, ...]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and _PAGE_NUMBER_PATTERN.fullmatch(lines[0]):
        lines = lines[1:]
    elements: list[ParsedElement] = []
    section = initial_section
    paragraph_lines: list[str] = []
    paragraph_index = 0

    def emit_paragraph() -> None:
        nonlocal paragraph_index
        if not paragraph_lines:
            return
        paragraph_index += 1
        paragraph = _join_visual_lines(paragraph_lines)
        paragraph_lines.clear()
        if paragraph:
            elements.append(
                ParsedElement(
                    element_id=f"page-{page_number}-paragraph-{paragraph_index}",
                    kind="text",
                    text=paragraph,
                    section_path=section,
                    page_number=page_number,
                    bbox=None,
                )
            )

    for line in lines:
        abstract_body = _prefixed_body(line, "Abstract")
        if abstract_body is not None:
            emit_paragraph()
            section = ("Abstract",)
            elements.append(
                ParsedElement(
                    element_id=f"page-{page_number}-section-{len(elements) + 1}",
                    kind="section_header",
                    text="Abstract",
                    section_path=section,
                    page_number=page_number,
                    bbox=None,
                )
            )
            if abstract_body:
                paragraph_lines.append(abstract_body)
            continue
        if _is_heading(line):
            emit_paragraph()
            section = (_clean_heading(line),)
            elements.append(
                ParsedElement(
                    element_id=f"page-{page_number}-section-{len(elements) + 1}",
                    kind="section_header",
                    text=section[0],
                    section_path=section,
                    page_number=page_number,
                    bbox=None,
                )
            )
            continue
        paragraph_lines.append(line)
        if sum(len(item) for item in paragraph_lines) >= 900 and _ends_sentence(line):
            emit_paragraph()
    emit_paragraph()
    return elements, section


def _prefixed_body(line: str, prefix: str) -> str | None:
    match = re.match(rf"^{re.escape(prefix)}\s*[-—:]\s*(.*)$", line, re.IGNORECASE)
    return None if match is None else match.group(1).strip()


def _is_heading(line: str) -> bool:
    return len(line) <= 120 and (
        _NUMBERED_HEADING_PATTERN.fullmatch(line) is not None
        or _KNOWN_HEADING_PATTERN.fullmatch(line) is not None
    )


def _clean_heading(line: str) -> str:
    return " ".join(line.split()).strip(" .")


def _ends_sentence(line: str) -> bool:
    return line.endswith((".", "!", "?", ";", ":", "。", "！", "？"))


def _join_visual_lines(lines: list[str]) -> str:
    output = ""
    for line in lines:
        if not output:
            output = line
        elif output.endswith("-") and line[:1].islower():
            output = f"{output[:-1]}{line}"
        else:
            output = f"{output} {line}"
    return " ".join(output.split())


def _markdown(elements: list[ParsedElement]) -> str:
    parts: list[str] = []
    for element in elements:
        if element.kind == "section_header":
            parts.append(f"## {element.text}")
        else:
            parts.append(element.text)
    return "\n\n".join(parts)
