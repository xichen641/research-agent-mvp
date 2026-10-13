import math
from typing import Protocol

import httpx

from research_agent.config import Settings
from research_agent.providers.protocols import RerankItem
from research_agent.shared.errors import AppError


class AsyncHTTPClient(Protocol):
    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
    ) -> httpx.Response: ...


class QwenRerankerProvider:
    def __init__(
        self,
        *,
        client: AsyncHTTPClient,
        base_url: str,
        api_key: str,
        model_id: str,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        if not normalized_base_url.startswith("https://"):
            raise ValueError("reranker base_url must use https")
        if not api_key.strip():
            raise ValueError("reranker api_key must not be blank")
        if not model_id.strip():
            raise ValueError("reranker model_id must not be blank")
        self._client = client
        self._endpoint = f"{normalized_base_url}/compatible-api/v1/reranks"
        self._api_key = api_key
        self.model_id = model_id

    async def rerank(
        self,
        *,
        query: str,
        items: list[RerankItem],
        top_n: int,
    ) -> list[RerankItem]:
        if top_n <= 0:
            raise ValueError("top_n must be positive")
        if not query.strip():
            raise ValueError("rerank query must not be blank")
        if not items:
            return []
        effective_top_n = min(top_n, len(items))
        try:
            response = await self._client.post(
                self._endpoint,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self.model_id,
                    "query": query,
                    "documents": [item.text for item in items],
                    "top_n": effective_top_n,
                },
            )
        except httpx.HTTPError as error:
            raise AppError(
                "RERANKER_UNAVAILABLE",
                "Reranker is temporarily unavailable",
                retryable=True,
            ) from error

        if response.status_code in {408, 429} or response.status_code >= 500:
            raise AppError(
                "RERANKER_UNAVAILABLE",
                "Reranker is temporarily unavailable",
                retryable=True,
            )
        if response.status_code >= 400:
            raise AppError(
                "RERANKER_REQUEST_FAILED",
                "Reranker request could not be completed",
            )

        results = self._parse_results(response, expected_count=effective_top_n)
        reranked: list[RerankItem] = []
        seen: set[int] = set()
        for result in results:
            index = result.get("index")
            score = result.get("relevance_score")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= len(items)
                or index in seen
                or not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not math.isfinite(float(score))
                or not 0.0 <= float(score) <= 1.0
            ):
                raise self._invalid_response()
            seen.add(index)
            item = items[index]
            reranked.append(RerankItem(id=item.id, text=item.text, score=float(score)))
        return reranked

    def _parse_results(
        self,
        response: httpx.Response,
        *,
        expected_count: int,
    ) -> list[dict[str, object]]:
        try:
            payload = response.json()
        except ValueError as error:
            raise self._invalid_response() from error
        if not isinstance(payload, dict):
            raise self._invalid_response()
        raw_results = payload.get("results")
        if (
            not isinstance(raw_results, list)
            or len(raw_results) != expected_count
            or not all(isinstance(item, dict) for item in raw_results)
        ):
            raise self._invalid_response()
        return raw_results

    @staticmethod
    def _invalid_response() -> AppError:
        return AppError(
            "RERANKER_INVALID_RESPONSE",
            "Reranker returned an invalid response",
        )


def build_qwen_reranker(settings: Settings) -> QwenRerankerProvider:
    return QwenRerankerProvider(
        client=httpx.AsyncClient(timeout=30.0),
        base_url=settings.reranker_base_url,
        api_key=settings.reranker_api_key,
        model_id=settings.reranker_model,
    )
