import hashlib
import json
import logging
from datetime import UTC, datetime
from time import perf_counter
from typing import Protocol
from uuid import uuid4

import httpx
from pydantic import ValidationError
from research_agent.config import Settings
from research_agent.providers.protocols import ChatTurn, OutputT
from research_agent.providers.telemetry import (
    ModelCallObservation,
    ModelCallObserver,
    response_metadata,
)
from research_agent.shared.errors import AppError

logger = logging.getLogger(__name__)


class AsyncHTTPClient(Protocol):
    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
    ) -> httpx.Response: ...


class QwenLLMProvider:
    """Project-owned structured-output adapter for Qwen's compatible API.

    项目自有的通义千问兼容接口结构化输出适配器。
    """

    def __init__(
        self,
        *,
        client: AsyncHTTPClient,
        base_url: str,
        api_key: str,
        model_id: str,
        observer: ModelCallObserver | None = None,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        if not normalized_base_url.startswith("https://"):
            raise ValueError("llm base_url must use https")
        if not api_key.strip():
            raise ValueError("llm api_key must not be blank")
        if not model_id.strip():
            raise ValueError("llm model_id must not be blank")
        self._client = client
        self._endpoint = f"{normalized_base_url}/chat/completions"
        self._api_key = api_key
        self.model_id = model_id
        self._observer = observer

    async def generate_structured(
        self,
        *,
        operation: str,
        messages: list[ChatTurn],
        output_type: type[OutputT],
    ) -> OutputT:
        if not operation.strip():
            raise ValueError("operation must not be blank")
        schema = json.dumps(
            output_type.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        instruction = (
            "Return exactly one valid JSON object and no markdown. "
            f"Operation: {operation}. Output type: {output_type.__name__}. "
            f"The JSON must satisfy this JSON Schema: {schema}"
        )
        request_messages: list[dict[str, str]] = [
            {"role": "system", "content": instruction},
            *[{"role": turn.role, "content": turn.content} for turn in messages],
        ]
        # One bounded retry absorbs transient provider and schema failures.
        # 仅进行一次有界重试，用于吸收瞬时服务故障或结构化输出偏差。
        for attempt in range(2):
            started_at = datetime.now(UTC)
            started = perf_counter()
            request_sha256 = hashlib.sha256(
                json.dumps(
                    {"model": self.model_id, "messages": request_messages},
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()

            async def observe(
                outcome: str,
                response: httpx.Response | None = None,
                *,
                observation_attempt: int = attempt + 1,
                observation_started_at: datetime = started_at,
                observation_started: float = started,
                observation_request_sha256: str = request_sha256,
            ) -> None:
                if self._observer is None:
                    return
                payload: object = None
                if response is not None:
                    try:
                        payload = response.json()
                    except ValueError:
                        pass
                model, tokens = response_metadata(payload)
                observation = ModelCallObservation.model_validate(
                    {
                        "call_id": uuid4(),
                        "operation": operation,
                        "attempt": observation_attempt,
                        "requested_model": self.model_id,
                        "response_model": model,
                        "started_at": observation_started_at,
                        "finished_at": datetime.now(UTC),
                        "duration_ms": (perf_counter() - observation_started) * 1000,
                        "outcome": outcome,
                        "http_status": None
                        if response is None
                        else response.status_code,
                        **tokens,
                        "request_sha256": observation_request_sha256,
                    }
                )
                try:
                    await self._observer(observation)
                except Exception:  # noqa: BLE001 -- best-effort telemetry
                    # A telemetry persistence failure must not alter answer behavior.
                    logger.warning("Model-call measurement could not be persisted")

            try:
                response = await self._client.post(
                    self._endpoint,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self.model_id,
                        "messages": request_messages,
                        "response_format": {"type": "json_object"},
                        # Structured planning and cited-answer JSON do not need the
                        # model's default long reasoning pass. Citation correctness
                        # is enforced separately by the deterministic verifier.
                        "enable_thinking": False,
                    },
                )
            except httpx.HTTPError as error:
                await observe("transport_error")
                if attempt == 0:
                    continue
                raise _unavailable() from error

            if response.status_code in {408, 429} or response.status_code >= 500:
                await observe("http_error", response)
                if attempt == 0:
                    continue
                raise _unavailable()
            if response.status_code >= 400:
                await observe("http_error", response)
                raise AppError(
                    "LLM_REQUEST_FAILED",
                    "Language model request could not be completed",
                )
            try:
                payload = response.json()
                choices = payload["choices"]
                if not isinstance(choices, list) or len(choices) != 1:
                    raise ValueError("invalid choices")
                choice = choices[0]
                if not isinstance(choice, dict):
                    raise TypeError("invalid choice")
                message = choice.get("message")
                if not isinstance(message, dict):
                    raise TypeError("invalid message")
                content = message.get("content")
                if not isinstance(content, str):
                    raise TypeError("invalid content")
                result = output_type.model_validate_json(content)
            except (KeyError, TypeError, ValueError, ValidationError) as error:
                await observe("invalid_response", response)
                if attempt == 0:
                    request_messages.append(
                        {
                            "role": "system",
                            "content": (
                                "The previous response failed schema validation. "
                                "Return a corrected JSON object matching the schema."
                            ),
                        }
                    )
                    continue
                raise AppError(
                    "LLM_INVALID_RESPONSE",
                    "Language model returned an invalid structured response",
                ) from error
            else:
                await observe("completed", response)
                return result
        raise _unavailable()


def build_qwen_llm(
    settings: Settings,
    *,
    client: AsyncHTTPClient | None = None,
) -> QwenLLMProvider:
    api_key = settings.llm_api_key or settings.embedding_api_key
    return QwenLLMProvider(
        client=client or httpx.AsyncClient(timeout=settings.llm_timeout_seconds),
        base_url=settings.llm_base_url,
        api_key=api_key,
        model_id=settings.llm_model,
    )


def _unavailable() -> AppError:
    return AppError(
        "LLM_UNAVAILABLE",
        "Language model is temporarily unavailable",
        retryable=True,
    )
