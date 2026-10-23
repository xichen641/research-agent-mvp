"""Run fixed tasks against the real application, without injecting review answers."""

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from research_agent.evaluation.corpus import CorpusManifest, load_corpus_manifest
from research_agent.evaluation.import_corpus import ImportReport
from research_agent.evaluation.research_tasks import (
    ResearchTask,
    load_research_tasks,
    task_fingerprint,
    validate_task_sources,
)
from research_agent.papers.quality import DocumentQualityResponse

FILLER_CYCLE = (
    "要求列出语义分支收益的三个竞争解释。",
    "要求设计一个固定 backbone 的最小对照。",
    "要求核对该对照的潜在混淆因素。",
    "要求整理尚未执行的下一步及停止条件。",
)
METRICS = ("first_progress_ms", "first_body_ms", "completion_ms")


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def get_json(client: httpx.Client, path: str) -> dict[str, Any]:
    response = client.get(path)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise TypeError("API response must be an object")
    return value


def paper_bindings(
    client: httpx.Client, manifest: CorpusManifest, import_path: Path
) -> dict[str, dict[str, Any]]:
    imported = ImportReport.model_validate_json(import_path.read_text(encoding="utf-8"))
    if (imported.corpus_id, imported.corpus_version) != (
        manifest.corpus_id,
        manifest.corpus_version,
    ):
        raise ValueError("import report belongs to another corpus")
    items = {item.paper_id: item for item in imported.papers}
    if len(items) != len(imported.papers) or set(items) != {
        p.paper_id for p in manifest.papers
    }:
        raise ValueError("import report must identify every fixed paper exactly once")
    result = {}
    for paper in manifest.papers:
        item = items[paper.paper_id]
        if (
            item.sha256 != paper.sha256
            or item.document_id is None
            or item.version_id is None
        ):
            raise ValueError(f"{paper.paper_id}: fixed import receipt unavailable")
        quality = DocumentQualityResponse.model_validate(
            get_json(client, f"documents/{item.document_id}/quality")
        )
        if (
            quality.source_sha256 != paper.sha256
            or quality.version_id != item.version_id
            or quality.status != "READY"
            or quality.quality_state != "available"
            or quality.quality is None
            or quality.quality.total_pages != paper.page_count
        ):
            raise ValueError(
                f"{paper.paper_id}: server material is not the fixed ready version"
            )
        result[paper.paper_id] = quality.model_dump(mode="json")
    return result


def check_scope(
    client: httpx.Client, bindings: dict[str, dict[str, Any]], scope: list[str]
) -> None:
    for paper_id in scope:
        expected = bindings[paper_id]
        current = get_json(client, f"documents/{expected['document_id']}/quality")
        if (
            any(
                current.get(key) != expected[key]
                for key in (
                    "version_id",
                    "source_sha256",
                    "pipeline_version",
                    "parser_version",
                )
            )
            or current.get("status") != "READY"
        ):
            raise ValueError(
                f"{paper_id}: material or parsing version changed during baseline"
            )


def usage_summary(calls: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {}
    missing = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        known = [call[key] for call in calls if type(call.get(key)) is int]
        missing[key] = len(calls) - len(known)
        totals[key] = sum(known) if calls and not missing[key] else None
    return {
        "scope": "observed_llm_http_attempts_including_retries_and_compaction",
        "model_call_count": len(calls),
        **totals,
        "missing_call_counts": missing,
        "embedding_tokens": None,
        "embedding_token_reason": "embedding_provider_usage_not_exposed",
        "cost_amount": None,
        "cost_currency": None,
        "cost_reason": "currency_qualified_billing_not_collected_no_price_assumption",
    }


def tool_actions(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    starts = [e for e in timeline if e.get("type") == "retrieval_started"]
    ends = [e for e in timeline if e.get("type") == "retrieval_completed"]
    result = []
    for index, start in enumerate(starts):
        end = ends[index] if index < len(ends) else None
        duration = (
            None
            if end is None
            else (
                datetime.fromisoformat(end["created_at"])
                - datetime.fromisoformat(start["created_at"])
            ).total_seconds()
            * 1000
        )
        result.append(
            {
                "name": "retrieval.retrieve",
                "kind": "workflow_service_call",
                "started_at": start["created_at"],
                "finished_at": None if end is None else end["created_at"],
                "duration_ms": duration,
            }
        )
    return result


def record_turn(
    client: httpx.Client,
    *,
    session_id: str,
    question: str,
    turn_id: str,
    mode: str,
    path: Path,
    configuration: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "turn_id": turn_id,
        "session_id": session_id,
        "run_id": None,
        "started_at": now(),
        "question": question,
        "retrieval_mode": mode,
        "status": "submitting",
        "failure_reason": None,
        "first_progress_ms": None,
        "first_body_ms": None,
        "completion_ms": None,
        "terminal_ms": None,
        "sse_events": [],
        "diagnostics": None,
        "answer": None,
        "answer_message_id": None,
        "model_version": None,
        "model_version_reason": "immutable_revision_not_exposed",
    }
    save_json(path, record)
    started = time.perf_counter()
    body_ms = None
    stream_completed = False
    try:
        # This POST is never automatically retried: message idempotency is T1.2.
        response = client.post(
            f"chat/sessions/{session_id}/messages",
            json={"question": question, "retrieval_mode": mode},
        )
        response.raise_for_status()
        accepted = response.json()
        record["run_id"] = str(accepted["run_id"])
        record["status"] = "observing"
        save_json(path, record)
        stream_path = f"chat/runs/{record['run_id']}/stream"
        with client.stream("GET", stream_path) as stream:
            stream.raise_for_status()
            event_id = None
            data_lines: list[str] = []
            for line in stream.iter_lines():
                elapsed = (time.perf_counter() - started) * 1000
                if elapsed > timeout_seconds * 1000:
                    raise TimeoutError("turn observation deadline exceeded")
                if line.startswith("id:"):
                    event_id = line[3:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif not line and data_lines:
                    raw = "\n".join(data_lines)
                    data_lines = []
                    part: dict[str, Any] = (
                        {"type": "done"} if raw == "[DONE]" else json.loads(raw)
                    )
                    if not isinstance(part, dict):
                        raise TypeError("SSE part must be an object")
                    record["sse_events"].append(
                        {"id": event_id, "received_ms": elapsed, "part": part}
                    )
                    if (
                        part.get("type") == "data-workflow"
                        and part.get("data", {}).get("status") == "completed"
                        and record["first_progress_ms"] is None
                    ):
                        record["first_progress_ms"] = elapsed
                    if (
                        part.get("type") == "text-delta"
                        and isinstance(part.get("delta"), str)
                        and part["delta"].strip()
                        and body_ms is None
                    ):
                        body_ms = elapsed
                    save_json(path, record)
                    if part.get("type") == "done":
                        stream_completed = True
                        record["terminal_ms"] = elapsed
                        break
            if not stream_completed:
                raise ValueError("stream ended without a terminal marker")
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as error:
        record["status"] = "execution_failed"
        record["failure_reason"] = (
            "observation_timeout"
            if isinstance(error, (TimeoutError, httpx.TimeoutException))
            else "http_or_stream_failure"
        )
        record["terminal_ms"] = (time.perf_counter() - started) * 1000
        if isinstance(error, httpx.HTTPStatusError):
            record["http_status"] = error.response.status_code
        if record["run_id"] is None:
            record["failure_reason"] = (
                "submission_rejected"
                if isinstance(error, httpx.HTTPStatusError)
                and error.response.status_code < 500
                else "submission_outcome_unknown"
            )
    if record["run_id"] is not None:
        try:
            diagnostics = get_json(client, f"chat/runs/{record['run_id']}/diagnostics")
            record["diagnostics"] = diagnostics
            record["usage"] = usage_summary(diagnostics.get("model_calls", []))
            record["tool_calls"] = tool_actions(diagnostics.get("timeline", []))
            if diagnostics.get("runtime_configuration") != configuration:
                record["status"] = "execution_failed"
                record["failure_reason"] = "worker_configuration_missing_or_changed"
            elif diagnostics["status"] == "SUCCEEDED":
                history = get_json(client, f"chat/sessions/{session_id}/messages")
                completed = [
                    e for e in diagnostics["timeline"] if e["type"] == "run_completed"
                ]
                message_id = (
                    completed[-1]["data"].get("message_id") if completed else None
                )
                answer = next(
                    (
                        m
                        for m in history["items"]
                        if m["id"] == message_id and m["role"] == "assistant"
                    ),
                    None,
                )
                if answer is None:
                    raise ValueError("no durable answer for this run")
                record.update(
                    first_body_ms=body_ms,
                    answer=answer["content"],
                    answer_message_id=message_id,
                )
                if stream_completed:
                    record.update(
                        status="completed",
                        failure_reason=None,
                        completion_ms=record["terminal_ms"],
                    )
            elif diagnostics["status"] == "FAILED":
                record["status"] = "execution_failed"
                record["failure_reason"] = (
                    diagnostics.get("error_code") or "CHAT_RUN_FAILED"
                )
            record["server_status"] = diagnostics["status"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            record["status"] = "execution_failed"
            record["failure_reason"] = (
                record["failure_reason"] or "diagnostics_or_answer_unavailable"
            )
    save_json(path, record)
    print(
        f"  {turn_id}: {record['status']}, run={record['run_id']}, body_ms={record['first_body_ms']}, reason={record['failure_reason']}",
        flush=True,
    )
    return record


def capability_gap(task: ResearchTask, configuration: dict[str, Any]) -> str | None:
    capabilities = configuration.get("capabilities", {})
    required = {
        "REID-R01": ("same_session_history", "memory_compaction"),
        "REID-R02": ("research_spaces", "cross_session_research_state"),
        "REID-R03": (
            "research_spaces",
            "cross_session_research_state",
            "correctable_research_records",
        ),
        "REID-R04": (
            "research_spaces",
            "cross_session_research_state",
            "correctable_research_records",
        ),
    }.get(task.task_id, ())
    missing = [key for key in required if capabilities.get(key) is not True]
    return "missing_capabilities:" + ",".join(missing) if missing else None


def aggregate(report: dict[str, Any]) -> dict[str, Any]:
    cases = report["tasks"]
    completed_turns = [
        t for case in cases for t in case["turns"] if t["status"] == "completed"
    ]
    metric_summary = {}
    for metric in METRICS:
        values = [t[metric] for t in completed_turns if t.get(metric) is not None]
        metric_summary[metric] = {
            "sample_count": len(values),
            "missing_count": len(completed_turns) - len(values),
            "median": statistics.median(values) if values else None,
            "max": max(values) if values else None,
        }
    calls = [
        call
        for case in cases
        for turn in case["turns"]
        for call in (turn.get("diagnostics") or {}).get("model_calls", [])
    ]
    return {
        "task_status_counts": dict(Counter(case["status"] for case in cases)),
        "completed_turn_count": len(completed_turns),
        "metrics": metric_summary,
        "usage": usage_summary(calls),
        "answer_quality_evaluated": False,
    }


def latency_thresholds(case: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "basis": "provisional_max_observed_successful_turn_x_1.25_rounded_up_to_second",
        "metrics": {},
    }
    for metric in METRICS:
        values = [
            turn[metric]
            for turn in case["turns"]
            if turn["status"] == "completed" and turn.get(metric) is not None
        ]
        result["metrics"][metric] = {
            "sample_count": len(values),
            "limit_ms": math.ceil(max(values) * 1.25 / 1000) * 1000 if values else None,
            "missing_reason": None if values else "no_successful_observation",
        }
    return result


def render_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# ReID 当前体验基线",
        "",
        f"运行标识：`{report['run_id']}`；开始：{report['started_at']}。",
        "",
        "测量方式：HTTP 提交→SSE 接收，包含提交、排队和传输；不测浏览器绘制或模型原生流式首 Token。",
        "",
        "| 任务 | 状态 | 完成回合 | 压缩事件 | 首段正文最大 ms | 原因 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for case in report["tasks"]:
        bodies = [
            turn["first_body_ms"]
            for turn in case["turns"]
            if turn.get("first_body_ms") is not None
        ]
        maximum = f"{max(bodies):.0f}" if bodies else "缺失"
        lines.append(
            f"| {case['task_id']} | {case['status']} | {sum(t['status'] == 'completed' for t in case['turns'])} | {case.get('compaction_count', 0)} | {maximum} | {case.get('failure_reason') or ''} |"
        )
    lines += [
        "",
        f"状态统计：`{json.dumps(summary['task_status_counts'], ensure_ascii=False)}`。",
        "",
        f"LLM HTTP 尝试数：{summary['usage']['model_call_count']}；总 Token：{summary['usage']['total_tokens']}（缺失调用数 {summary['usage']['missing_call_counts']['total_tokens']}）。",
        "",
        "费用与独立模型修订号缺失，未根据别名推断版本或价格；embedding/reranker 用量未计入 LLM Token。",
        "",
        "每题 thresholds 保存成功回合最大观察值×1.25、向上取整到秒的暂定复测门槛；无成功观测则留空。单次任务样本不构成稳定分位数或正式 SLA。",
        "",
        "只发送固定问题与对话，没有注入 fact_checks 或评分答案。真实回答与引用见各任务目录及 report.json；人工质量评分尚未进行。",
        "",
    ]
    return "\n".join(lines)


def run_baseline(
    client: httpx.Client,
    *,
    tasks: list[ResearchTask],
    manifest: CorpusManifest,
    import_path: Path,
    output: Path,
    dataset_sha256: str,
    manifest_sha256: str,
    mode: str = "auto",
    timeout_seconds: float = 600,
    runtime_identity: dict[str, Any] | None = None,
    conversation_timeout_seconds: float = 1800,
) -> dict[str, Any]:
    if output.exists():
        raise ValueError(
            "baseline output must be a new directory; existing results are preserved"
        )
    bindings = paper_bindings(client, manifest, import_path)
    configuration = get_json(client, "chat/evaluation/config")
    output.mkdir(parents=True)
    report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": str(uuid4()),
        "started_at": now(),
        "finished_at": None,
        "measurement_protocol": "http_post_accepted_then_sse_get_v1",
        "retrieval_mode": mode,
        "dataset_sha256": dataset_sha256,
        "manifest_sha256": manifest_sha256,
        "corpus_id": manifest.corpus_id,
        "corpus_version": manifest.corpus_version,
        "runtime_configuration": configuration,
        "runtime_identity": runtime_identity or {},
        "observation_limits": {
            "turn_seconds": timeout_seconds,
            "conversation_seconds": conversation_timeout_seconds,
            "max_compaction_filler_cycles": 10,
        },
        "materials": bindings,
        "tasks": [
            {
                "task_id": task.task_id,
                "task_sha256": task_fingerprint(task),
                "category": task.category,
                "status": "not_run",
                "failure_reason": None,
                "turns": [],
            }
            for task in tasks
        ],
    }

    def checkpoint() -> None:
        report["summary"] = aggregate(report)
        save_json(output / "report.json", report)
        (output / "report.md").write_text(render_report(report), encoding="utf-8")

    checkpoint()
    for task, case in zip(tasks, report["tasks"], strict=True):
        print(f"{task.task_id}: starting", flush=True)
        gap = capability_gap(task, configuration)
        if gap:
            case.update(status="capability_missing", failure_reason=gap)
            case["thresholds"] = latency_thresholds(case)
            checkpoint()
            continue
        check_scope(client, bindings, task.paper_scope)
        response = client.post(
            "chat/sessions",
            json={
                "title": f"baseline:{report['run_id']}:{task.task_id}",
                "document_ids": [bindings[p]["document_id"] for p in task.paper_scope],
            },
        )
        response.raise_for_status()
        session_id = str(response.json()["id"])
        conversation_started = time.perf_counter()
        case["session_id"] = session_id
        case["status"] = "running"
        checkpoint()

        def turn(
            question: str,
            turn_id: str,
            *,
            task_scope: list[str] = task.paper_scope,
            task_id: str = task.task_id,
            case_record: dict[str, Any] = case,
            session_ref: str = session_id,
        ) -> dict[str, Any]:
            check_scope(client, bindings, task_scope)
            result = record_turn(
                client,
                session_id=session_ref,
                question=question,
                turn_id=turn_id,
                mode=mode,
                path=output / task_id / f"{turn_id}.json",
                configuration=configuration,
                timeout_seconds=timeout_seconds,
            )
            case_record["turns"].append(result)
            case_record["compaction_count"] = sum(
                event["data"].get("memory_compacted") is True
                for item in case_record["turns"]
                for event in (item.get("diagnostics") or {}).get("timeline", [])
                if event["type"] == "context_assembled"
            )
            checkpoint()
            return result

        if task.kind != "conversation":
            turn(task.prompt, "T01")
        else:
            for initial in task.turns[:2]:
                if turn(initial.user_message, initial.turn_id)["status"] != "completed":
                    break
            if all(t["status"] == "completed" for t in case["turns"]):
                for cycle in range(10):
                    if case.get("compaction_count", 0) >= 2:
                        break
                    if (
                        time.perf_counter() - conversation_started
                        >= conversation_timeout_seconds
                    ):
                        case["failure_reason"] = (
                            "conversation_observation_budget_exhausted"
                        )
                        break
                    cycle_failed = False
                    for index, question in enumerate(FILLER_CYCLE):
                        result = turn(question, f"C{cycle + 1:02}-{index + 1}")
                        if result["status"] != "completed":
                            cycle_failed = True
                            break
                    if cycle_failed:
                        break
                if case.get("compaction_count", 0) >= 2 and all(
                    t["status"] == "completed" for t in case["turns"]
                ):
                    turn(task.turns[2].user_message, task.turns[2].turn_id)
                elif all(t["status"] == "completed" for t in case["turns"]):
                    case["failure_reason"] = case.get("failure_reason") or (
                        "two_real_compactions_not_observed_within_ten_cycles"
                    )
        failed = [t for t in case["turns"] if t["status"] != "completed"]
        case["status"] = (
            "execution_failed" if failed or case.get("failure_reason") else "completed"
        )
        case["failure_reason"] = case.get("failure_reason") or (
            failed[0]["failure_reason"] if failed else None
        )
        try:
            check_scope(client, bindings, task.paper_scope)
        except ValueError:
            case.update(
                status="execution_failed", failure_reason="material_changed_during_turn"
            )
        case["thresholds"] = latency_thresholds(case)
        checkpoint()
    report["finished_at"] = now()
    checkpoint()
    return report


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=root / "evaluation/datasets/reid_research_tasks.jsonl",
    )
    parser.add_argument(
        "--manifest", type=Path, default=root / "evaluation/corpora/reid-v1.json"
    )
    parser.add_argument("--root", type=Path, default=root)
    parser.add_argument("--import-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--task-id", action="append")
    parser.add_argument(
        "--retrieval-mode", choices=("auto", "on", "off"), default="auto"
    )
    parser.add_argument("--turn-timeout", type=float, default=600)
    parser.add_argument("--conversation-timeout", type=float, default=1800)
    parser.add_argument("--runtime-identity", type=Path)
    args = parser.parse_args(argv)
    try:
        address = urlsplit(args.api_url)
        if (
            address.scheme not in {"http", "https"}
            or not address.netloc
            or address.username
            or address.password
            or address.query
            or address.fragment
            or address.path not in ("", "/")
            or not math.isfinite(args.turn_timeout)
            or args.turn_timeout <= 0
            or not math.isfinite(args.conversation_timeout)
            or args.conversation_timeout <= 0
        ):
            raise ValueError(
                "API URL must be an origin without credentials; timeout must be positive"
            )
        tasks = load_research_tasks(args.dataset)
        manifest = load_corpus_manifest(args.manifest)
        validate_task_sources(tasks, manifest, root=args.root)
        if args.task_id:
            selected = set(args.task_id)
            if not selected <= {t.task_id for t in tasks}:
                raise ValueError("unknown task ID")
            tasks = [t for t in tasks if t.task_id in selected]
        identity = (
            {}
            if args.runtime_identity is None
            else json.loads(args.runtime_identity.read_text(encoding="utf-8-sig"))
        )
        with httpx.Client(
            base_url=args.api_url.rstrip("/") + "/api/v1/",
            timeout=httpx.Timeout(30, connect=10),
            trust_env=False,
        ) as client:
            report = run_baseline(
                client,
                tasks=tasks,
                manifest=manifest,
                import_path=args.import_report,
                output=args.output,
                dataset_sha256=digest(args.dataset),
                manifest_sha256=digest(args.manifest),
                mode=args.retrieval_mode,
                timeout_seconds=args.turn_timeout,
                runtime_identity=identity,
                conversation_timeout_seconds=args.conversation_timeout,
            )
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, httpx.HTTPError) as error:
        if isinstance(error, httpx.HTTPError):
            print(
                "baseline error: API connection/request failed; saved records are preserved",
                file=sys.stderr,
            )
        else:
            print(f"baseline error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
