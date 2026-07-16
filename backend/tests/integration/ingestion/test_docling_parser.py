import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from docling_core.types.doc.base import BoundingBox as DoclingBoundingBox, Size
from docling_core.types.doc.common.reference import PageItem, ProvenanceItem
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.table.table_data import TableCell, TableData

from research_agent.ingestion.docling_parser import DoclingDocumentParser
from ...helpers.pdf_factory import create_two_page_pdf


pytestmark = pytest.mark.integration


def test_generated_two_page_pdf_preserves_text_json_and_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source = tmp_path / "generated.pdf"
    page_one_text, page_two_text = create_two_page_pdf(source)
    parser = DoclingDocumentParser()

    parsed = parser.parse(source)

    assert parser.name == "docling"
    assert parser.version
    assert parser._pipeline_options.do_ocr is False
    assert parsed.page_count == 2
    assert page_one_text in parsed.markdown
    assert page_two_text in parsed.markdown
    normalized_text = "\n".join(element.text for element in parsed.elements)
    assert page_one_text in normalized_text
    assert page_two_text in normalized_text
    document_json = json.loads(parsed.json_bytes)
    assert document_json["schema_name"] == "DoclingDocument"
    text_elements = [element for element in parsed.elements if element.text.strip()]
    assert {element.page_number for element in text_elements} == {1, 2}
    assert any(element.bbox is not None for element in text_elements)


def test_table_and_picture_emit_every_provenance_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = DoclingDocument(
        name="multi-provenance",
        pages={
            1: PageItem(page_no=1, size=Size(width=600, height=800)),
            2: PageItem(page_no=2, size=Size(width=600, height=800)),
        },
    )
    page_one = ProvenanceItem(
        page_no=1,
        bbox=DoclingBoundingBox(l=10, t=20, r=100, b=80),
        charspan=(0, 0),
    )
    page_two = ProvenanceItem(
        page_no=2,
        bbox=DoclingBoundingBox(l=15, t=25, r=110, b=90),
        charspan=(0, 0),
    )
    table = document.add_table(
        TableData(
            num_rows=1,
            num_cols=1,
            table_cells=[
                TableCell(
                    start_row_offset_idx=0,
                    end_row_offset_idx=1,
                    start_col_offset_idx=0,
                    end_col_offset_idx=1,
                    text="cell",
                )
            ],
        ),
        prov=page_one,
    )
    table.prov.append(page_two)
    picture = document.add_picture(prov=page_one)
    picture.prov.append(page_two)
    parser = DoclingDocumentParser()
    monkeypatch.setattr(
        parser._converter,
        "convert",
        lambda source: SimpleNamespace(document=document),
    )

    parsed = parser.parse(tmp_path / "not-read.pdf")

    table_elements = [element for element in parsed.elements if element.kind == "table"]
    picture_elements = [
        element for element in parsed.elements if element.kind == "picture"
    ]
    assert [(element.element_id, element.page_number) for element in table_elements] == [
        ("#/tables/0", 1),
        ("#/tables/0@prov-1", 2),
    ]
    assert table_elements[0].text == table_elements[1].text
    assert "cell" in table_elements[0].text
    assert [
        (element.element_id, element.page_number) for element in picture_elements
    ] == [
        ("#/pictures/0", 1),
        ("#/pictures/0@prov-1", 2),
    ]
    assert all(element.bbox is not None for element in table_elements + picture_elements)
