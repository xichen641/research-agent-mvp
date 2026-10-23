from research_agent.evaluation.baseline_report import overview, render_overview


def test_service_warning_and_missing_capability_do_not_become_quality_passes() -> None:
    report = {
        "run_id": "synthetic-report-fixture",
        "finished_at": "2026-10-14T00:00:00Z",
        "retrieval_mode": "auto",
        "runtime_configuration": {"llm_model": "synthetic-model"},
        "tasks": [
            {
                "task_id": "REID-M02",
                "status": "completed",
                "failure_reason": None,
                "turns": [
                    {
                        "status": "completed",
                        "first_body_ms": 1000,
                        "answer": {"warnings": ["INSUFFICIENT_PAPER_EVIDENCE"]},
                        "diagnostics": {"model_calls": []},
                    }
                ],
            },
            {
                "task_id": "REID-R02",
                "status": "capability_missing",
                "failure_reason": "research_spaces",
                "turns": [],
            },
        ],
    }
    result = overview(report)
    assert result["answer_quality_evaluated"] is False
    assert result["tasks"][0]["reply_class"] == "reply_with_server_warning"
    assert result["tasks"][1]["max_first_body_ms"] is None
    assert result["reply_counts"] == {
        "reply_with_server_warning": 1,
        "no_complete_scenario": 1,
    }
    text = render_overview(report, result)
    assert "INSUFFICIENT_PAPER_EVIDENCE" in text
    assert "不能视作科研题通过" in text
    assert "synthetic-report-fixture" in text
