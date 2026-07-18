from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationCase:
    case_id: str
    query: str
    relevant_ids: tuple[str, ...]
    retrieved_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must not be blank")
        if not self.query.strip():
            raise ValueError("query must not be blank")
        if not self.relevant_ids:
            raise ValueError("relevant_ids must not be empty")
        if len(set(self.relevant_ids)) != len(self.relevant_ids):
            raise ValueError("relevant_ids contains duplicates")
        if len(set(self.retrieved_ids)) != len(self.retrieved_ids):
            raise ValueError("retrieved_ids contains duplicates")


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    case_count: int
    recall_at_5: float
    recall_at_10: float
    mean_reciprocal_rank: float
