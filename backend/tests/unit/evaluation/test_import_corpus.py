import hashlib
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from reportlab.pdfgen.canvas import Canvas
from research_agent.evaluation.corpus import CorpusManifest, load_corpus_manifest
from research_agent.evaluation.import_corpus import ImportReport, import_corpus, main


@pytest.fixture
def import_files(tmp_path: Path) -> tuple[Path, CorpusManifest]:
    source = Path(__file__).parents[4] / "evaluation/corpora/reid-v1.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    data["papers"] = data["papers"][:2]
    directory = tmp_path / data["dataset_root"]
    directory.mkdir(parents=True)
    for index, paper in enumerate(data["papers"], 1):
        pdf = directory / f"paper-{index}.pdf"
        canvas = Canvas(str(pdf))
        canvas.drawString(72, 720, f"Research material {index}")
        canvas.showPage()
        canvas.save()
        paper.update(
            file_name=pdf.name,
            page_count=1,
            size_bytes=pdf.stat().st_size,
            sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
        )
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, load_corpus_manifest(path)


class ImportAPI:
    """HTTP double with separate upload, task, and quality observations."""

    def __init__(self, manifest: CorpusManifest) -> None:
        self.manifest = manifest
        self.uploads: list[int] = []
        self.requests: list[str] = []
        self.seen: set[int] = set()
        self.task_status = "READY"
        self.task_error_code = "PARSE_PARSER_FAILED"
        self.version_status = "READY"
        self.quality_state = "available"
        self.warnings: list[str] = []
        self.quality_status_code = 200
        self.mismatch_version = False
        self.timeout_once = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if request.method == "POST":
            index = next(
                index
                for index in range(1, 3)
                if f'filename="paper-{index}.pdf"'.encode() in request.content
            )
            self.uploads.append(index)
            duplicate = index in self.seen
            self.seen.add(index)
            if self.timeout_once:
                self.timeout_once = False
                raise httpx.ReadTimeout(
                    "response lost after accepting upload", request=request
                )
            return httpx.Response(
                202,
                json={
                    "document_id": str(UUID(int=index)),
                    "version_id": str(UUID(int=10 + index)),
                    "task_id": str(UUID(int=20 + index)),
                    "duplicate": duplicate,
                },
            )
        if "/tasks/" in request.url.path:
            index = UUID(request.url.path.split("/")[-1]).int - 20
            return httpx.Response(
                200,
                json={
                    "id": str(UUID(int=20 + index)),
                    "document_version_id": str(UUID(int=10 + index)),
                    "status": self.task_status,
                    "stage": "VERIFYING",
                    "progress_current": 8,
                    "progress_total": 8,
                    "error_code": self.task_error_code
                    if self.task_status == "FAILED"
                    else None,
                    "error_message": "Source document could not be parsed"
                    if self.task_status == "FAILED"
                    else None,
                },
            )
        index = UUID(request.url.path.split("/")[-2]).int
        return httpx.Response(
            self.quality_status_code,
            json={
                "document_id": str(UUID(int=index)),
                "version_id": str(
                    UUID(int=99 if self.mismatch_version else 10 + index)
                ),
                "source_sha256": self.manifest.papers[index - 1].sha256,
                "status": self.version_status,
                "parser_name": "pdfium-text",
                "parser_version": "test",
                "pipeline_version": "1",
                "quality_state": self.quality_state,
                "quality": {
                    "total_pages": 1,
                    "parsed_pages": 1,
                    "body_characters": 500,
                    "empty_pages": [],
                    "section_count": 1,
                    "table_count": 0,
                    "image_count": 0,
                    "warnings": self.warnings,
                }
                if self.quality_state == "available"
                else None,
            },
        )


def test_repeat_import_uses_duplicate_receipts_and_retains_original_ids(
    tmp_path: Path, import_files: tuple[Path, CorpusManifest]
) -> None:
    _, manifest = import_files
    api = ImportAPI(manifest)
    with httpx.Client(
        transport=httpx.MockTransport(api), base_url="http://test/api/v1/"
    ) as client:
        first = import_corpus(manifest, root=tmp_path, client=client)
        second = import_corpus(manifest, root=tmp_path, client=client)
    assert first.summary["imported"] == first.summary["ready"] == 2
    assert second.summary["duplicate"] == second.summary["ready"] == 2
    assert [
        (item.document_id, item.version_id, item.task_id) for item in first.papers
    ] == [(item.document_id, item.version_id, item.task_id) for item in second.papers]


@pytest.mark.parametrize(
    ("task_status", "version_status", "quality_state", "warnings", "expected"),
    [
        ("RUNNING", "PROCESSING", "pending", [], "processing"),
        ("FAILED", "FAILED", "unavailable", [], "failed"),
        ("CANCELLED", "PROCESSING", "pending", [], "failed"),
        (
            "RUNNING",
            "NEEDS_REVIEW",
            "available",
            ["low parsed page coverage"],
            "needs_review",
        ),
        ("READY", "READY", "unavailable", [], "needs_review"),
        ("READY", "READY", "available", ["uncertain table"], "needs_review"),
    ],
)
def test_processing_failure_and_review_are_not_reported_as_ready(
    tmp_path: Path,
    import_files: tuple[Path, CorpusManifest],
    task_status: str,
    version_status: str,
    quality_state: str,
    warnings: list[str],
    expected: str,
) -> None:
    _, manifest = import_files
    api = ImportAPI(manifest)
    api.task_status, api.version_status = task_status, version_status
    api.quality_state, api.warnings = quality_state, warnings
    with httpx.Client(
        transport=httpx.MockTransport(api), base_url="http://test/api/v1/"
    ) as client:
        report = import_corpus(manifest, root=tmp_path, client=client)
    assert {item.state for item in report.papers} == {expected}


def test_missing_file_is_skipped_and_remaining_material_is_imported(
    tmp_path: Path, import_files: tuple[Path, CorpusManifest]
) -> None:
    _, manifest = import_files
    (tmp_path / manifest.dataset_root / manifest.papers[0].file_name).unlink()
    api = ImportAPI(manifest)
    with httpx.Client(
        transport=httpx.MockTransport(api), base_url="http://test/api/v1/"
    ) as client:
        report = import_corpus(manifest, root=tmp_path, client=client)
    assert report.papers[0].state == "local_error"
    assert report.papers[0].local_status == "missing"
    assert report.papers[1].state == "ready"
    assert api.uploads == [2]


def test_lost_upload_response_is_unknown_and_rerun_recovers_original_upload(
    tmp_path: Path, import_files: tuple[Path, CorpusManifest]
) -> None:
    _, manifest = import_files
    api = ImportAPI(manifest)
    api.timeout_once = True
    with httpx.Client(
        transport=httpx.MockTransport(api), base_url="http://test/api/v1/"
    ) as client:
        first = import_corpus(manifest, root=tmp_path, client=client)
        second = import_corpus(manifest, root=tmp_path, client=client)
    assert first.papers[0].import_result == first.papers[0].state == "unknown"
    assert first.papers[0].error_code == "UPLOAD_OUTCOME_UNKNOWN"
    assert second.summary["duplicate"] == second.summary["ready"] == 2


@pytest.mark.parametrize("problem", ["missing_endpoint", "version_changed"])
def test_unavailable_quality_or_changed_version_cannot_claim_success(
    tmp_path: Path,
    import_files: tuple[Path, CorpusManifest],
    problem: str,
) -> None:
    _, manifest = import_files
    api = ImportAPI(manifest)
    api.quality_status_code = 404 if problem == "missing_endpoint" else 200
    api.mismatch_version = problem == "version_changed"
    with httpx.Client(
        transport=httpx.MockTransport(api), base_url="http://test/api/v1/"
    ) as client:
        report = import_corpus(manifest, root=tmp_path, client=client)
    assert report.summary["unknown"] == 2
    assert report.summary["ready"] == 0


def test_polling_refreshes_task_without_resending_uploads(
    tmp_path: Path,
    import_files: tuple[Path, CorpusManifest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, manifest = import_files
    api = ImportAPI(manifest)
    api.task_status, api.version_status = "RUNNING", "PROCESSING"

    def complete(seconds: float) -> None:
        api.task_status, api.version_status = "READY", "READY"

    monkeypatch.setattr("research_agent.evaluation.import_corpus.time.sleep", complete)
    snapshots = []

    def on_update(report: ImportReport) -> None:
        snapshots.append(report.model_copy(deep=True))

    with httpx.Client(
        transport=httpx.MockTransport(api), base_url="http://test/api/v1/"
    ) as client:
        report = import_corpus(
            manifest, root=tmp_path, client=client, wait_seconds=1, on_update=on_update
        )
    assert report.summary["ready"] == 2
    assert api.uploads == [1, 2]
    assert any(snapshot.summary.get("processing") == 2 for snapshot in snapshots)


@pytest.mark.parametrize("output_target", ["manifest", "pdf"])
def test_cli_rejects_overwriting_source_before_upload(
    tmp_path: Path,
    import_files: tuple[Path, CorpusManifest],
    output_target: str,
) -> None:
    path, manifest = import_files
    target = (
        path
        if output_target == "manifest"
        else tmp_path / manifest.dataset_root / manifest.papers[0].file_name
    )
    before = target.read_bytes()
    assert (
        main(
            ["--root", str(tmp_path), "--manifest", str(path), "--output", str(target)]
        )
        == 2
    )
    assert target.read_bytes() == before


@pytest.mark.parametrize(
    "address",
    [
        "http://user:secret@test",
        "http://test?token=secret",
        "file:///tmp/data",
        "http://test/api/v1",
    ],
)
def test_cli_rejects_invalid_or_sensitive_api_origins(
    import_files: tuple[Path, CorpusManifest],
    address: str,
) -> None:
    path, _ = import_files
    assert main(["--manifest", str(path), "--api-url", address]) == 2


def test_parse_quality_review_failure_is_presented_as_needing_review(
    tmp_path: Path,
    import_files: tuple[Path, CorpusManifest],
) -> None:
    _, manifest = import_files
    api = ImportAPI(manifest)
    api.task_status, api.version_status = "FAILED", "NEEDS_REVIEW"
    api.task_error_code = "PARSE_QUALITY_REVIEW"
    api.warnings = ["low parsed page coverage"]
    with httpx.Client(
        transport=httpx.MockTransport(api), base_url="http://test/api/v1/"
    ) as client:
        report = import_corpus(manifest, root=tmp_path, client=client)
    assert report.summary["needs_review"] == 2
    assert report.summary["failed"] == 0
    assert report.papers[0].issues == ["low parsed page coverage"]


@pytest.mark.parametrize(
    ("task_status", "exit_code"), [("READY", 0), ("RUNNING", 3), ("FAILED", 1)]
)
def test_cli_saves_report_with_correct_exit_status(
    tmp_path: Path,
    import_files: tuple[Path, CorpusManifest],
    monkeypatch: pytest.MonkeyPatch,
    task_status: str,
    exit_code: int,
) -> None:
    path, manifest = import_files
    api = ImportAPI(manifest)
    api.task_status = task_status
    api.version_status = "READY" if task_status == "READY" else "PROCESSING"
    client = httpx.Client(
        transport=httpx.MockTransport(api), base_url="http://test/api/v1/"
    )
    monkeypatch.setattr(
        "research_agent.evaluation.import_corpus.httpx.Client", lambda **kwargs: client
    )
    output = tmp_path / "reports/import.json"
    result = main(
        ["--manifest", str(path), "--root", str(tmp_path), "--output", str(output)]
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert result == exit_code
    assert report["finished_at"] is not None
    assert len(report["papers"]) == 2
    assert len(api.uploads) == 2
