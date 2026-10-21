from __future__ import annotations

from typing import Any

import httpx
from pydantic import SecretStr, ValidationError

from dr_core.db.enums import SearchProviderName
from dr_core.providers.cache import (
    ProviderCache,
    cache_key,
    get_or_load_json,
)
from dr_core.providers.errors import (
    ProviderDisabled,
    ProviderNotConfigured,
    ProviderResponseError,
)
from dr_core.providers.http import JsonHttpClient, RetryPolicy
from dr_core.providers.models import SearchResult
from dr_core.providers.text import (
    clamp_score,
    parse_date_value,
    strip_markup,
)

_MAX_RESULTS = 20


class TavilySearchProvider:
    name = SearchProviderName.TAVILY

    def __init__(
        self,
        api_key: str | SecretStr | None,
        *,
        enabled: bool = True,
        base_url: str = "https://api.tavily.com",
        search_depth: str = "basic",
        cache: ProviderCache | None = None,
        cache_ttl_seconds: float = 900,
        timeout_seconds: float = 20,
        connect_timeout_seconds: float = 5,
        retry_policy: RetryPolicy | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = (
            api_key.get_secret_value()
            if isinstance(api_key, SecretStr)
            else (api_key or "")
        )
        self._enabled = enabled
        self._cache = cache
        self._cache_ttl = cache_ttl_seconds
        self._search_depth = search_depth
        self._http = JsonHttpClient(
            self.name.value,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            retry_policy=retry_policy,
            client=client,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def close(self) -> None:
        self._http.close()

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchResult]:
        if not self._enabled:
            raise ProviderDisabled(self.name.value, "Tavily provider is disabled")
        if not self._api_key:
            raise ProviderNotConfigured(
                self.name.value,
                "TAVILY_API_KEY is not configured",
            )
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Search query must not be empty")
        limit = max(1, min(limit, _MAX_RESULTS))
        offset = max(offset, 0)
        if offset >= _MAX_RESULTS:
            return []

        key = cache_key(
            self.name.value,
            "search",
            normalized_query,
            limit=limit,
            offset=offset,
            depth=self._search_depth,
        )

        def load() -> list[dict[str, Any]]:
            payload = self._http.request_json(
                "POST",
                "/search",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "query": normalized_query,
                    "search_depth": self._search_depth,
                    "max_results": min(offset + limit, _MAX_RESULTS),
                    "include_answer": False,
                    "include_raw_content": False,
                },
            )
            raw_results = (payload or {}).get("results")
            if not isinstance(raw_results, list):
                raise ProviderResponseError(
                    self.name.value,
                    "Tavily returned an unexpected payload",
                )
            parsed: list[dict[str, Any]] = []
            for index, item in enumerate(raw_results):
                result = self._to_result(item, rank=index)
                if result is not None:
                    parsed.append(result.model_dump(mode="json"))
            return parsed

        cached = get_or_load_json(self._cache, key, self._cache_ttl, load)
        results = [SearchResult.model_validate(item) for item in cached]
        return [
            result.model_copy(update={"rank": offset + index})
            for index, result in enumerate(results[offset : offset + limit])
        ]

    def _to_result(self, item: Any, *, rank: int) -> SearchResult | None:
        if not isinstance(item, dict):
            return None
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url:
            return None
        content = item.get("content")
        snippet = strip_markup(str(content)) if content else ""
        try:
            return SearchResult(
                provider=self.name,
                title=title,
                url=url,
                snippet=snippet[:20000],
                published_at=parse_date_value(item.get("published_date")),
                score=clamp_score(item.get("score")),
                rank=rank,
                metadata={"tavily_score": item.get("score")},
            )
        except (ValidationError, ValueError):
            return None
