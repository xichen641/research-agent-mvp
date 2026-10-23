import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from research_agent.evaluation.baseline import (
    capability_gap,
    latency_thresholds,
    record_turn,
    run_baseline,
    usage_summary,
)
from research_agent.evaluation.corpus import load_corpus_manifest
from research_agent.evaluation.import_corpus import ImportItem, ImportReport
from research_agent.evaluation.research_tasks import load_research_tasks

ROOT = Path(__file__).parents[4]


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED"])
@pytest.mark.parametrize("terminal", [True, False])
def test_sse_receipt_does_not_confuse_acceptance_progress_or_failure_text_with_answer(
    tmp_path: Path, status: str, terminal: bool
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert json.loads(request.content) == {
                "question": "fixed question",
                "retrieval_mode": "auto",
            }
            return httpx.Response(202, json={"run_id": "run-one"})
        if request.url.path.endswith("/stream"):
            parts = [
                {"type": "start", "messageId": "run-one"},
                {
                    "type": "data-workflow",
                    "data": {"stage": "plan", "status": "completed"},
                },
                {"type": "text-delta", "delta": "Answer or failure fallback"},
            ]
            body = "".join(
                f"id: {i}\ndata: {json.dumps(part)}\n\n"
                for i, part in enumerate(parts, 1)
            ) + ("id: 4\ndata: [DONE]\n\n" if terminal else "")
            return httpx.Response(
                200,
                content=body.encode(),
                headers={"content-type": "text/event-stream"},
            )
        if request.url.path.endswith("/diagnostics"):
            return httpx.Response(
                200,
                json={
                    "status": status,
                    "error_code": "MODEL_FAILED" if status == "FAILED" else None,
                    "runtime_configuration": {},
                    "model_calls": [],
                    "timeline": [
                        {"type": "run_completed", "data": {"message_id": "message-one"}}
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "message-one",
                        "role": "assistant",
                        "content": {
                            "blocks": [{"type": "model_knowledge", "text": "Answer"}]
                        },
                    }
                ]
            },
        )

    with httpx.Client(
        base_url="http://local/api/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        result = record_turn(
            client,
            session_id="session-one",
            question="fixed question",
            turn_id="T01",
            mode="auto",
            path=tmp_path / "turn.json",
            configuration={},
            timeout_seconds=60,
        )
    assert len([r for r in requests if r.method == "POST"]) == 1
    assert result["first_progress_ms"] is not None
    assert result["terminal_ms"] is not None
    if status == "SUCCEEDED":
        assert result["first_body_ms"] >= result["first_progress_ms"]
        assert result["answer_message_id"] == "message-one"
        if terminal:
            assert result["status"] == "completed"
            assert result["completion_ms"] >= result["first_body_ms"]
        else:
            assert result["status"] == "execution_failed"
            assert result["completion_ms"] is None
    else:
        assert result["status"] == "execution_failed"
        assert result["first_body_ms"] is None
        assert result["completion_ms"] is None
        assert result["failure_reason"] == "MODEL_FAILED"


def test_post_transport_error_preserves_unknown_delivery_without_retry(
    tmp_path: Path,
) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        raise httpx.ReadError("private network detail", request=request)

    with httpx.Client(
        base_url="http://local/api/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        result = record_turn(
            client,
            session_id="session",
            question="fixed question",
            turn_id="T01",
            mode="auto",
            path=tmp_path / "turn.json",
            configuration={},
            timeout_seconds=60,
        )
    assert count == 1
    assert result["run_id"] is None
    assert result["failure_reason"] == "submission_outcome_unknown"
    assert "private network detail" not in (tmp_path / "turn.json").read_text()


@pytest.mark.parametrize(
    ("http_status", "reason"),
    [(400, "submission_rejected"), (503, "submission_outcome_unknown")],
)
def test_submission_rejection_and_server_uncertainty_are_distinct(
    tmp_path: Path, http_status: int, reason: str
) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(http_status, json={"private_detail": "do-not-export"})

    with httpx.Client(
        base_url="http://local/api/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        result = record_turn(
            client,
            session_id="session",
            question="fixed question",
            turn_id="T01",
            mode="auto",
            path=tmp_path / "turn.json",
            configuration={},
            timeout_seconds=60,
        )
    assert count == 1
    assert result["failure_reason"] == reason
    assert result["http_status"] == http_status
    assert "do-not-export" not in (tmp_path / "turn.json").read_text()


def test_partial_usage_and_unmeasured_latency_are_not_fabricated() -> None:
    usage = usage_summary(
        [
            {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            {"prompt_tokens": 20, "completion_tokens": None, "total_tokens": None},
        ]
    )
    assert usage["prompt_tokens"] == 30
    assert usage["completion_tokens"] is None
    assert usage["total_tokens"] is None
    assert usage["missing_call_counts"]["total_tokens"] == 1
    assert usage["cost_amount"] is None
    threshold = latency_thresholds({"turns": []})
    assert all(
        item["limit_ms"] is None and item["sample_count"] == 0
        for item in threshold["metrics"].values()
    )


def test_unsupported_cross_session_case_makes_no_session_or_model_requests(
    tmp_path: Path,
) -> None:
    manifest = load_corpus_manifest(ROOT / "evaluation/corpora/reid-v1.json")
    task = next(
        t
        for t in load_research_tasks(
            ROOT / "evaluation/datasets/reid_research_tasks.jsonl"
        )
        if t.task_id == "REID-R02"
    )
    snapshots = {}
    items = []
    for paper in manifest.papers:
        document_id, version_id = uuid4(), uuid4()
        snapshot = {
            "document_id": str(document_id),
            "version_id": str(version_id),
            "source_sha256": paper.sha256,
            "status": "READY",
            "parser_name": "synthetic-fixture",
            "parser_version": "test",
            "pipeline_version": "1",
            "quality_state": "available",
            "quality": {
                "total_pages": paper.page_count,
                "parsed_pages": paper.page_count,
                "body_characters": 100,
                "empty_pages": [],
                "section_count": 1,
                "table_count": 0,
                "image_count": 0,
                "warnings": [],
            },
        }
        snapshots[str(document_id)] = snapshot
        items.append(
            ImportItem(
                paper_id=paper.paper_id,
                display_name=paper.display_name,
                file_name=paper.file_name,
                sha256=paper.sha256,
                local_status="matched",
                state="ready",
                document_id=document_id,
                version_id=version_id,
            )
        )
    report = ImportReport(
        corpus_id=manifest.corpus_id,
        corpus_version=manifest.corpus_version,
        papers=items,
        unlisted_files=[],
    )
    import_path = tmp_path / "import.json"
    import_path.write_text(report.model_dump_json(), encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path.endswith("/config"):
            return httpx.Response(200, json={"capabilities": {}})
        return httpx.Response(200, json=snapshots[request.url.path.split("/")[-2]])

    with httpx.Client(
        base_url="http://local/api/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        result = run_baseline(
            client,
            tasks=[task],
            manifest=manifest,
            import_path=import_path,
            output=tmp_path / "run",
            dataset_sha256="synthetic",
            manifest_sha256="synthetic",
        )
    assert result["tasks"][0]["status"] == "capability_missing"
    assert result["summary"]["completed_turn_count"] == 0
    assert result["summary"]["answer_quality_evaluated"] is False
    assert (
        capability_gap(
            task,
            {
                "capabilities": {
                    "research_spaces": True,
                    "cross_session_research_state": True,
                }
            },
        )
        is None
    )
