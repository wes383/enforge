from __future__ import annotations

import re
import threading
from datetime import UTC, datetime
from typing import Any, Callable

import httpx
from pydantic import SecretStr

from dr_core.providers.errors import (
    ProviderDisabled,
    ProviderNotConfigured,
    UnsafeUrlError,
)
from dr_core.providers.http import JsonHttpClient, RetryPolicy
from dr_core.providers.models import (
    ContentProviderName,
    FetchStatus,
    WebFetchResult,
)
from dr_core.providers.text import normalize_extracted_text, strip_markup
from dr_core.providers.urls import validate_fetch_url

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_PAYWALL_MARKERS = (
    "paywall",
    "subscription",
    "subscribe to continue",
    "requires subscription",
    "login required",
    "sign in to continue",
    "access denied",
    "forbidden",
)


class FirecrawlProvider:
    name = ContentProviderName.FIRECRAWL

    def __init__(
        self,
        api_key: str | SecretStr | None,
        *,
        enabled: bool = True,
        base_url: str = "https://api.firecrawl.dev",
        max_bytes: int = 2_000_000,
        min_content_chars: int = 400,
        max_concurrency: int = 2,
        timeout_seconds: float = 30,
        connect_timeout_seconds: float = 5,
        retry_policy: RetryPolicy | None = None,
        client: httpx.Client | None = None,
        url_validator: Callable[[str], str] = validate_fetch_url,
    ) -> None:
        self._api_key = (
            api_key.get_secret_value()
            if isinstance(api_key, SecretStr)
            else (api_key or "")
        )
        self._enabled = enabled
        self._max_bytes = max_bytes
        self._min_content_chars = min_content_chars
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        self._validator = url_validator
        self._http = JsonHttpClient(
            self.name.value,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            retry_policy=retry_policy,
            max_response_bytes=max(max_bytes * 2, 1_000_000),
            client=client,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def close(self) -> None:
        self._http.close()

    def fetch(self, url: str) -> WebFetchResult:
        if not self._enabled:
            raise ProviderDisabled(self.name.value, "Firecrawl provider is disabled")
        if not self._api_key:
            raise ProviderNotConfigured(
                self.name.value,
                "FIRECRAWL_API_KEY is not configured",
            )
        try:
            safe_url = self._validator(url)
        except UnsafeUrlError as exc:
            raise UnsafeUrlError(self.name.value, str(exc)) from exc

        with self._semaphore:
            payload = self._http.request_json(
                "POST",
                "/v1/scrape",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "url": safe_url,
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                    "blockAds": True,
                    "removeBase64Images": True,
                },
            )
        if not isinstance(payload, dict):
            return self._failure(
                safe_url,
                "invalid_response",
                "Firecrawl returned an unexpected payload",
            )
        if payload.get("success") is False:
            return self._failure_from_error(
                safe_url,
                payload,
                payload.get("error"),
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            return self._failure(
                safe_url,
                "invalid_response",
                "Firecrawl response is missing data",
            )

        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        raw_markdown = data.get("markdown") or data.get("text") or ""
        if not raw_markdown and data.get("html"):
            raw_markdown = strip_markup(str(data["html"]))
        text = normalize_extracted_text(str(raw_markdown))
        description = _optional_str(metadata.get("description"))
        title = _optional_str(metadata.get("title")) or _title_from_markdown(text)
        http_status = _optional_int(
            metadata.get("statusCode") or data.get("statusCode")
        )
        final_url_value = (
            metadata.get("sourceURL")
            or metadata.get("url")
            or data.get("url")
            or safe_url
        )
        try:
            final_url = self._validator(str(final_url_value))
        except UnsafeUrlError as exc:
            return self._failure(
                safe_url,
                "unsafe_redirect",
                f"Firecrawl returned an unsafe final URL: {exc}",
                status=FetchStatus.BLOCKED,
            )

        if http_status in {401, 402, 403}:
            return self._failure(
                safe_url,
                "paywall",
                "The page requires authentication or a subscription",
                status=FetchStatus.PAYWALLED,
                final_url=final_url,
                http_status=http_status,
                title=title,
                description=description,
            )
        if http_status is not None and http_status >= 400:
            return self._failure(
                safe_url,
                "http_error",
                f"Firecrawl reported HTTP {http_status}",
                final_url=final_url,
                http_status=http_status,
                title=title,
                description=description,
            )

        lowered = text.casefold()
        if any(marker in lowered for marker in _PAYWALL_MARKERS) and len(text) < max(
            self._min_content_chars,
            2000,
        ):
            return self._failure(
                safe_url,
                "paywall",
                "The page appears to be paywalled or requires login",
                status=FetchStatus.PAYWALLED,
                final_url=final_url,
                http_status=http_status,
                title=title,
                description=description,
            )

        size_bytes = len(text.encode("utf-8"))
        if size_bytes > self._max_bytes:
            return self._failure(
                safe_url,
                "too_large",
                "Fetched content exceeds the configured size limit",
                status=FetchStatus.TOO_LARGE,
                final_url=final_url,
                http_status=http_status,
                title=title,
                description=description,
                extra={"size_bytes": size_bytes},
            )

        if len(text) < self._min_content_chars:
            if not description:
                return self._failure(
                    safe_url,
                    "insufficient_content",
                    "Extracted content is below the minimum threshold",
                    final_url=final_url,
                    http_status=http_status,
                    title=title,
                )
            return WebFetchResult(
                provider=self.name,
                status=FetchStatus.ABSTRACT_ONLY,
                url=safe_url,
                final_url=final_url,
                title=title,
                text=text,
                description=description,
                fetched_at=datetime.now(tz=UTC),
                http_status=http_status,
                metadata=self._metadata(metadata, size_bytes, final_url),
            )

        return WebFetchResult(
            provider=self.name,
            status=FetchStatus.SUCCESS,
            url=safe_url,
            final_url=final_url,
            title=title,
            text=text,
            description=description,
            fetched_at=datetime.now(tz=UTC),
            http_status=http_status,
            metadata=self._metadata(metadata, size_bytes, final_url),
        )

    def _failure_from_error(
        self,
        url: str,
        payload: dict[str, Any],
        error: Any,
    ) -> WebFetchResult:
        message = _sanitize_error(str(error or "Firecrawl scrape failed"), self._api_key)
        lowered = message.casefold()
        status = (
            FetchStatus.PAYWALLED
            if any(marker in lowered for marker in _PAYWALL_MARKERS)
            else FetchStatus.FAILED
        )
        return self._failure(
            url,
            "paywall" if status is FetchStatus.PAYWALLED else "scrape_failed",
            message,
            status=status,
        )

    def _failure(
        self,
        url: str,
        code: str,
        message: str,
        *,
        status: FetchStatus = FetchStatus.FAILED,
        final_url: str | None = None,
        http_status: int | None = None,
        title: str | None = None,
        description: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> WebFetchResult:
        metadata = dict(extra or {})
        metadata["firecrawl_error_code"] = code
        return WebFetchResult(
            provider=self.name,
            status=status,
            url=url,
            final_url=final_url,
            title=title,
            description=description,
            fetched_at=datetime.now(tz=UTC),
            http_status=http_status,
            error_code=code,
            error_message=_sanitize_error(message, self._api_key),
            metadata=metadata,
        )

    def _metadata(
        self,
        raw: dict[str, Any],
        size_bytes: int,
        final_url: str,
    ) -> dict[str, Any]:
        allowed = {
            key: raw.get(key)
            for key in (
                "language",
                "contentType",
                "cacheState",
                "sourceURL",
                "url",
            )
            if raw.get(key) is not None
        }
        allowed["size_bytes"] = size_bytes
        allowed["final_url"] = final_url
        return allowed


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= 599 else None


def _title_from_markdown(text: str) -> str | None:
    match = _HEADING_RE.search(text)
    if match:
        title = normalize_extracted_text(match.group(1))
        return title[:5000] if title else None
    return None


def _sanitize_error(message: str, secret: str) -> str:
    if secret:
        message = message.replace(secret, "[redacted]")
    return normalize_extracted_text(message)[:4000] or "Firecrawl scrape failed"
