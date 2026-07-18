import json
from pathlib import Path
from typing import cast

from research_agent.evaluation.domain import RetrievalEvaluationCase


_EXPECTED_KEYS = {
    "case_id",
    "query",
    "relevant_ids",
    "retrieved_ids",
}


def load_retrieval_dataset(path: Path) -> list[RetrievalEvaluationCase]:
    cases: list[RetrievalEvaluationCase] = []
    with path.open(encoding="utf-8") as dataset:
        for line_number, line in enumerate(dataset, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid retrieval dataset JSON at line {line_number}"
                ) from error
            if not isinstance(value, dict) or set(value) != _EXPECTED_KEYS:
                raise ValueError(
                    f"invalid retrieval dataset schema at line {line_number}"
                )
            case_id = value["case_id"]
            query = value["query"]
            relevant_ids = value["relevant_ids"]
            retrieved_ids = value["retrieved_ids"]
            if (
                not isinstance(case_id, str)
                or not isinstance(query, str)
                or not _is_string_list(relevant_ids)
                or not _is_string_list(retrieved_ids)
            ):
                raise ValueError(
                    f"invalid retrieval dataset values at line {line_number}"
                )
            cases.append(
                RetrievalEvaluationCase(
                    case_id=case_id,
                    query=query,
                    relevant_ids=tuple(cast(list[str], relevant_ids)),
                    retrieved_ids=tuple(cast(list[str], retrieved_ids)),
                )
            )
    if not cases:
        raise ValueError("retrieval dataset must contain at least one case")
    return cases


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
