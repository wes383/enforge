from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from dr_core.db.enums import SearchProviderName
from dr_core.normalization import InvalidDOI, normalize_doi
from dr_core.providers.cache import (
    ProviderCache,
    cache_key,
    get_or_load_json,
)
from dr_core.providers.errors import ProviderDisabled, ProviderResponseError
from dr_core.providers.http import JsonHttpClient, RetryPolicy
from dr_core.providers.models import AcademicReference, AcademicResult
from dr_core.providers.text import parse_year, strip_markup

_FIELDS = ",".join(
    (
        "DOI",
        "title",
        "author",
        "issued",
        "published",
        "container-title",
        "publisher",
        "URL",
        "abstract",
        "reference",
        "is-referenced-by-count",
        "reference-count",
        "type",
    )
)
_MAX_RESULTS = 100


class CrossrefProvider:
    name = SearchProviderName.CROSSREF
    supports_citations = False

    def __init__(
        self,
        *,
        enabled: bool = True,
        mailto: str | None = None,
        base_url: str = "https://api.crossref.org",
        cache: ProviderCache | None = None,
        cache_ttl_seconds: float = 900,
        timeout_seconds: float = 20,
        connect_timeout_seconds: float = 5,
        retry_policy: RetryPolicy | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._enabled = enabled
        self._mailto = (mailto or "").strip() or None
        self._cache = cache
        self._cache_ttl = cache_ttl_seconds
        user_agent = "Enforge/0.1"
        if self._mailto:
            user_agent = f"{user_agent} (mailto:{self._mailto})"
        self._http = JsonHttpClient(
            self.name.value,
            base_url=base_url,
            headers={"User-Agent": user_agent},
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
            raise ProviderDisabled(self.name.value, "Crossref provider is disabled")
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
                "/works",
                params={
                    "query.bibliographic": normalized_query,
                    "rows": limit,
                    "offset": offset,
                    "select": _FIELDS,
                    **({"mailto": self._mailto} if self._mailto else {}),
                },
            )
            message = (payload or {}).get("message")
            if not isinstance(message, dict):
                raise ProviderResponseError(
                    self.name.value,
                    "Crossref returned an unexpected payload",
                )
            items = message.get("items")
            if not isinstance(items, list):
                raise ProviderResponseError(
                    self.name.value,
                    "Crossref response is missing items",
                )
            parsed: list[dict[str, Any]] = []
            for index, item in enumerate(items):
                result = self._to_result(item, rank=index)
                if result is not None:
                    parsed.append(result.model_dump(mode="json"))
            return parsed

        cached = get_or_load_json(self._cache, key, self._cache_ttl, load)
        return [AcademicResult.model_validate(item) for item in cached]

    def get_work(self, identifier: str) -> AcademicResult | None:
        payload = self._get_work_payload(identifier)
        if payload is None:
            return None
        return self._to_result(payload, rank=0)

    def get_references(
        self,
        identifier: str,
        *,
        limit: int = 50,
    ) -> list[AcademicReference]:
        payload = self._get_work_payload(identifier)
        if payload is None:
            return []
        raw_references = payload.get("reference")
        if not isinstance(raw_references, list):
            return []
        references: list[AcademicReference] = []
        for item in raw_references[: max(1, min(limit, 500))]:
            reference = self._to_reference(item)
            if reference is not None:
                references.append(reference)
        return references

    def get_citations(
        self,
        identifier: str,
        *,
        limit: int = 50,
    ) -> list[AcademicResult]:
        return []

    def _get_work_payload(self, identifier: str) -> dict[str, Any] | None:
        if not self._enabled:
            raise ProviderDisabled(self.name.value, "Crossref provider is disabled")
        try:
            doi = normalize_doi(identifier)
        except InvalidDOI as exc:
            raise ValueError("Crossref lookup requires a valid DOI") from exc
        key = cache_key(self.name.value, "work", doi)

        def load() -> dict[str, Any] | None:
            payload = self._http.request_json(
                "GET",
                f"/works/{quote(doi, safe='')}",
                params={"mailto": self._mailto} if self._mailto else None,
                allow_not_found=True,
            )
            if payload is None:
                return None
            message = payload.get("message")
            if not isinstance(message, dict):
                raise ProviderResponseError(
                    self.name.value,
                    "Crossref returned an unexpected work payload",
                )
            items = message.get("items")
            if isinstance(items, list):
                return items[0] if items and isinstance(items[0], dict) else None
            return message

        return get_or_load_json(self._cache, key, self._cache_ttl, load)

    def _to_result(self, item: Any, *, rank: int) -> AcademicResult | None:
        if not isinstance(item, dict):
            return None
        titles = item.get("title")
        title = ""
        if isinstance(titles, list) and titles:
            title = str(titles[0] or "").strip()
        if not title:
            return None
        doi = item.get("DOI")
        published_at, published_year = _crossref_date(
            item.get("published") or item.get("issued")
        )
        url_value = item.get("URL")
        if not url_value and doi:
            url_value = f"https://doi.org/{doi}"
        authors = _crossref_authors(item.get("author"))
        venue = _first_value(item.get("container-title"))
        try:
            return AcademicResult(
                provider=self.name,
                provider_id=str(doi) if doi else None,
                title=title,
                authors=authors,
                doi=str(doi) if doi else None,
                url=str(url_value) if url_value else None,
                published_year=published_year,
                published_at=published_at,
                venue=venue,
                publisher=_optional_str(item.get("publisher")),
                abstract=strip_markup(str(item.get("abstract") or "")) or None,
                citation_count=_optional_non_negative(
                    item.get("is-referenced-by-count")
                ),
                reference_count=_optional_non_negative(item.get("reference-count")),
                work_type=_optional_str(item.get("type")),
                rank=rank,
                metadata={"crossref_type": item.get("type")},
            )
        except (ValidationError, ValueError):
            return None

    def _to_reference(self, item: Any) -> AcademicReference | None:
        if not isinstance(item, dict):
            return None
        doi = item.get("DOI")
        title = _optional_str(
            item.get("article-title")
            or item.get("volume-title")
            or item.get("unstructured")
        )
        year = parse_year(item.get("year"))
        author = _optional_str(item.get("author"))
        unstructured = _optional_str(item.get("unstructured"))
        try:
            return AcademicReference(
                title=title[:5000] if title else None,
                authors=[author] if author else [],
                doi=str(doi) if doi else None,
                year=year,
                venue=_optional_str(item.get("journal-title")),
                url=f"https://doi.org/{doi}" if doi else None,
                unstructured=unstructured[:10000] if unstructured else None,
            )
        except (ValidationError, ValueError):
            return None


def _first_value(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return _optional_str(value[0])
    return _optional_str(value)


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


def _crossref_date(value: Any):
    if not isinstance(value, dict):
        return None, None
    parts = value.get("date-parts")
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
        return None, None
    numbers = [part for part in parts[0] if isinstance(part, int)]
    if not numbers:
        return None, None
    year = parse_year(numbers[0])
    if len(numbers) >= 3:
        try:
            return date(numbers[0], numbers[1], numbers[2]), year
        except ValueError:
            return None, year
    return None, year


def _crossref_authors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for author in value[:200]:
        if not isinstance(author, dict):
            continue
        name = " ".join(
            part
            for part in (
                _optional_str(author.get("given")),
                _optional_str(author.get("family")),
            )
            if part
        ).strip() or _optional_str(author.get("name"))
        if name:
            authors.append(name[:300])
    return authors
