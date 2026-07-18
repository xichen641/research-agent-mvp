import pytest
from pathlib import Path

from research_agent.evaluation.dataset import load_retrieval_dataset
from research_agent.evaluation.domain import RetrievalEvaluationCase
from research_agent.evaluation.retrieval_eval import evaluate_retrieval


def test_retrieval_metrics_average_case_recall_and_reciprocal_rank() -> None:
    metrics = evaluate_retrieval(
        [
            RetrievalEvaluationCase(
                case_id="q1",
                query="first",
                relevant_ids=("a", "b"),
                retrieved_ids=("x", "b", "a"),
            ),
            RetrievalEvaluationCase(
                case_id="q2",
                query="second",
                relevant_ids=("c",),
                retrieved_ids=("c",),
            ),
        ]
    )

    assert metrics.case_count == 2
    assert metrics.recall_at_5 == 1.0
    assert metrics.recall_at_10 == 1.0
    assert metrics.mean_reciprocal_rank == pytest.approx(0.75)


def test_evaluation_case_rejects_empty_or_duplicate_identifiers() -> None:
    with pytest.raises(ValueError, match="relevant"):
        RetrievalEvaluationCase("q1", "query", (), ("a",))
    with pytest.raises(ValueError, match="duplicates"):
        RetrievalEvaluationCase("q1", "query", ("a", "a"), ("a",))


def test_evaluation_rejects_empty_dataset() -> None:
    with pytest.raises(ValueError, match="at least one"):
        evaluate_retrieval([])


def test_synthetic_dataset_reaches_recall_at_10_target() -> None:
    path = (
        Path(__file__).parents[4]
        / "evaluation"
        / "datasets"
        / "synthetic_retrieval.jsonl"
    )

    metrics = evaluate_retrieval(load_retrieval_dataset(path))

    assert metrics.recall_at_10 == 1.0
