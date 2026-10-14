import hashlib
import json
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from reportlab.pdfgen.canvas import Canvas
from research_agent.evaluation.corpus import CorpusManifest, load_corpus_manifest
from research_agent.evaluation.research_tasks import (
    DIMENSIONS,
    HumanReview,
    ResearchTask,
    load_research_tasks,
    main,
    normalize_source_text,
    review_template,
    summarize_reviews,
    validate_task_sources,
)

REPOSITORY_ROOT = Path(__file__).parents[4]
DATASET = REPOSITORY_ROOT / "evaluation/datasets/reid_research_tasks.jsonl"


@pytest.fixture
def tasks() -> list[ResearchTask]:
    return load_research_tasks(DATASET)


@pytest.fixture
def synthetic_sources(
    tmp_path: Path,
    tasks: list[ResearchTask],
) -> tuple[CorpusManifest, list[ResearchTask]]:
    """Six public synthetic PDFs; no test depends on private paper files."""
    data = load_corpus_manifest(
        REPOSITORY_ROOT / "evaluation/corpora/reid-v1.json"
    ).model_dump(mode="json")
    folder = tmp_path / data["dataset_root"]
    folder.mkdir(parents=True)
    hashes = {}
    for paper in data["papers"]:
        name = paper["paper_id"]
        path = folder / f"{name}.pdf"
        excerpts = sorted(
            {
                source.excerpt
                for task in tasks
                for source in task.sources
                if source.paper_id == name
            }
        )
        canvas = Canvas(str(path))
        text = canvas.beginText(40, 750)
        text.setFont("Helvetica", 8)
        text.textLine(f"Synthetic source fixture for {name}")
        for excerpt in excerpts:
            for line in textwrap.wrap(excerpt, width=95, break_long_words=False):
                text.textLine(line)
        canvas.drawText(text)
        canvas.showPage()
        canvas.save()
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        paper.update(
            file_name=path.name,
            sha256=hashes[name],
            size_bytes=path.stat().st_size,
            page_count=1,
        )
    modified = []
    for task in tasks:
        item = task.model_dump(mode="json")
        for source in item["sources"]:
            source.update(sha256=hashes[source["paper_id"]], pdf_page=1)
        modified.append(ResearchTask.model_validate_json(json.dumps(item)))
    return CorpusManifest.model_validate_json(json.dumps(data)), modified


def _write_tasks(path: Path, items: list[ResearchTask]) -> None:
    path.write_text(
        "".join(task.model_dump_json() + "\n" for task in items), encoding="utf-8"
    )


def _completed_reviews(
    tasks: list[ResearchTask], scores: list[int]
) -> list[HumanReview]:
    records = []
    for template in review_template(tasks):
        data = template.model_dump(mode="json")
        data.update(
            run_id="unit-test-fixture",
            reviewer="synthetic-test-reviewer",
            reviewed_at=datetime.now(UTC).isoformat(),
            execution_status="completed",
            answer_refs=[f"test://answers/{template.task_id}"],
        )
        for dimension, score in zip(DIMENSIONS, scores, strict=True):
            data["ratings"][dimension].update(
                score=score,
                reason="Synthetic threshold test, not a real model evaluation",
            )
        records.append(HumanReview.model_validate_json(json.dumps(data)))
    return records


def _write_reviews(path: Path, items: list[HumanReview]) -> None:
    path.write_text(
        "".join(item.model_dump_json() + "\n" for item in items), encoding="utf-8"
    )


def test_checked_in_dataset_covers_five_categories_and_six_papers(
    tasks: list[ResearchTask],
) -> None:
    manifest = load_corpus_manifest(REPOSITORY_ROOT / "evaluation/corpora/reid-v1.json")
    result = validate_task_sources(
        tasks, manifest, root=REPOSITORY_ROOT, structure_only=True
    )
    assert result["task_count"] == 20
    category_counts = result["category_counts"]
    covered_papers = result["covered_papers"]
    assert isinstance(category_counts, dict)
    assert isinstance(covered_papers, list)
    assert set(category_counts.values()) == {4}
    assert len(covered_papers) == 6
    assert result["checked_pages"] == 0
    assert result["answer_quality_evaluated"] is False


@pytest.mark.parametrize(
    "problem", ["missing_task", "duplicate_task", "missing_category"]
)
def test_invalid_dataset_counts_or_identifiers_are_rejected(
    tmp_path: Path,
    tasks: list[ResearchTask],
    problem: str,
) -> None:
    records = [task.model_dump(mode="json") for task in tasks]
    if problem == "missing_task":
        records.pop()
    elif problem == "duplicate_task":
        records[-1]["task_id"] = records[-2]["task_id"]
    else:
        records[0]["category"] = "experiment_design"
    path = tmp_path / "invalid.jsonl"
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in records), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_research_tasks(path)


def test_fact_cannot_reference_missing_source(tasks: list[ResearchTask]) -> None:
    data = tasks[0].model_dump(mode="json")
    data["fact_checks"][0]["source_ids"] = ["INVENTED-SOURCE"]
    with pytest.raises(ValidationError, match="undefined source"):
        ResearchTask.model_validate_json(json.dumps(data))


def test_normalization_handles_pdf_wrapping_without_changing_numbers() -> None:
    assert normalize_source_text(
        "cross\ufffe\nmodal ’text’ 77"
    ) == normalize_source_text("cross-modal 'text' 77")
    assert normalize_source_text("0.95 and -5") != normalize_source_text("0.5 and 5")


def test_source_check_reads_real_pdf_text_from_synthetic_materials(
    tmp_path: Path,
    synthetic_sources: tuple[CorpusManifest, list[ResearchTask]],
) -> None:
    manifest, tasks = synthetic_sources
    result = validate_task_sources(tasks, manifest, root=tmp_path)
    assert result["checked_pages"] == 6
    checked_excerpts = result["checked_excerpt_references"]
    assert isinstance(checked_excerpts, int)
    assert checked_excerpts > 20
    assert result["validation_scope"] == "structure_and_pdf_sources"


@pytest.mark.parametrize(
    "problem", ["wrong_hash", "wrong_version", "wrong_page", "fabricated_excerpt"]
)
def test_changed_or_invented_evidence_is_rejected(
    tmp_path: Path,
    synthetic_sources: tuple[CorpusManifest, list[ResearchTask]],
    problem: str,
) -> None:
    manifest, tasks = synthetic_sources
    data = tasks[0].model_dump(mode="json")
    source = data["sources"][0]
    field, value = {
        "wrong_hash": ("sha256", "0" * 64),
        "wrong_version": ("material_version", "invented-version"),
        "wrong_page": ("pdf_page", 2),
        "fabricated_excerpt": (
            "excerpt",
            "An invented finding that is absent from this paper.",
        ),
    }[problem]
    source[field] = value
    tasks[0] = ResearchTask.model_validate_json(json.dumps(data))
    with pytest.raises(ValueError):
        validate_task_sources(tasks, manifest, root=tmp_path)


def test_blank_review_template_stays_pending_with_no_scores(
    tmp_path: Path,
    tasks: list[ResearchTask],
) -> None:
    template = review_template(tasks)
    assert all(
        rating.score is None
        for review in template
        for rating in review.ratings.values()
    )
    path = tmp_path / "reviews.jsonl"
    _write_reviews(path, template)
    result = summarize_reviews(tasks, path)
    assert result["status"] == "pending"
    assert result["counts"] == {"pending": 20}


@pytest.mark.parametrize(
    ("scores", "blocking", "expected"),
    [
        ([2, 2, 1, 2, 1], False, "passed"),
        ([0, 2, 2, 2, 2], False, "failed"),
        ([2, 2, 1, 1, 1], False, "failed"),
        ([2, 2, 2, 2, 2], True, "failed"),
    ],
)
def test_grading_requires_each_dimension_total_threshold_and_no_blocker(
    tmp_path: Path,
    tasks: list[ResearchTask],
    scores: list[int],
    blocking: bool,
    expected: str,
) -> None:
    reviews = _completed_reviews(tasks, scores)
    if blocking:
        data = reviews[0].model_dump(mode="json")
        data["blocking_issues"] = ["Synthetic fixture: fabricated citation"]
        reviews[0] = HumanReview.model_validate_json(json.dumps(data))
    path = tmp_path / "reviews.jsonl"
    _write_reviews(path, reviews)
    result = summarize_reviews(tasks, path)
    assert result["status"] == expected


@pytest.mark.parametrize("score", [3, -1, True, 1.5])
def test_invalid_human_score_is_rejected(
    tasks: list[ResearchTask], score: object
) -> None:
    data = _completed_reviews(tasks, [2] * 5)[0].model_dump(mode="json")
    data["ratings"][DIMENSIONS[0]]["score"] = score
    with pytest.raises(ValidationError):
        HumanReview.model_validate_json(json.dumps(data))


def test_completed_review_requires_answer_reference_and_score_reasons(
    tasks: list[ResearchTask],
) -> None:
    data = _completed_reviews(tasks, [2] * 5)[0].model_dump(mode="json")
    data["answer_refs"] = []
    with pytest.raises(ValidationError, match="answer references"):
        HumanReview.model_validate_json(json.dumps(data))
    data["answer_refs"] = ["test://answers/fixture"]
    data["ratings"][DIMENSIONS[0]]["reason"] = ""
    with pytest.raises(ValidationError, match="scores and reasons"):
        HumanReview.model_validate_json(json.dumps(data))


def test_old_scores_cannot_be_reused_after_task_changes(
    tmp_path: Path, tasks: list[ResearchTask]
) -> None:
    path = tmp_path / "reviews.jsonl"
    _write_reviews(path, _completed_reviews(tasks, [2] * 5))
    data = tasks[0].model_dump(mode="json")
    data["prompt"] += " This changes the task."
    tasks[0] = ResearchTask.model_validate_json(json.dumps(data))
    with pytest.raises(ValueError, match="different task revision"):
        summarize_reviews(tasks, path)


def test_missing_capability_is_failure_without_fabricated_scores(
    tmp_path: Path, tasks: list[ResearchTask]
) -> None:
    reviews = review_template(tasks)
    data = reviews[-1].model_dump(mode="json")
    data.update(
        execution_status="capability_missing",
        failure_reason="Synthetic fixture: research spaces unavailable",
    )
    reviews[-1] = HumanReview.model_validate_json(json.dumps(data))
    path = tmp_path / "reviews.jsonl"
    _write_reviews(path, reviews)
    result = summarize_reviews(tasks, path)
    assert result["status"] == "failed"
    results = result["tasks"]
    assert isinstance(results, list)
    assert results[-1]["total_score"] is None


def test_cli_preserves_existing_review_file_and_reports_pending(
    tmp_path: Path,
    tasks: list[ResearchTask],
) -> None:
    output = tmp_path / "reviews.jsonl"
    args = ["--dataset", str(DATASET), "review-template", "--output", str(output)]
    assert main(args) == 0
    before = output.read_bytes()
    assert main(args) == 2
    assert output.read_bytes() == before
    assert main(["--dataset", str(DATASET), "review", "--reviews", str(output)]) == 3
