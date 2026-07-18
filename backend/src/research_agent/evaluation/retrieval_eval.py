from research_agent.evaluation.domain import (
    RetrievalEvaluationCase,
    RetrievalMetrics,
)


def evaluate_retrieval(
    cases: list[RetrievalEvaluationCase],
) -> RetrievalMetrics:
    if not cases:
        raise ValueError("evaluation requires at least one case")

    recalls_at_5: list[float] = []
    recalls_at_10: list[float] = []
    reciprocal_ranks: list[float] = []
    for case in cases:
        relevant = set(case.relevant_ids)
        recalls_at_5.append(len(relevant & set(case.retrieved_ids[:5])) / len(relevant))
        recalls_at_10.append(
            len(relevant & set(case.retrieved_ids[:10])) / len(relevant)
        )
        reciprocal_ranks.append(
            next(
                (
                    1.0 / rank
                    for rank, item_id in enumerate(case.retrieved_ids, start=1)
                    if item_id in relevant
                ),
                0.0,
            )
        )

    case_count = len(cases)
    return RetrievalMetrics(
        case_count=case_count,
        recall_at_5=sum(recalls_at_5) / case_count,
        recall_at_10=sum(recalls_at_10) / case_count,
        mean_reciprocal_rank=sum(reciprocal_ranks) / case_count,
    )
