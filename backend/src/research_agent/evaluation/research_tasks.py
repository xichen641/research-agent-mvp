"""Fixed research tasks, source checks, and explicit human review results."""

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import pypdfium2 as pdfium
from pydantic import Field, PositiveInt, model_validator

from research_agent.evaluation.corpus import (
    CorpusManifest,
    ManifestModel,
    Sha256,
    check_corpus,
    load_corpus_manifest,
)

Category = Literal[
    "method_understanding",
    "paper_comparison",
    "idea_critique",
    "experiment_design",
    "memory_correction",
]
NonEmptyString = Annotated[str, Field(min_length=1, pattern=r"\S")]
CATEGORIES = (
    "method_understanding",
    "paper_comparison",
    "idea_critique",
    "experiment_design",
    "memory_correction",
)
DIMENSIONS = (
    "factual_grounding",
    "task_understanding",
    "analysis_boundaries",
    "actionability",
    "context_continuity",
)


class TaskEvidence(ManifestModel):
    source_id: NonEmptyString
    paper_id: NonEmptyString
    sha256: Sha256
    material_version: NonEmptyString
    pdf_page: PositiveInt
    locator: NonEmptyString
    excerpt: NonEmptyString

    @model_validator(mode="after")
    def validate_excerpt(self) -> "TaskEvidence":
        if not normalize_source_text(self.excerpt):
            raise ValueError("excerpt must contain readable source text")
        return self


class FactCheck(ManifestModel):
    fact_id: NonEmptyString
    claim: NonEmptyString
    source_ids: Annotated[list[NonEmptyString], Field(min_length=1)]
    acceptable_answers: Annotated[list[NonEmptyString], Field(min_length=1)]


class ScenarioTurn(ManifestModel):
    turn_id: NonEmptyString
    space_key: NonEmptyString
    session_key: NonEmptyString
    user_message: NonEmptyString
    before_turn_requirements: list[NonEmptyString] = Field(default_factory=list)
    checks: Annotated[list[NonEmptyString], Field(min_length=1)]


class ResearchTask(ManifestModel):
    schema_version: Literal[1]
    dataset_version: Literal[1]
    task_id: Annotated[str, Field(pattern=r"^REID-[MCHER][0-9]{2}$")]
    category: Category
    kind: Literal["paper_fact", "open_research", "conversation"]
    scenario_origin: Literal["synthetic_fixture"]
    corpus_id: NonEmptyString
    corpus_version: PositiveInt
    title: NonEmptyString
    paper_scope: list[NonEmptyString]
    prompt: NonEmptyString
    sources: list[TaskEvidence]
    fact_checks: list[FactCheck]
    required_analysis: Annotated[list[NonEmptyString], Field(min_length=1)]
    error_boundaries: Annotated[list[NonEmptyString], Field(min_length=1)]
    next_step_requirements: Annotated[list[NonEmptyString], Field(min_length=1)]
    scoring_focus: dict[str, NonEmptyString]
    execution_requirements: list[NonEmptyString]
    turns: list[ScenarioTurn]

    @model_validator(mode="after")
    def validate_references(self) -> "ResearchTask":
        _unique(self.paper_scope, "paper_scope")
        source_ids = [source.source_id for source in self.sources]
        _unique(source_ids, "source_id")
        _unique([fact.fact_id for fact in self.fact_checks], "fact_id")
        _unique([turn.turn_id for turn in self.turns], "turn_id")
        if set(self.scoring_focus) != set(DIMENSIONS):
            raise ValueError(
                "scoring_focus must contain exactly five review dimensions"
            )
        if any(source.paper_id not in self.paper_scope for source in self.sources):
            raise ValueError("source paper is outside paper_scope")
        if any(
            not set(fact.source_ids) <= set(source_ids) for fact in self.fact_checks
        ):
            raise ValueError("fact check references an undefined source")
        if self.kind == "paper_fact" and not self.fact_checks:
            raise ValueError("paper fact tasks require sourced fact checks")
        if self.category == "paper_comparison" and len(self.paper_scope) < 2:
            raise ValueError("comparison tasks require at least two papers")
        if self.category == "memory_correction" and (
            self.kind != "conversation" or len(self.turns) < 3
        ):
            raise ValueError(
                "memory tasks require a conversation with at least three turns"
            )
        if self.kind != "conversation" and self.turns:
            raise ValueError("single-turn tasks must not contain scenario turns")
        return self


def _unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label}")


def load_research_tasks(path: Path) -> list[ResearchTask]:
    tasks = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            tasks.append(ResearchTask.model_validate_json(line))
        except ValueError as error:
            raise ValueError(
                f"invalid research task at line {line_number}: {error}"
            ) from error
    _unique([task.task_id for task in tasks], "task_id")
    counts = Counter(task.category for task in tasks)
    if len(tasks) < 20 or any(counts[category] < 4 for category in CATEGORIES):
        raise ValueError(
            "dataset requires at least 20 tasks and four in every category"
        )
    return tasks


def normalize_source_text(text: str) -> str:
    """Normalize typography and PDF word wrapping, without rephrasing evidence."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u00ad\ufffe]\s*", "", text)
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    text = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", "", text)
    text = text.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'}))
    return re.sub(r"\s+", " ", text).strip()


def validate_task_sources(
    tasks: list[ResearchTask],
    manifest: CorpusManifest,
    *,
    root: Path,
    structure_only: bool = False,
) -> dict[str, object]:
    papers = {paper.paper_id: paper for paper in manifest.papers}
    for task in tasks:
        if (task.corpus_id, task.corpus_version) != (
            manifest.corpus_id,
            manifest.corpus_version,
        ):
            raise ValueError(f"{task.task_id}: corpus identity/version mismatch")
        if not set(task.paper_scope) <= set(papers):
            raise ValueError(f"{task.task_id}: unknown scoped paper")
        for source in task.sources:
            paper = papers[source.paper_id]
            if (
                source.sha256 != paper.sha256
                or source.material_version != paper.material_version.identifier
            ):
                raise ValueError(
                    f"{task.task_id}/{source.source_id}: material version mismatch"
                )
            if source.pdf_page > paper.page_count:
                raise ValueError(
                    f"{task.task_id}/{source.source_id}: PDF page outside material"
                )
    covered = {source.paper_id for task in tasks for source in task.sources}
    if covered != set(papers):
        raise ValueError("task sources must cover every fixed corpus paper")
    checked_pages: dict[tuple[str, int], str] = {}
    checked_excerpts = 0
    if not structure_only:
        corpus = check_corpus(manifest, root=root)
        if corpus.status != "passed":
            raise ValueError(
                "local corpus differs from manifest; run check-reid-corpus first"
            )
        documents: dict[str, pdfium.PdfDocument] = {}
        try:
            for task in tasks:
                for source in task.sources:
                    key = (source.paper_id, source.pdf_page)
                    if key not in checked_pages:
                        if source.paper_id not in documents:
                            documents[source.paper_id] = pdfium.PdfDocument(
                                root
                                / manifest.dataset_root
                                / papers[source.paper_id].file_name
                            )
                        page = documents[source.paper_id][source.pdf_page - 1]
                        text_page = page.get_textpage()
                        try:
                            checked_pages[key] = normalize_source_text(
                                text_page.get_text_range()
                            )
                        finally:
                            text_page.close()
                            page.close()
                    if normalize_source_text(source.excerpt) not in checked_pages[key]:
                        raise ValueError(
                            f"{task.task_id}/{source.source_id}: excerpt not found on PDF page {source.pdf_page}"
                        )
                    checked_excerpts += 1
        finally:
            for document in documents.values():
                document.close()
    return {
        "status": "passed",
        "validation_scope": "structure_only"
        if structure_only
        else "structure_and_pdf_sources",
        "task_count": len(tasks),
        "category_counts": dict(Counter(task.category for task in tasks)),
        "covered_papers": sorted(covered),
        "checked_pages": len(checked_pages),
        "checked_excerpt_references": checked_excerpts,
        "answer_quality_evaluated": False,
    }


class DimensionRating(ManifestModel):
    score: Annotated[int, Field(ge=0, le=2)] | None
    reason: str
    evidence_refs: list[NonEmptyString]


class HumanReview(ManifestModel):
    task_id: NonEmptyString
    task_sha256: Sha256
    rubric_version: Literal[1]
    run_id: str | None
    reviewer: str | None
    reviewed_at: datetime | None
    execution_status: Literal[
        "not_run", "completed", "capability_missing", "execution_failed"
    ]
    answer_refs: list[NonEmptyString]
    failure_reason: str | None
    ratings: dict[str, DimensionRating]
    blocking_issues: list[NonEmptyString]

    @model_validator(mode="after")
    def validate_completed_review(self) -> "HumanReview":
        if set(self.ratings) != set(DIMENSIONS):
            raise ValueError("ratings must contain exactly five review dimensions")
        if self.execution_status == "completed":
            if not (
                self.run_id
                and self.run_id.strip()
                and self.reviewer
                and self.reviewer.strip()
                and self.reviewed_at
                and self.answer_refs
            ):
                raise ValueError(
                    "completed reviews require run, reviewer, timestamp and actual answer references"
                )
            if self.reviewed_at.tzinfo is None:
                raise ValueError("review timestamp must include timezone")
            if any(
                rating.score is None or not rating.reason.strip()
                for rating in self.ratings.values()
            ):
                raise ValueError("completed reviews require five scores and reasons")
        elif any(rating.score is not None for rating in self.ratings.values()):
            raise ValueError("unexecuted tasks must not have fabricated answer scores")
        if self.execution_status in ("capability_missing", "execution_failed") and not (
            self.failure_reason and self.failure_reason.strip()
        ):
            raise ValueError("failed execution requires a reason")
        return self


def task_fingerprint(task: ResearchTask) -> str:
    encoded = json.dumps(
        task.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def review_template(tasks: list[ResearchTask]) -> list[HumanReview]:
    return [
        HumanReview(
            task_id=task.task_id,
            task_sha256=task_fingerprint(task),
            rubric_version=1,
            run_id=None,
            reviewer=None,
            reviewed_at=None,
            execution_status="not_run",
            answer_refs=[],
            failure_reason=None,
            ratings={
                name: DimensionRating(score=None, reason="", evidence_refs=[])
                for name in DIMENSIONS
            },
            blocking_issues=[],
        )
        for task in tasks
    ]


def summarize_reviews(tasks: list[ResearchTask], path: Path) -> dict[str, object]:
    reviews = [
        HumanReview.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    _unique([review.task_id for review in reviews], "review task_id")
    by_id = {task.task_id: task for task in tasks}
    if {review.task_id for review in reviews} != set(by_id):
        raise ValueError("reviews must contain exactly the dataset task IDs")
    results = []
    for review in reviews:
        task = by_id[review.task_id]
        if review.task_sha256 != task_fingerprint(task):
            raise ValueError(
                f"{review.task_id}: review belongs to a different task revision"
            )
        allowed_refs = {source.source_id for source in task.sources} | {
            f"turn:{turn.turn_id}" for turn in task.turns
        }
        if any(
            not set(rating.evidence_refs) <= allowed_refs
            for rating in review.ratings.values()
        ):
            raise ValueError(
                f"{review.task_id}: review cites an undefined evidence/turn reference"
            )
        total = None
        if review.execution_status == "not_run":
            decision = "pending"
        elif review.execution_status != "completed":
            decision = "failed"
        else:
            scores = [rating.score for rating in review.ratings.values()]
            total = sum(score for score in scores if score is not None)
            decision = (
                "passed"
                if not review.blocking_issues
                and all(score is not None and score >= 1 for score in scores)
                and total >= 8
                else "failed"
            )
        results.append(
            {
                "task_id": review.task_id,
                "decision": decision,
                "total_score": total,
                "blocking_issues": review.blocking_issues,
                "execution_status": review.execution_status,
            }
        )
    counts = Counter(result["decision"] for result in results)
    return {
        "status": "failed"
        if counts["failed"]
        else "pending"
        if counts["pending"]
        else "passed",
        "counts": dict(counts),
        "tasks": results,
    }


def main(argv: list[str] | None = None) -> int:
    repository_root = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=repository_root / "evaluation/datasets/reid_research_tasks.jsonl",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument(
        "--manifest",
        type=Path,
        default=repository_root / "evaluation/corpora/reid-v1.json",
    )
    validate.add_argument("--root", type=Path, default=repository_root)
    validate.add_argument("--structure-only", action="store_true")
    template = commands.add_parser("review-template")
    template.add_argument("--output", type=Path, required=True)
    review = commands.add_parser("review")
    review.add_argument("--reviews", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        tasks = load_research_tasks(args.dataset)
        if args.command == "validate":
            report = validate_task_sources(
                tasks,
                load_corpus_manifest(args.manifest),
                root=args.root,
                structure_only=args.structure_only,
            )
        elif args.command == "review-template":
            output = args.output.resolve()
            if (
                output == args.dataset.resolve()
                or output.suffix.lower() != ".jsonl"
                or output.exists()
            ):
                raise ValueError(
                    "review template requires a new .jsonl output; existing files are preserved"
                )
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as stream:
                for item in review_template(tasks):
                    stream.write(item.model_dump_json() + "\n")
            print(f"Created {len(tasks)} unscored review rows at {output}")
            return 0
        else:
            report = summarize_reviews(tasks, args.reviews)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return {"passed": 0, "failed": 1, "pending": 3}[str(report["status"])]
    except (OSError, ValueError, pdfium.PdfiumError) as error:
        print(f"research tasks error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
