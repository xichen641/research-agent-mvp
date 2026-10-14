"""Import a fixed local corpus through the existing HTTP upload pipeline."""

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, Field, ValidationError

from research_agent.evaluation.corpus import (
    CorpusManifest,
    CorpusPaper,
    check_corpus,
    load_corpus_manifest,
)
from research_agent.papers.quality import DocumentQualityResponse


class UploadResponse(BaseModel):
    document_id: UUID
    version_id: UUID
    task_id: UUID
    duplicate: bool


class TaskSnapshot(BaseModel):
    id: UUID
    document_version_id: UUID
    status: Literal["QUEUED", "RUNNING", "READY", "FAILED", "CANCELLED"]
    stage: str
    progress_current: int
    progress_total: int
    error_code: str | None = None
    error_message: str | None = None


class ImportItem(BaseModel):
    paper_id: str
    display_name: str
    file_name: str
    sha256: str
    local_status: str
    import_result: Literal["imported", "duplicate", "not_imported", "unknown"] = (
        "not_imported"
    )
    state: Literal[
        "ready", "processing", "needs_review", "failed", "unknown", "local_error"
    ]
    document_id: UUID | None = None
    version_id: UUID | None = None
    task_id: UUID | None = None
    task: TaskSnapshot | None = None
    document_quality: DocumentQualityResponse | None = None
    error_code: str | None = None
    issues: list[str] = Field(default_factory=list)


class ImportReport(BaseModel):
    schema_version: Literal[1] = 1
    run_id: UUID = Field(default_factory=uuid4)
    corpus_id: str
    corpus_version: int
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    summary: dict[str, int] = Field(default_factory=dict)
    unlisted_files: list[str]
    papers: list[ImportItem]


def _checkpoint(report: ImportReport, callback: Callable[[ImportReport], None]) -> None:
    report.updated_at = datetime.now(UTC)
    report.summary = {
        **{
            key: sum(item.import_result == key for item in report.papers)
            for key in ("imported", "duplicate", "not_imported")
        },
        **{
            key: sum(item.state == key for item in report.papers)
            for key in (
                "ready",
                "processing",
                "needs_review",
                "failed",
                "unknown",
                "local_error",
            )
        },
    }
    callback(report)


def import_corpus(
    manifest: CorpusManifest,
    *,
    root: Path,
    client: httpx.Client,
    wait_seconds: float = 0,
    poll_interval: float = 2,
    on_update: Callable[[ImportReport], None] = lambda report: None,
) -> ImportReport:
    if not math.isfinite(wait_seconds) or wait_seconds < 0:
        raise ValueError("wait_seconds must be finite and non-negative")
    if not math.isfinite(poll_interval) or poll_interval <= 0:
        raise ValueError("poll_interval must be finite and positive")
    preflight = check_corpus(manifest, root=root)
    report = ImportReport(
        corpus_id=manifest.corpus_id,
        corpus_version=manifest.corpus_version,
        unlisted_files=preflight.unlisted_files,
        papers=[
            ImportItem(
                paper_id=check.paper_id,
                display_name=check.display_name,
                file_name=check.file_name,
                sha256=paper.sha256,
                local_status=check.status,
                state="processing" if check.status == "matched" else "local_error",
                issues=list(check.issues),
            )
            for paper, check in zip(manifest.papers, preflight.papers, strict=True)
        ],
    )
    _checkpoint(report, on_update)
    for paper, item in zip(manifest.papers, report.papers, strict=True):
        if item.local_status == "matched":
            _upload(paper, item, root=root, manifest=manifest, client=client)
            _checkpoint(report, on_update)
    deadline = time.monotonic() + wait_seconds
    while True:
        for paper, item in zip(manifest.papers, report.papers, strict=True):
            if item.document_id is not None and item.state in ("processing", "unknown"):
                _refresh(item, paper=paper, client=client)
                _checkpoint(report, on_update)
        pending = any(
            item.document_id is not None and item.state in ("processing", "unknown")
            for item in report.papers
        )
        remaining = deadline - time.monotonic()
        if not pending or remaining <= 0:
            break
        time.sleep(min(poll_interval, remaining))
    report.finished_at = datetime.now(UTC)
    _checkpoint(report, on_update)
    return report


def _upload(
    paper: CorpusPaper,
    item: ImportItem,
    *,
    root: Path,
    manifest: CorpusManifest,
    client: httpx.Client,
) -> None:
    path = (root / manifest.dataset_root / paper.file_name).resolve()
    try:
        if not path.is_relative_to((root / manifest.dataset_root).resolve()):
            raise ValueError("PDF resolves outside dataset_root")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != paper.sha256:
                raise ValueError("PDF changed after local verification")
            stream.seek(0)
            response = client.post(
                "documents",
                files={"file": (paper.file_name, stream, "application/pdf")},
            )
        if response.is_error:
            item.error_code = f"HTTP_{response.status_code}"
            if response.is_server_error:
                item.import_result, item.state = "unknown", "unknown"
                item.issues = [
                    f"Upload outcome unknown (HTTP {response.status_code}); rerun via deduplication"
                ]
            else:
                item.state = "failed"
                item.issues = [f"Upload rejected (HTTP {response.status_code})"]
            return
        receipt = UploadResponse.model_validate(response.json())
        item.document_id, item.version_id, item.task_id = (
            receipt.document_id,
            receipt.version_id,
            receipt.task_id,
        )
        item.import_result = "duplicate" if receipt.duplicate else "imported"
    except (OSError, ValueError) as error:
        # A malformed response can follow a committed upload; its outcome is unknown.
        if isinstance(error, (ValidationError, json.JSONDecodeError)):
            item.import_result, item.state = "unknown", "unknown"
            item.error_code = "UPLOAD_OUTCOME_UNKNOWN"
            item.issues = [
                "Invalid upload response; rerun to resolve via SHA-256 deduplication"
            ]
        else:
            item.state, item.local_status = "local_error", "changed"
            item.issues = ["Local PDF could not be read or changed after verification"]
    except httpx.RequestError:
        item.import_result, item.state = "unknown", "unknown"
        item.error_code = "UPLOAD_OUTCOME_UNKNOWN"
        item.issues = [
            "Upload response unavailable; rerun to resolve via SHA-256 deduplication"
        ]


def _refresh(item: ImportItem, *, paper: CorpusPaper, client: httpx.Client) -> None:
    try:
        task_response = client.get(f"tasks/{item.task_id}")
        task_response.raise_for_status()
        task = TaskSnapshot.model_validate(task_response.json())
        if task.id != item.task_id or task.document_version_id != item.version_id:
            raise ValueError("task identity mismatch")
        item.task = task
        quality_response = client.get(f"documents/{item.document_id}/quality")
        quality_response.raise_for_status()
        quality = DocumentQualityResponse.model_validate(quality_response.json())
        if (
            quality.document_id != item.document_id
            or quality.version_id != item.version_id
            or quality.source_sha256 != paper.sha256
        ):
            raise ValueError("document version or source identity mismatch")
        item.document_quality = quality
        item.error_code, item.issues = None, []
        if task.status == "FAILED" and task.error_code == "PARSE_QUALITY_REVIEW":
            item.state = "needs_review"
            item.error_code = task.error_code
            item.issues = (
                list(quality.quality.warnings)
                if quality.quality
                else ["Parse requires review"]
            )
        elif task.status in ("FAILED", "CANCELLED"):
            item.state = "failed"
            item.error_code = task.error_code or task.status
            item.issues = [task.error_message or f"Ingestion {task.status.lower()}"]
        elif quality.status == "NEEDS_REVIEW":
            item.state = "needs_review"
            item.issues = (
                list(quality.quality.warnings)
                if quality.quality
                else ["Parse requires review"]
            )
        elif task.status != "READY":
            item.state = "processing"
        elif quality.status != "READY" or quality.quality is None:
            item.state = "needs_review"
            item.issues = [
                "Completed task has no usable quality report or version is not ready"
            ]
        elif quality.quality.total_pages != paper.page_count:
            item.state = "needs_review"
            item.issues = ["Parsed page count differs from the fixed corpus"]
        elif (
            quality.quality.warnings
            or quality.quality.empty_pages
            or quality.quality.parsed_pages < quality.quality.total_pages
            or quality.quality.body_characters < quality.quality.parsed_pages * 100
        ):
            item.state = "needs_review"
            item.issues = list(quality.quality.warnings) or [
                "Incomplete page coverage or insufficient body text"
            ]
        else:
            item.state = "ready"
    except (httpx.HTTPError, ValueError):
        if item.task is not None and item.task.status in ("FAILED", "CANCELLED"):
            item.state = (
                "needs_review"
                if item.task.error_code == "PARSE_QUALITY_REVIEW"
                else "failed"
            )
            item.error_code = item.task.error_code or item.task.status
            item.issues = [
                item.task.error_message or "Ingestion failed",
                "Quality data unavailable; rerun to refresh",
            ]
        else:
            item.state, item.error_code = "unknown", "STATUS_UNAVAILABLE"
            item.issues = [
                "Task/quality status unavailable or inconsistent; rerun to refresh"
            ]


def _save_report(report: ImportReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(report.model_dump_json(indent=2) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    repository_root = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repository_root)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repository_root / "evaluation/corpora/reid-v1.json",
    )
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--output", type=Path, default=repository_root / ".tmp/reid-import-report.json"
    )
    parser.add_argument("--wait-seconds", type=float, default=0)
    parser.add_argument("--poll-interval", type=float, default=2)
    args = parser.parse_args(argv)
    try:
        manifest = load_corpus_manifest(args.manifest)
        address = urlsplit(args.api_url)
        if (
            address.scheme not in ("http", "https")
            or not address.hostname
            or address.username is not None
            or address.password is not None
            or address.query
            or address.fragment
            or address.path not in ("", "/")
        ):
            raise ValueError(
                "api-url must be an HTTP(S) origin without credentials or query"
            )
        output = args.output.resolve()
        if output == args.manifest.resolve() or output.suffix.lower() == ".pdf":
            raise ValueError("report output must not overwrite the manifest or a PDF")
        last_progress: dict[str, tuple[str, str, str | None]] = {}

        def checkpoint(report: ImportReport) -> None:
            _save_report(report, output)
            for item in report.papers:
                progress = (
                    item.import_result,
                    item.state,
                    item.task.stage if item.task else None,
                )
                if last_progress.get(item.paper_id) != progress:
                    print(
                        f"{item.display_name}: import={progress[0]}, state={progress[1]}, stage={progress[2] or '-'}",
                        file=sys.stderr,
                        flush=True,
                    )
                    last_progress[item.paper_id] = progress

        with httpx.Client(
            base_url=args.api_url.rstrip("/") + "/api/v1/",
            timeout=60,
            follow_redirects=False,
        ) as client:
            report = import_corpus(
                manifest,
                root=args.root,
                client=client,
                wait_seconds=args.wait_seconds,
                poll_interval=args.poll_interval,
                on_update=checkpoint,
            )
        print(report.model_dump_json(indent=2))
        if report.unlisted_files or any(
            item.state not in ("ready", "processing") for item in report.papers
        ):
            return 1
        return 3 if any(item.state == "processing" for item in report.papers) else 0
    except KeyboardInterrupt:
        print(
            "Import interrupted; report retains received IDs. Rerun to resolve via deduplication.",
            file=sys.stderr,
        )
        return 130
    except (OSError, ValueError, httpx.InvalidURL) as error:
        print(f"corpus import error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
