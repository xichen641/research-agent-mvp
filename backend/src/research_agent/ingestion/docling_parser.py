from importlib.metadata import version as package_version
from io import BytesIO
import json
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.node import DocItem
from docling_core.types.doc.items.picture.picture import PictureItem
from docling_core.types.doc.items.table.table import TableItem
from docling_core.types.doc.items.text import SectionHeaderItem, TextItem

from research_agent.ingestion.contracts import (
    BoundingBox,
    ParsedDocument,
    ParsedElement,
    ParsedImage,
)


class DoclingDocumentParser:
    name = "docling"
    version = package_version("docling")

    def __init__(self) -> None:
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = False
        pipeline_options.do_table_structure = True
        pipeline_options.generate_picture_images = True
        self._pipeline_options = pipeline_options
        self._converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            },
        )

    def parse(self, source: Path) -> ParsedDocument:
        document = self._converter.convert(source).document
        elements: list[ParsedElement] = []
        images: list[ParsedImage] = []
        warnings: list[str] = []
        section_stack: list[str] = []

        for item, _level in document.iterate_items():
            if not isinstance(item, (TextItem, TableItem, PictureItem)):
                continue
            element_id = item.self_ref
            if isinstance(item, SectionHeaderItem):
                header_level = max(item.level, 1)
                section_stack = section_stack[: header_level - 1]
                section_stack.append(item.text)
                text = item.text
                kind = "section_header"
            elif isinstance(item, TableItem):
                text = item.export_to_markdown(document)
                kind = "table"
            elif isinstance(item, PictureItem):
                text = item.caption_text(document)
                kind = "picture"
            else:
                text = item.text
                kind = item.label.value

            # Emit every provenance entry so multi-page tables and pictures keep
            # their full source trace; only text uses char spans for per-page text.
            # 输出每条溯源记录以保留跨页表格和图片的完整来源；仅文本按字符区间拆分内容。
            provenance_count = max(len(item.prov), 1)
            for provenance_index in range(provenance_count):
                normalized_id = (
                    element_id
                    if provenance_index == 0
                    else f"{element_id}@prov-{provenance_index}"
                )
                normalized_text = text
                if isinstance(item, TextItem) and provenance_count > 1:
                    start, end = item.prov[provenance_index].charspan
                    normalized_text = item.text[start:end]
                page_number, bbox = _provenance(item, document, provenance_index)
                if page_number == 0:
                    warnings.append(f"missing provenance for {normalized_id}")
                elements.append(
                    ParsedElement(
                        element_id=normalized_id,
                        kind=kind,
                        text=normalized_text,
                        section_path=tuple(section_stack),
                        page_number=page_number,
                        bbox=bbox,
                    )
                )

            if isinstance(item, PictureItem):
                image = item.get_image(document)
                if image is None:
                    warnings.append(f"missing image data for {element_id}")
                    continue
                output = BytesIO()
                image.save(output, format="PNG")
                images.append(
                    ParsedImage(
                        image_id=element_id,
                        content=output.getvalue(),
                        content_type="image/png",
                    )
                )

        json_bytes = json.dumps(
            document.export_to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        markdown = document.export_to_markdown(image_placeholder="<!-- image -->")
        return ParsedDocument(
            markdown=markdown,
            json_bytes=json_bytes,
            elements=elements,
            images=images,
            page_count=len(document.pages),
            warnings=tuple(warnings),
        )


def _provenance(
    item: DocItem, document: DoclingDocument, provenance_index: int = 0
) -> tuple[int, BoundingBox | None]:
    if len(item.prov) <= provenance_index:
        return 0, None
    provenance = item.prov[provenance_index]
    page_number = provenance.page_no
    page = document.pages.get(page_number)
    if page is None:
        return 0, None
    box = provenance.bbox.to_top_left_origin(page.size.height)
    return page_number, BoundingBox(box.l, box.t, box.r, box.b)
