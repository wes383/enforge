from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from pydantic import SecretStr, ValidationError

from dr_core.db.enums import SearchProviderName
from dr_core.normalization import InvalidDOI, normalize_doi
from dr_core.providers.cache import (
    ProviderCache,
    cache_key,
    get_or_load_json,
)
from dr_core.providers.errors import (
    ProviderDisabled,
    ProviderResponseError,
)
from dr_core.providers.http import JsonHttpClient, RetryPolicy
from dr_core.providers.models import AcademicReference, AcademicResult
from dr_core.providers.text import parse_date_value, parse_year

_FIELDS = ",".join(
    (
        "paperId",
        "title",
        "abstract",
        "url",
        "year",
        "venue",
        "publicationDate",
        "authors",
        "externalIds",
        "citationCount",
        "referenceCount",
        "openAccessPdf",
        "publicationTypes",
    )
)
_MAX_RESULTS = 100


class SemanticScholarProvider:
    name = SearchProviderName.SEMANTIC_SCHOLAR
    supports_citations = True

    def __init__(
        self,
        api_key: str | SecretStr | None = None,
        *,
        enabled: bool = True,
        base_url: str = "https://api.semanticscholar.org/graph/v1",
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
        headers = {"x-api-key": self._api_key} if self._api_key else {}
        self._http = JsonHttpClient(
            self.name.value,
            base_url=base_url,
            headers=headers,
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

    def search_works(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[AcademicResult]:
        if not self._enabled:
            raise ProviderDisabled(
                self.name.value,
                "Semantic Scholar provider is disabled",
            )
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Search query must not be empty")
        limit = max(1, min(limit, _MAX_RESULTS))
        offset = max(offset, 0)
        key = cache_key(
            self.name.value,
            "search",
            normalized_query,
            limit=limit,
            offset=offset,
        )

        def load() -> list[dict[str, Any]]:
            payload = self._http.request_json(
                "GET",
                "/paper/search",
                params={
                    "query": normalized_query,
                    "limit": limit,
                    "offset": offset,
                    "fields": _FIELDS,
                },
            )
            raw_results = (payload or {}).get("data")
            if not isinstance(raw_results, list):
                raise ProviderResponseError(
                    self.name.value,
                    "Semantic Scholar returned an unexpected payload",
                )
            parsed: list[dict[str, Any]] = []
            for index, item in enumerate(raw_results):
                result = self._to_result(item, rank=index)
                if result is not None:
                    parsed.append(result.model_dump(mode="json"))
            return parsed

        cached = get_or_load_json(self._cache, key, self._cache_ttl, load)
        return [AcademicResult.model_validate(item) for item in cached]

    def get_work(self, identifier: str) -> AcademicResult | None:
        if not self._enabled:
            raise ProviderDisabled(
                self.name.value,
                "Semantic Scholar provider is disabled",
            )
        path = self._paper_path(identifier)
        key = cache_key(self.name.value, "work", path)

        def load() -> dict[str, Any] | None:
            payload = self._http.request_json(
                "GET",
                path,
                params={"fields": _FIELDS},
                allow_not_found=True,
            )
            return payload

        cached = get_or_load_json(self._cache, key, self._cache_ttl, load)
        if cached is None:
            return None
        return self._to_result(cached, rank=0)

    def get_references(
        self,
        identifier: str,
        *,
        limit: int = 50,
    ) -> list[AcademicReference]:
        results = self._get_related(identifier, "references", limit=limit)
        return [
            AcademicReference(
                title=result.title,
                authors=result.authors,
                doi=result.doi,
                year=result.published_year,
                venue=result.venue,
                url=result.url,
            )
            for result in results
        ]

    def get_citations(
        self,
        identifier: str,
        *,
        limit: int = 50,
    ) -> list[AcademicResult]:
        return self._get_related(identifier, "citations", limit=limit)

    def _get_related(
        self,
        identifier: str,
        relation: str,
        *,
        limit: int,
    ) -> list[AcademicResult]:
        if not self._enabled:
            raise ProviderDisabled(
                self.name.value,
                "Semantic Scholar provider is disabled",
            )
        limit = max(1, min(limit, 1000))
        path = f"{self._paper_path(identifier)}/{relation}"
        key = cache_key(self.name.value, relation, path, limit=limit)

        def load() -> list[dict[str, Any]]:
            payload = self._http.request_json(
                "GET",
                path,
                params={"fields": _FIELDS, "limit": limit},
            )
            rows = (payload or {}).get("data")
            if not isinstance(rows, list):
                raise ProviderResponseError(
                    self.name.value,
                    "Semantic Scholar returned an unexpected relation payload",
                )
            nested_key = "citedPaper" if relation == "references" else "citingPaper"
            parsed: list[dict[str, Any]] = []
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                item = row.get(nested_key)
                if not isinstance(item, dict):
                    continue
                result = self._to_result(item, rank=index)
                if result is not None:
                    parsed.append(result.model_dump(mode="json"))
            return parsed

        cached = get_or_load_json(self._cache, key, self._cache_ttl, load)
        return [AcademicResult.model_validate(item) for item in cached]

    def _paper_path(self, identifier: str) -> str:
        candidate = identifier.strip()
        if not candidate:
            raise ValueError("Academic identifier must not be empty")
        try:
            doi = normalize_doi(candidate)
        except InvalidDOI:
            doi = None
        if doi is not None:
            return f"/paper/DOI:{quote(doi, safe='')}"
        return f"/paper/{quote(candidate, safe='')}"

    def _to_result(self, item: Any, *, rank: int) -> AcademicResult | None:
        if not isinstance(item, dict):
            return None
        title = str(item.get("title") or "").strip()
        if not title:
            return None
        external_ids = item.get("externalIds")
        doi = None
        if isinstance(external_ids, dict):
            doi = external_ids.get("DOI") or external_ids.get("doi")
        paper_id = item.get("paperId")
        url_value = item.get("url")
        if not url_value and paper_id:
            url_value = f"https://www.semanticscholar.org/paper/{paper_id}"
        authors = []
        raw_authors = item.get("authors")
        if isinstance(raw_authors, list):
            for author in raw_authors[:200]:
                if isinstance(author, dict):
                    name = str(author.get("name") or "").strip()
                    if name:
                        authors.append(name[:300])
        publication_types = item.get("publicationTypes")
        work_type = None
        if isinstance(publication_types, list) and publication_types:
            work_type = str(publication_types[0])[:128]
        open_access_url = None
        open_access = item.get("openAccessPdf")
        if isinstance(open_access, dict):
            open_access_url = open_access.get("url")
        try:
            return AcademicResult(
                provider=self.name,
                provider_id=str(paper_id) if paper_id else None,
                title=title,
                authors=authors,
                doi=str(doi) if doi else None,
                url=str(url_value) if url_value else None,
                published_year=parse_year(item.get("year")),
                published_at=parse_date_value(item.get("publicationDate")),
                venue=_optional_str(item.get("venue")),
                abstract=_optional_str(item.get("abstract")),
                citation_count=_optional_non_negative(item.get("citationCount")),
                reference_count=_optional_non_negative(item.get("referenceCount")),
                open_access_url=(
                    str(open_access_url) if open_access_url else None
                ),
                work_type=work_type,
                rank=rank,
                metadata={
                    "paper_id": paper_id,
                    "open_access": bool(open_access_url),
                },
            )
        except (ValidationError, ValueError):
            return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_non_negative(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None
