from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol


class ProviderCache(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, ttl_seconds: float) -> None: ...


class InMemoryProviderCache:
    def __init__(
        self,
        *,
        max_entries: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_entries = max_entries
        self._clock = clock
        self._entries: dict[str, tuple[float, str]] = {}
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> str | None:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._entries.pop(key, None)
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key: str, value: str, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        expires_at = self._clock() + ttl_seconds
        with self._lock:
            self._entries.pop(key, None)
            self._entries[key] = (expires_at, value)
            self._evict(self._clock())

    def _evict(self, now: float) -> None:
        expired = [
            key for key, (expires_at, _) in self._entries.items() if expires_at <= now
        ]
        for key in expired:
            self._entries.pop(key, None)
        while len(self._entries) > self._max_entries:
            oldest = min(self._entries, key=lambda item: self._entries[item][0])
            self._entries.pop(oldest, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def cache_key(
    provider: str,
    operation: str,
    *parts: Any,
    **params: Any,
) -> str:
    payload = json.dumps(
        {"parts": parts, "params": params},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"provider:{provider}:{operation}:{digest}"


def get_or_load_json(
    cache: ProviderCache | None,
    key: str,
    ttl_seconds: float,
    loader: Callable[[], Any],
) -> Any:
    if cache is None or ttl_seconds <= 0:
        return loader()
    cached = cache.get(key)
    if cached is not None:
        return json.loads(cached)
    value = loader()
    cache.set(
        key,
        json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")),
        ttl_seconds,
    )
    return value
