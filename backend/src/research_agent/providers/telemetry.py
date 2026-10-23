"""Allowlisted model-call measurements; never retain prompts or provider errors."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ModelCallObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: UUID
    operation: str
    attempt: int = Field(ge=1, le=2)
    requested_model: str
    response_model: str | None
    started_at: datetime
    finished_at: datetime
    duration_ms: float = Field(ge=0)
    outcome: Literal["completed", "http_error", "transport_error", "invalid_response"]
    http_status: int | None
    prompt_tokens: int | None = Field(ge=0, strict=True)
    completion_tokens: int | None = Field(ge=0, strict=True)
    total_tokens: int | None = Field(ge=0, strict=True)
    request_sha256: str


ModelCallObserver = Callable[[ModelCallObservation], Awaitable[None]]


def response_metadata(payload: object) -> tuple[str | None, dict[str, int | None]]:
    model = None
    usage: object = None
    if isinstance(payload, dict):
        raw_model = payload.get("model")
        if isinstance(raw_model, str) and 0 < len(raw_model) <= 256:
            model = raw_model
        usage = payload.get("usage")
    tokens: dict[str, int | None] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key) if isinstance(usage, dict) else None
        tokens[key] = value if type(value) is int and value >= 0 else None
    return model, tokens
