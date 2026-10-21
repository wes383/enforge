from __future__ import annotations

import logging
from collections.abc import Iterable

from dr_core.normalization import normalized_text, url_fingerprint
from dr_core.providers.base import (
    AcademicSearchProvider,
    SearchProvider,
    WebContentProvider,
)
from dr_core.providers.errors import ProviderError
from dr_core.providers.models import (
    AcademicBundle,
    AcademicReference,
    AcademicResult,
    FetchStatus,
    ProviderFailure,
    SearchBundle,
    SearchResult,
    WebFetchResult,
)

logger = logging.getLogger(__name__)


def collect_web_search(
    providers: Iterable[SearchProvider],
    query: str,
    *,
    limit: int = 10,
    offset: int = 0,
) -> SearchBundle:
    limit = max(1, limit)
    offset = max(0, offset)
    merged: dict[str, SearchResult] = {}
    failures: list[ProviderFailure] = []

    for provider in providers:
        if not provider.enabled:
            continue
        try:
            results = provider.search(query, limit=limit, offset=offset)
        except ProviderError as exc:
            failures.append(exc.to_failure())
            continue
        except Exception as exc:
            logger.warning(
                "Web search provider %s raised %s",
                provider.name.value,
                type(exc).__name__,
            )
            failures.append(_unexpected_failure(provider.name.value, exc))
            continue

        for result in results:
            key = url_fingerprint(result.url)
            existing = merged.get(key)
            if existing is None or _score(result) > _score(existing):
                merged[key] = result

    ranked = list(merged.values())[:limit]
    ranked = [
        result.model_copy(update={"rank": index})
        for index, result in enumerate(ranked)
    ]
    return SearchBundle(query=query, results=ranked, failures=failures)


def collect_academic_search(
    providers: Iterable[AcademicSearchProvider],
    query: str,
    *,
    limit: int = 10,
    offset: int = 0,
) -> AcademicBundle:
    limit = max(1, limit)
    offset = max(0, offset)
    merged: dict[str, AcademicResult] = {}
    failures: list[ProviderFailure] = []

    for provider in providers:
        if not provider.enabled:
            continue
        try:
            results = provider.search_works(query, limit=limit, offset=offset)
        except ProviderError as exc:
            failures.append(exc.to_failure())
            continue
        except Exception as exc:
            logger.warning(
                "Academic provider %s raised %s",
                provider.name.value,
                type(exc).__name__,
            )
            failures.append(_unexpected_failure(provider.name.value, exc))
            continue

        for result in results:
            key = _academic_key(result)
            existing = merged.get(key)
            merged[key] = _merge_academic(existing, result) if existing else result

    ranked = list(merged.values())[:limit]
    ranked = [
        result.model_copy(update={"rank": index})
        for index, result in enumerate(ranked)
    ]
    return AcademicBundle(query=query, results=ranked, failures=failures)


def lookup_academic_work(
    providers: Iterable[AcademicSearchProvider],
    identifier: str,
) -> tuple[AcademicResult | None, list[ProviderFailure]]:
    failures: list[ProviderFailure] = []
    for provider in providers:
        if not provider.enabled:
            continue
        try:
            result = provider.get_work(identifier)
        except ProviderError as exc:
            failures.append(exc.to_failure())
            continue
        except Exception as exc:
            failures.append(_unexpected_failure(provider.name.value, exc))
            continue
        if result is not None:
            return result, failures
    return None, failures


def collect_references(
    providers: Iterable[AcademicSearchProvider],
    identifier: str,
    *,
    limit: int = 50,
) -> tuple[list[AcademicReference], list[ProviderFailure]]:
    merged: dict[str, AcademicReference] = {}
    failures: list[ProviderFailure] = []
    for provider in providers:
        if not provider.enabled:
            continue
        try:
            references = provider.get_references(identifier, limit=limit)
        except ProviderError as exc:
            failures.append(exc.to_failure())
            continue
        except Exception as exc:
            failures.append(_unexpected_failure(provider.name.value, exc))
            continue
        for reference in references:
            key = _reference_key(reference)
            merged.setdefault(key, reference)
    return list(merged.values())[:limit], failures


def collect_citations(
    providers: Iterable[AcademicSearchProvider],
    identifier: str,
    *,
    limit: int = 50,
) -> tuple[list[AcademicResult], list[ProviderFailure]]:
    merged: dict[str, AcademicResult] = {}
    failures: list[ProviderFailure] = []
    for provider in providers:
        if not provider.enabled or not provider.supports_citations:
            continue
        try:
            results = provider.get_citations(identifier, limit=limit)
        except ProviderError as exc:
            failures.append(exc.to_failure())
            continue
        except Exception as exc:
            failures.append(_unexpected_failure(provider.name.value, exc))
            continue
        for result in results:
            merged.setdefault(_academic_key(result), result)
    return list(merged.values())[:limit], failures


def fetch_web_content(
    providers: Iterable[WebContentProvider],
    url: str,
) -> tuple[WebFetchResult | None, list[ProviderFailure]]:
    failures: list[ProviderFailure] = []
    last_result: WebFetchResult | None = None
    for provider in providers:
        if not provider.enabled:
            continue
        try:
            result = provider.fetch(url)
        except ProviderError as exc:
            failures.append(exc.to_failure())
            continue
        except Exception as exc:
            logger.warning(
                "Content provider %s raised %s",
                provider.name.value,
                type(exc).__name__,
            )
            failures.append(_unexpected_failure(provider.name.value, exc))
            continue

        last_result = result
        if result.status in {FetchStatus.SUCCESS, FetchStatus.ABSTRACT_ONLY}:
            return result, failures
        failures.append(
            ProviderFailure(
                provider=result.provider.value,
                code=result.error_code or "fetch_failed",
                message=result.error_message or "Content fetch failed",
                retryable=False,
                status_code=result.http_status,
            )
        )
    return last_result, failures


def _score(result: SearchResult) -> float:
    return result.score if result.score is not None else -1.0


def _academic_key(result: AcademicResult) -> str:
    if result.doi:
        return f"doi:{result.doi}"
    year = result.published_year or 0
    return f"title:{normalized_text(result.title)}:{year}"


def _reference_key(reference: AcademicReference) -> str:
    if reference.doi:
        return f"doi:{reference.doi}"
    title = reference.title or reference.unstructured or ""
    return f"title:{normalized_text(title)}:{reference.year or 0}"


def _merge_academic(
    existing: AcademicResult,
    candidate: AcademicResult,
) -> AcademicResult:
    updates: dict = {}
    if not existing.authors and candidate.authors:
        updates["authors"] = candidate.authors
    if existing.abstract is None and candidate.abstract:
        updates["abstract"] = candidate.abstract
    if existing.url is None and candidate.url:
        updates["url"] = candidate.url
    if existing.open_access_url is None and candidate.open_access_url:
        updates["open_access_url"] = candidate.open_access_url
    if existing.venue is None and candidate.venue:
        updates["venue"] = candidate.venue
    if existing.publisher is None and candidate.publisher:
        updates["publisher"] = candidate.publisher
    if existing.published_at is None and candidate.published_at:
        updates["published_at"] = candidate.published_at
    if existing.citation_count is None:
        updates["citation_count"] = candidate.citation_count
    elif candidate.citation_count is not None:
        updates["citation_count"] = max(
            existing.citation_count,
            candidate.citation_count,
        )
    if existing.score is None:
        updates["score"] = candidate.score
    elif candidate.score is not None:
        updates["score"] = max(existing.score, candidate.score)
    return existing.model_copy(update=updates) if updates else existing


def _unexpected_failure(provider: str, exc: Exception) -> ProviderFailure:
    return ProviderFailure(
        provider=provider,
        code="unexpected_error",
        message=f"{type(exc).__name__}",
        retryable=False,
    )
