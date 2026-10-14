import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from reportlab.pdfgen.canvas import Canvas
from research_agent.evaluation.corpus import check_corpus, load_corpus_manifest, main


def _write_pdf(path: Path, text: str) -> None:
    canvas = Canvas(str(path))
    canvas.drawString(72, 720, text)
    canvas.showPage()
    canvas.save()


@pytest.fixture
def corpus_files(tmp_path: Path) -> tuple[Path, Path]:
    """Use a synthetic PDF; tests never need the private research corpus."""
    source = Path(__file__).parents[4] / "evaluation/corpora/reid-v1.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    pdf = tmp_path / "datasets/Reid/paper.pdf"
    pdf.parent.mkdir(parents=True)
    _write_pdf(pdf, "Original research material")
    paper = data["papers"][0]
    paper.update(
        file_name=pdf.name,
        page_count=1,
        size_bytes=pdf.stat().st_size,
        sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
    )
    data["papers"] = [paper]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    return manifest, pdf


def test_matching_corpus_is_repeatable_and_does_not_modify_materials(
    tmp_path: Path, corpus_files: tuple[Path, Path]
) -> None:
    manifest_file, pdf = corpus_files
    original_pdf = pdf.read_bytes()
    original_manifest = manifest_file.read_bytes()
    manifest = load_corpus_manifest(manifest_file)

    first = check_corpus(manifest, root=tmp_path)
    second = check_corpus(manifest, root=tmp_path)

    assert first == second
    assert first.status == "passed"
    assert first.matched_papers == first.matched_pages == 1
    assert pdf.read_bytes() == original_pdf
    assert manifest_file.read_bytes() == original_manifest


def test_valid_replacement_pdf_is_rejected_even_with_the_same_page_count(
    tmp_path: Path, corpus_files: tuple[Path, Path]
) -> None:
    manifest_file, pdf = corpus_files
    _write_pdf(pdf, "Replaced research material")

    report = check_corpus(load_corpus_manifest(manifest_file), root=tmp_path)

    assert report.status == "failed"
    assert report.matched_papers == 0
    assert report.papers[0].status == "changed"
    assert report.papers[0].actual_page_count == 1
    assert any("SHA-256" in issue for issue in report.papers[0].issues)


def test_corrupt_pdf_is_reported_without_crashing(
    tmp_path: Path, corpus_files: tuple[Path, Path]
) -> None:
    manifest_file, pdf = corpus_files
    pdf.write_bytes(b"%PDF-invalid")

    report = check_corpus(load_corpus_manifest(manifest_file), root=tmp_path)

    assert report.status == "failed"
    assert report.papers[0].status == "unreadable"
    assert report.papers[0].issues


def test_missing_and_unlisted_pdfs_are_reported_including_nested_and_uppercase(
    tmp_path: Path, corpus_files: tuple[Path, Path]
) -> None:
    manifest_file, pdf = corpus_files
    pdf.rename(pdf.with_name("unexpected.PDF"))
    nested = pdf.parent / "nested"
    nested.mkdir()
    _write_pdf(nested / "another.pdf", "Unlisted paper")

    report = check_corpus(load_corpus_manifest(manifest_file), root=tmp_path)

    assert report.status == "failed"
    assert report.papers[0].status == "missing"
    assert report.unlisted_files == ["nested/another.pdf", "unexpected.PDF"]


def test_wrong_page_count_in_manifest_is_rejected(
    tmp_path: Path, corpus_files: tuple[Path, Path]
) -> None:
    manifest_file, _ = corpus_files
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    data["papers"][0]["page_count"] = 2
    manifest_file.write_text(json.dumps(data), encoding="utf-8")

    report = check_corpus(load_corpus_manifest(manifest_file), root=tmp_path)

    assert report.papers[0].status == "changed"
    assert report.papers[0].issues == ["page_count: expected 2, got 1"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_root", "../outside"),
        ("dataset_root", "D:/outside"),
        ("dataset_root", "/outside"),
        ("file_name", "../paper.pdf"),
        ("file_name", "nested\\paper.pdf"),
        ("sha256", "invalid-hash"),
    ],
)
def test_invalid_or_out_of_scope_manifest_is_rejected(
    corpus_files: tuple[Path, Path], field: str, value: str
) -> None:
    manifest_file, _ = corpus_files
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    target = data if field == "dataset_root" else data["papers"][0]
    target[field] = value
    manifest_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_corpus_manifest(manifest_file)


@pytest.mark.parametrize("duplicate_field", ["paper_id", "file_name", "sha256"])
def test_duplicate_identity_or_material_in_manifest_is_rejected(
    corpus_files: tuple[Path, Path], duplicate_field: str
) -> None:
    manifest_file, _ = corpus_files
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    second = dict(data["papers"][0])
    second.update(paper_id="second", file_name="second.pdf", sha256="0" * 64)
    second[duplicate_field] = data["papers"][0][duplicate_field]
    data["papers"].append(second)
    manifest_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValidationError, match=f"duplicate {duplicate_field}"):
        load_corpus_manifest(manifest_file)


def test_cli_saves_report_and_returns_nonzero_when_material_is_missing(
    tmp_path: Path, corpus_files: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_file, pdf = corpus_files
    report_path = tmp_path / "reports/check.json"
    arguments = ["--manifest", str(manifest_file), "--root", str(tmp_path)]

    assert main([*arguments, "--output", str(report_path)]) == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "passed"
    capsys.readouterr()
    pdf.unlink()

    assert main(arguments) == 1
    assert json.loads(capsys.readouterr().out)["papers"][0]["status"] == "missing"


@pytest.mark.parametrize("target", ["manifest", "pdf"])
def test_cli_cannot_overwrite_manifest_or_source_pdf(
    tmp_path: Path, corpus_files: tuple[Path, Path], target: str
) -> None:
    manifest_file, pdf = corpus_files
    output = manifest_file if target == "manifest" else pdf
    before = output.read_bytes()

    result = main(
        [
            "--manifest",
            str(manifest_file),
            "--root",
            str(tmp_path),
            "--output",
            str(output),
        ]
    )

    assert result == 2
    assert output.read_bytes() == before


def test_cli_reports_invalid_manifest_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = tmp_path / "invalid.json"
    manifest.write_text("{}", encoding="utf-8")

    assert main(["--manifest", str(manifest)]) == 2
    assert "corpus check error" in capsys.readouterr().err
