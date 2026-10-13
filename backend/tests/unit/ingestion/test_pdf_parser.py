from pathlib import Path

from reportlab.pdfgen.canvas import Canvas

from research_agent.ingestion.contracts import ParsedDocument
from research_agent.ingestion.pdf_parser import (
    AdaptivePdfDocumentParser,
    PdfiumTextDocumentParser,
)


def create_two_page_pdf(destination: Path) -> tuple[str, str]:
    page_one_text = "ResearchAgent unique first page born digital text."
    page_two_text = "ResearchAgent unique second page born digital text."
    canvas = Canvas(str(destination))
    for text in (page_one_text, page_two_text):
        text_object = canvas.beginText(72, 720)
        for _ in range(8):
            text_object.textLine(text)
        canvas.drawText(text_object)
        canvas.showPage()
    canvas.save()
    return page_one_text, page_two_text


def test_pdfium_parser_preserves_pages_and_creates_retrieval_sized_elements(
    tmp_path: Path,
) -> None:
    source = tmp_path / "paper.pdf"
    page_one_text, page_two_text = create_two_page_pdf(source)

    parsed = PdfiumTextDocumentParser().parse(source)

    assert parsed.page_count == 2
    assert page_one_text in parsed.markdown
    assert page_two_text in parsed.markdown
    assert {element.page_number for element in parsed.elements} == {1, 2}
    assert all(len(element.text) <= 2_000 for element in parsed.elements)
    assert parsed.images == []


def test_adaptive_parser_uses_fast_path_for_a_complete_text_layer(
    tmp_path: Path,
) -> None:
    source = tmp_path / "paper.pdf"
    create_two_page_pdf(source)
    fallback_calls = 0

    def fallback_factory():
        nonlocal fallback_calls
        fallback_calls += 1
        raise AssertionError("complete native text must not invoke Docling")

    parsed = AdaptivePdfDocumentParser(
        fallback_factory=fallback_factory,
    ).parse(source)

    assert parsed.page_count == 2
    assert fallback_calls == 0


def test_adaptive_parser_falls_back_when_native_text_is_incomplete(
    tmp_path: Path,
) -> None:
    empty = ParsedDocument(
        markdown="",
        json_bytes=b"{}",
        elements=[],
        images=[],
        page_count=2,
    )
    fallback = ParsedDocument(
        markdown="fallback",
        json_bytes=b'{"parser":"fallback"}',
        elements=[],
        images=[],
        page_count=2,
    )

    class StubParser:
        name = "stub"
        version = "1"

        def __init__(self, result: ParsedDocument) -> None:
            self.result = result

        def parse(self, source: Path) -> ParsedDocument:
            del source
            return self.result

    parsed = AdaptivePdfDocumentParser(
        fast_parser=StubParser(empty),
        fallback_factory=lambda: StubParser(fallback),
    ).parse(tmp_path / "paper.pdf")

    assert parsed.markdown == "fallback"
    assert "used layout parser fallback" in parsed.warnings[-1]
