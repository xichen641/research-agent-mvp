from research_agent.chat.domain import QueryPlan
from research_agent.chat.graph import route_after_evidence_check


def test_insufficient_evidence_routes_to_clarification() -> None:
    state = {
        "plan": QueryPlan(
            intent="compare_methods",
            original_query="compare",
            lexical_queries=("compare",),
            semantic_query="compare",
        ),
        "evidence": [],
        "evidence_sufficient": False,
    }

    assert route_after_evidence_check(state) == "clarify"


def test_sufficient_evidence_routes_to_draft_generation() -> None:
    assert route_after_evidence_check({"evidence_sufficient": True}) == "generate_draft"
