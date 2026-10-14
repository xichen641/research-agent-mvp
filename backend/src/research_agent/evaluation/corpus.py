"""Read-only verification of a reviewed, versioned local paper corpus."""

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, TypedDict

import pypdfium2 as pdfium
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

NonEmptyString = Annotated[str, Field(min_length=1)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MaterialVersion(ManifestModel):
    kind: Literal["arxiv_preprint", "cvf_open_access"]
    identifier: NonEmptyString
    date: date | None
    copyright_year_on_pdf: PositiveInt | None


class PaperSource(ManifestModel):
    url: Annotated[str, Field(pattern=r"^https://\S+$")]
    basis: Literal["pdf_watermark", "publisher_catalog_and_pdf"]
    original_download_url: str | None


class Publication(ManifestModel):
    status: Literal["verified", "not_independently_verified"]
    venue: str | None
    year: PositiveInt | None
    source_url: str | None


class MetadataReview(ManifestModel):
    pdf_page: PositiveInt
    checked_fields: Annotated[list[NonEmptyString], Field(min_length=1)]
    notes: NonEmptyString


class CorpusPaper(ManifestModel):
    paper_id: Annotated[str, Field(pattern=r"^[a-z0-9-]+$")]
    display_name: NonEmptyString
    file_name: NonEmptyString
    sha256: Sha256
    size_bytes: PositiveInt
    page_count: PositiveInt
    title: NonEmptyString
    authors: Annotated[list[NonEmptyString], Field(min_length=1)]
    material_version: MaterialVersion
    source: PaperSource
    publication: Publication
    metadata_review: MetadataReview

    @model_validator(mode="after")
    def validate_file_name(self) -> "CorpusPaper":
        if (
            PurePosixPath(self.file_name).name != self.file_name
            or "\\" in self.file_name
            or ":" in self.file_name
            or not self.file_name.lower().endswith(".pdf")
        ):
            raise ValueError("file_name must be a single PDF filename")
        if self.metadata_review.pdf_page > self.page_count:
            raise ValueError("metadata review page exceeds page_count")
        return self


class CorpusManifest(ManifestModel):
    schema_version: Literal[1]
    corpus_id: NonEmptyString
    corpus_version: PositiveInt
    reviewed_on: date
    dataset_root: NonEmptyString
    papers: Annotated[list[CorpusPaper], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_scope(self) -> "CorpusManifest":
        path = PurePosixPath(self.dataset_root)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in str(path)
            or ":" in str(path)
        ):
            raise ValueError("dataset_root must be a relative path without traversal")
        for field in ("paper_id", "file_name", "sha256"):
            values = [getattr(paper, field) for paper in self.papers]
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {field} in corpus manifest")
        return self


class PaperCheck(ManifestModel):
    paper_id: str
    display_name: str
    file_name: str
    status: Literal["matched", "missing", "changed", "unreadable"]
    actual_sha256: str | None = None
    actual_size_bytes: int | None = None
    actual_page_count: int | None = None
    issues: list[str] = Field(default_factory=list)


class CorpusReport(ManifestModel):
    corpus_id: str
    corpus_version: int
    status: Literal["passed", "failed"]
    expected_papers: int
    matched_papers: int
    matched_pages: int
    unlisted_files: list[str]
    papers: list[PaperCheck]


class _PaperIdentity(TypedDict):
    paper_id: str
    display_name: str
    file_name: str


def load_corpus_manifest(path: Path) -> CorpusManifest:
    return CorpusManifest.model_validate_json(path.read_text(encoding="utf-8"))


def check_corpus(manifest: CorpusManifest, *, root: Path) -> CorpusReport:
    """Check exact bytes and page counts without importing or modifying papers."""
    root = root.resolve()
    dataset_root = (root / manifest.dataset_root).resolve()
    if not dataset_root.is_relative_to(root):
        raise ValueError("dataset_root resolves outside the selected root")
    checks = [_check_paper(paper, dataset_root) for paper in manifest.papers]
    expected = {paper.file_name for paper in manifest.papers}
    unlisted = sorted(
        path.relative_to(dataset_root).as_posix()
        for path in dataset_root.rglob("*")
        if path.is_file()
        and path.suffix.lower() == ".pdf"
        and path.relative_to(dataset_root).as_posix() not in expected
    )
    matched = [check for check in checks if check.status == "matched"]
    return CorpusReport(
        corpus_id=manifest.corpus_id,
        corpus_version=manifest.corpus_version,
        status="passed" if len(matched) == len(checks) and not unlisted else "failed",
        expected_papers=len(checks),
        matched_papers=len(matched),
        matched_pages=sum(check.actual_page_count or 0 for check in matched),
        unlisted_files=unlisted,
        papers=checks,
    )


def _check_paper(paper: CorpusPaper, dataset_root: Path) -> PaperCheck:
    identity: _PaperIdentity = {
        "paper_id": paper.paper_id,
        "display_name": paper.display_name,
        "file_name": paper.file_name,
    }
    try:
        path = (dataset_root / paper.file_name).resolve()
        if not path.is_relative_to(dataset_root):
            raise ValueError("PDF resolves outside dataset_root")
        if not path.exists():
            return PaperCheck(**identity, status="missing", issues=["PDF is missing"])
        with path.open("rb") as stream:
            sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            size_bytes = os.fstat(stream.fileno()).st_size
            stream.seek(0)
            document = pdfium.PdfDocument(stream)
            try:
                page_count = len(document)
            finally:
                document.close()
        issues = []
        if sha256 != paper.sha256:
            issues.append("SHA-256 differs from the fixed material version")
        if size_bytes != paper.size_bytes:
            issues.append(f"size_bytes: expected {paper.size_bytes}, got {size_bytes}")
        if page_count != paper.page_count:
            issues.append(f"page_count: expected {paper.page_count}, got {page_count}")
        return PaperCheck(
            **identity,
            status="changed" if issues else "matched",
            actual_sha256=sha256,
            actual_size_bytes=size_bytes,
            actual_page_count=page_count,
            issues=issues,
        )
    except (OSError, ValueError, pdfium.PdfiumError) as error:
        return PaperCheck(**identity, status="unreadable", issues=[str(error)])


def main(argv: list[str] | None = None) -> int:
    repository_root = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repository_root)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repository_root / "evaluation/corpora/reid-v1.json",
    )
    parser.add_argument("--output", type=Path, help="Optional local JSON report path")
    args = parser.parse_args(argv)
    try:
        manifest = load_corpus_manifest(args.manifest)
        report = check_corpus(manifest, root=args.root)
        serialized = json.dumps(
            report.model_dump(mode="json"), ensure_ascii=False, indent=2
        )
        if args.output is not None:
            output = args.output.resolve()
            protected = [
                args.manifest.resolve(),
                *(
                    (args.root / manifest.dataset_root / paper.file_name).resolve()
                    for paper in manifest.papers
                ),
            ]
            if output in protected or output.suffix.lower() == ".pdf":
                raise ValueError(
                    "report output must not overwrite the manifest or a PDF"
                )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(serialized + "\n", encoding="utf-8")
    except (OSError, ValueError) as error:
        print(f"corpus check error: {error}", file=sys.stderr)
        return 2
    print(serialized)
    return 0 if report.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
