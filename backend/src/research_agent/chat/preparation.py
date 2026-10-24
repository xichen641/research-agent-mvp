"""Bounded, non-authoritative projections; original messages remain in storage."""

import re

from pydantic import BaseModel, Field

from research_agent.providers.context_budget import (
    ContextBudgetPolicy,
    segments,
    shorten,
    units,
)
from research_agent.providers.protocols import ChatTurn, LLMProvider
from research_agent.shared.errors import AppError


class ContextSegmentDraft(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


def critical_lines(text: str, source: str) -> list[str]:
    lines = re.split(r"(?<=[。！？\n;；])", text)
    pattern = r"更正|改为|纠正|约束|限制|显存|基线|不得|禁止|尚未|未训练|必须|只用|不使用|GB|baseline|correction|must|constraint"
    return [
        f"[{source}] {line.strip()}"
        for line in lines
        if re.search(pattern, line, re.I) and line.strip()
    ]


def bounded_constraints(lines: list[str], limit: int) -> tuple[str, ...]:
    result: list[str] = []
    remaining = limit
    for line in reversed(list(dict.fromkeys(lines))):
        value = shorten(line, remaining)
        if value:
            result.append(value)
            remaining -= units(value) + 1
        if remaining <= 0:
            break
    return tuple(reversed(result))


async def project_text(
    llm: LLMProvider, policy: ContextBudgetPolicy, text: str, *, limit: int, label: str
) -> tuple[str, list[str]]:
    if units(text) <= limit:
        return text, []
    parts = list(segments(text, policy.question - 256))
    notes: list[str] = []
    try:
        for index, part in enumerate(parts):
            draft = await llm.generate_structured(
                operation="prepare_context_segment",
                messages=[
                    ChatTurn(
                        "system",
                        "Summarize this segment of user context concisely. Preserve explicit corrections, negations, constraints, identifiers and unresolved questions. Do not infer completed experiments or paper evidence. Later segments override earlier conflicting statements.",
                    ),
                    ChatTurn(
                        "user",
                        f"{label} segment {index + 1}/{len(parts)}:\n{part}",
                        "question",
                    ),
                ],
                output_type=ContextSegmentDraft,
            )
            notes.append(shorten(draft.text, max(64, (limit - 128) // len(parts))))
    except (AppError, TimeoutError):
        return shorten(text, limit), ["CONTEXT_PREPARATION_FALLBACK"]
    return shorten(
        "分段整理的非权威上下文，原文已保留：\n" + "\n".join(notes), limit
    ), ["CONTEXT_INPUT_SEGMENTED"]
