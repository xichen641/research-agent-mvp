"""Explain saved baseline measurements and server warnings, without model judging."""

import argparse
import hashlib
import json
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def overview(report: dict[str, Any]) -> dict[str, Any]:
    results = []
    for task in report["tasks"]:
        warnings = Counter(
            warning
            for turn in task["turns"]
            for warning in (turn.get("answer") or {}).get("warnings", [])
        )
        completed = [turn for turn in task["turns"] if turn["status"] == "completed"]
        calls = [
            call
            for turn in task["turns"]
            for call in (turn.get("diagnostics") or {}).get("model_calls", [])
        ]
        values = [
            turn["first_body_ms"]
            for turn in completed
            if turn.get("first_body_ms") is not None
        ]
        results.append(
            {
                "task_id": task["task_id"],
                "execution_status": task["status"],
                "reply_class": "reply_with_server_warning"
                if warnings
                else "reply_without_server_warning"
                if task["status"] == "completed"
                else "no_complete_scenario",
                "server_warnings": dict(warnings),
                "failure_reason": task.get("failure_reason"),
                "completed_turn_count": len(completed),
                "body_sample_count": len(values),
                "max_first_body_ms": max(values) if values else None,
                "model_call_count": len(calls),
                "provider_models": sorted(
                    {
                        call["response_model"]
                        for call in calls
                        if call.get("response_model")
                    }
                ),
                "compaction_count": task.get("compaction_count", 0),
            }
        )
    single_turns = [
        turn
        for task in report["tasks"]
        if task.get("category") != "memory_correction"
        and not task["task_id"].startswith("REID-R")
        for turn in task["turns"]
        if turn["status"] == "completed"
    ]
    single_metrics = {}
    for metric in ("first_progress_ms", "first_body_ms", "completion_ms"):
        values = [turn[metric] for turn in single_turns if turn.get(metric) is not None]
        single_metrics[metric] = {
            "sample_count": len(values),
            "median": statistics.median(values) if values else None,
            "max": max(values) if values else None,
        }
    return {
        "run_id": report["run_id"],
        "capture_finished": report.get("finished_at") is not None,
        "task_count": len(results),
        "execution_counts": dict(Counter(item["execution_status"] for item in results)),
        "reply_counts": dict(Counter(item["reply_class"] for item in results)),
        "answer_quality_evaluated": False,
        "single_turn_metrics": single_metrics,
        "tasks": results,
    }


def render_overview(report: dict[str, Any], result: dict[str, Any]) -> str:
    config = report["runtime_configuration"]
    lines = [
        "# ReID 基线测量核对",
        "",
        f"运行：`{result['run_id']}`；采集结束：`{result['capture_finished']}`。",
        "",
        f"任务数：{result['task_count']}；状态：`{json.dumps(result['execution_counts'], ensure_ascii=False)}`。",
        f"回复类别：`{json.dumps(result['reply_counts'], ensure_ascii=False)}`。",
        "",
        f"配置模型：`{config.get('llm_model')}`；检索模式：`{report['retrieval_mode']}`；每轮证据上限：{config.get('chat_evidence_limit')}。",
        "",
        "固定单轮任务统计（排除 R01 的追问，包含带警告的回复）：",
        "",
        "| 指标 | 样本数 | 中位数 s | 最大值 s |",
        "| --- | ---: | ---: | ---: |",
    ]
    labels = {
        "first_progress_ms": "有效进度",
        "first_body_ms": "首段正文",
        "completion_ms": "完成",
    }
    for metric, values in result["single_turn_metrics"].items():
        median = (
            "缺失" if values["median"] is None else f"{values['median'] / 1000:.1f}"
        )
        maximum = "缺失" if values["max"] is None else f"{values['max'] / 1000:.1f}"
        lines.append(
            f"| {labels[metric]} | {values['sample_count']} | {median} | {maximum} |"
        )
    lines += [
        "",
        "| 任务 | 场景执行状态 | 回复类别 | 正文样本 | 首段正文最大 ms | LLM HTTP 尝试 | 压缩事件 | 服务警告/原因 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for task in result["tasks"]:
        elapsed = (
            "缺失"
            if task["max_first_body_ms"] is None
            else f"{task['max_first_body_ms']:.0f}"
        )
        notes = "; ".join(
            note
            for note in (", ".join(task["server_warnings"]), task["failure_reason"])
            if note
        )
        lines.append(
            f"| {task['task_id']} | {task['execution_status']} | {task['reply_class']} | {task['body_sample_count']} | {elapsed} | {task['model_call_count']} | {task['compaction_count']} | {notes} |"
        )
    lines += [
        "",
        "这里的 completed 表示按执行协议取得持久回复，不能视作科研题通过。服务端的证据不足或引用失败警告照原样列出；无警告也不自动证明论文事实正确。",
        "",
        "首段正文指客户端实际接收的正文事件，可能是澄清回复或带警告的回答。此类时间不能作为后续完整论文回答的同类门槛；复测需核对 reply_class，分类变化时分别比较。",
        "",
        "R01 的压缩次数来自已持久化 context_assembled 事件，不能用轮数或模型口述替代。R02–R04 缺失能力不执行模拟动作；缺测门槛留空。",
        "",
        "report.json 中每题 thresholds 是成功回合最大观测值×1.25、向上取整到秒的暂定门槛。全局时延统计只包含 completed 回合，也可能包含未完成连续场景中的成功回合；失败、能力缺失与其缺失值查看每题原始记录，不能用全局 missing_count=0 推断全套任务无缺测。",
        "",
        "Token 是已记录的 LLM HTTP 尝试用量，包含观测到的重试和记忆压缩；embedding/reranker 用量及带币种账单未收集，费用保持缺失。模型别名与供应商返回名称均已记录，独立权重修订号仍未知。",
        "",
        "质量评分按 evaluation/rubrics/reid_research_review.md 另行进行。本报告只读取已保存观测，无模型调用、答案补写或默认评分。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        output = args.output or args.report.parent / "measurement-review.md"
        if output.exists() or output.resolve() == args.report.resolve():
            raise ValueError("use a new output file; existing reports are preserved")
        raw = args.report.read_bytes()
        report = json.loads(raw)
        result = overview(report)
        text = render_overview(report, result)
        text += f"\n原始 report.json SHA-256：`{hashlib.sha256(raw).hexdigest()}`；生成时间：{datetime.now(UTC).isoformat()}。\n"
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(text)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "execution_counts": result["execution_counts"],
                    "reply_counts": result["reply_counts"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"measurement review error: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
