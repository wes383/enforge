from __future__ import annotations

import logging
from typing import Any, Sequence

import httpx

logger = logging.getLogger(__name__)


class LangfuseIngestionClient:
    """Minimal Langfuse ingestion client.

    The client batches structured observations and sends them to the public
    ingestion endpoint. Observability must never break research execution, so
    transport and response errors are logged without payloads and swallowed.
    """

    def __init__(
        self,
        *,
        host: str,
        public_key: str,
        secret_key: str,
        timeout_seconds: float = 5.0,
        batch_size: int = 50,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = f"{host.rstrip('/')}/api/public/ingestion"
        self._public_key = public_key
        self._secret_key = secret_key
        self._batch_size = max(1, batch_size)
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._reported_failure = False

    @property
    def endpoint(self) -> str:
        return self._url

    def send_batch(self, events: Sequence[dict[str, Any]]) -> bool:
        if not events:
            return True
        success = True
        for start in range(0, len(events), self._batch_size):
            batch = list(events[start : start + self._batch_size])
            try:
                response = self._client.post(
                    self._url,
                    json={"batch": batch},
                    auth=(self._public_key, self._secret_key),
                )
            except Exception as exc:
                success = False
                self._log_failure(type(exc).__name__)
                continue
            if response.status_code >= 400:
                success = False
                self._log_failure(f"HTTP {response.status_code}")
                continue
            if response.status_code == 207:
                try:
                    errors = response.json().get("errors") or []
                except ValueError:
                    errors = []
                if errors:
                    success = False
                    logger.warning(
                        "Langfuse ingestion reported %d event error(s); "
                        "research execution is unaffected",
                        len(errors),
                    )
        if success:
            self._reported_failure = False
        return success

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _log_failure(self, reason: str) -> None:
        if self._reported_failure:
            return
        self._reported_failure = True
        logger.warning(
            "Langfuse ingestion unavailable (%s); traces are dropped",
            reason,
        )
