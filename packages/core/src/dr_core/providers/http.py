from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from dr_core.providers.errors import (
    ProviderRateLimited,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeout,
    ProviderUnavailable,
)

RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
_MAX_RETRY_AFTER_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        backoff = min(
            self.base_delay_seconds * (2 ** max(attempt - 1, 0)),
            self.max_delay_seconds,
        )
        if retry_after is not None:
            backoff = max(backoff, retry_after)
        return min(backoff, _MAX_RETRY_AFTER_SECONDS)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        seconds = (target - datetime.now(tz=UTC)).total_seconds()
    return max(0.0, min(seconds, _MAX_RETRY_AFTER_SECONDS))


class JsonHttpClient:
    def __init__(
        self,
        provider: str,
        *,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 20.0,
        connect_timeout_seconds: float = 5.0,
        retry_policy: RetryPolicy | None = None,
        max_response_bytes: int = 10 * 1024 * 1024,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self._headers = dict(headers or {})
        self._timeout = httpx.Timeout(
            timeout_seconds,
            connect=connect_timeout_seconds,
        )
        self._retry_policy = retry_policy or RetryPolicy()
        self._max_response_bytes = max_response_bytes
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=self._timeout,
            follow_redirects=False,
        )
        self._sleep = sleep
        self.last_attempts = 0

    def __enter__(self) -> "JsonHttpClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        merged_headers = {**self._headers, **(headers or {})}
        attempts = max(1, self._retry_policy.max_attempts)
        last_timeout: httpx.TimeoutException | None = None
        last_transport_error: httpx.TransportError | None = None

        for attempt in range(1, attempts + 1):
            self.last_attempts = attempt
            try:
                response = self._client.request(
                    method,
                    self._url(path),
                    params=params,
                    json=json,
                    headers=merged_headers,
                )
            except httpx.TimeoutException as exc:
                last_timeout = exc
                if attempt < attempts:
                    self._sleep(self._retry_policy.delay(attempt))
                    continue
                raise ProviderTimeout(
                    self.provider,
                    f"{self.provider} request timed out",
                ) from exc
            except httpx.TransportError as exc:
                last_transport_error = exc
                if attempt < attempts:
                    self._sleep(self._retry_policy.delay(attempt))
                    continue
                raise ProviderUnavailable(
                    self.provider,
                    f"{self.provider} is unreachable",
                ) from exc

            if len(response.content) > self._max_response_bytes:
                raise ProviderResponseError(
                    self.provider,
                    f"{self.provider} response exceeded the configured size limit",
                )

            if response.status_code == 404 and allow_not_found:
                return None
            if 200 <= response.status_code < 300:
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise ProviderResponseError(
                        self.provider,
                        f"{self.provider} returned invalid JSON",
                    ) from exc
                if not isinstance(payload, dict):
                    raise ProviderResponseError(
                        self.provider,
                        f"{self.provider} returned an unexpected JSON payload",
                    )
                return payload

            if 300 <= response.status_code < 400:
                raise ProviderResponseError(
                    self.provider,
                    f"{self.provider} returned an unexpected redirect",
                )

            retry_after = parse_retry_after(response.headers.get("retry-after"))
            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt < attempts:
                    self._sleep(self._retry_policy.delay(attempt, retry_after))
                    continue
                if response.status_code == 429:
                    raise ProviderRateLimited(
                        self.provider,
                        f"{self.provider} rate limit exceeded",
                        retry_after_seconds=retry_after,
                    )
                raise ProviderUnavailable(
                    self.provider,
                    f"{self.provider} returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )

            raise ProviderRequestError(
                self.provider,
                f"{self.provider} returned HTTP {response.status_code}",
                status_code=response.status_code,
            )

        if last_timeout is not None:
            raise ProviderTimeout(
                self.provider,
                f"{self.provider} request timed out",
            )
        raise ProviderUnavailable(
            self.provider,
            f"{self.provider} is unreachable",
        ) from last_transport_error
