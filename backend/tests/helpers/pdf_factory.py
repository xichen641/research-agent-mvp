from pathlib import Path

from reportlab.pdfgen.canvas import Canvas


def create_two_page_pdf(destination: Path) -> tuple[str, str]:
    page_one_text = "ResearchAgent unique first page born digital text"
    page_two_text = "ResearchAgent unique second page born digital text"
    canvas = Canvas(str(destination))
    canvas.drawString(72, 720, page_one_text)
    canvas.showPage()
    canvas.drawString(72, 720, page_two_text)
    canvas.showPage()
    canvas.save()
    return page_one_text, page_two_text
