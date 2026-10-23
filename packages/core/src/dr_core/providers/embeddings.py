from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import SecretStr

from dr_core.providers.errors import ProviderResponseError
from dr_core.providers.http import JsonHttpClient, RetryPolicy

_DEFAULT_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class OpenAICompatibleEmbeddingProvider:
    """Embeddings adapter for OpenAI-compatible endpoints.

    The provider is operator-configured and only used when a dedicated
    embedding key is present; the chat key is never reused for embeddings.
    """

    def __init__(
        self,
        api_key: str | SecretStr | None,
        *,
        base_url: str = "https://api.openai.com/v1",
        model: str = "text-embedding-3-small",
        dimension: int | None = None,
        timeout_seconds: float = 30.0,
        connect_timeout_seconds: float = 5.0,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._api_key = (
            api_key.get_secret_value()
            if isinstance(api_key, SecretStr)
            else (api_key or "")
        )
        self._model = model
        self._dimension = dimension or _DEFAULT_DIMENSIONS.get(model, 1536)
        self._http = JsonHttpClient(
            "openai",
            base_url=base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            retry_policy=retry_policy,
        )

    @property
    def name(self) -> str:
        return self._model

    @property
    def enabled(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def close(self) -> None:
        self._http.close()

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        request: dict[str, Any] = {
            "model": self._model,
            "input": list(texts),
        }
        if self._model.startswith("text-embedding-3"):
            # Only the v3 models accept a target dimension; other endpoints
            # reject the field outright.
            request["dimensions"] = self._dimension
        payload = self._http.request_json("POST", "/embeddings", json=request)
        if not isinstance(payload, dict):
            raise ProviderResponseError(
                "openai",
                "embedding response is missing the payload object",
            )
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise ProviderResponseError(
                "openai",
                "embedding response is missing the data array",
            )
        vectors: list[list[float]] = []
        for entry in data:
            if not isinstance(entry, dict):
                raise ProviderResponseError(
                    "openai",
                    "embedding response contains an invalid entry",
                )
            embedding = entry.get("embedding")
            if not isinstance(embedding, list) or not embedding:
                raise ProviderResponseError(
                    "openai",
                    "embedding response contains no vector",
                )
            if len(embedding) != self._dimension:
                raise ProviderResponseError(
                    "openai",
                    f"embedding response has {len(embedding)} dimensions, "
                    f"expected {self._dimension}",
                )
            try:
                vectors.append([float(value) for value in embedding])
            except (TypeError, ValueError) as exc:
                raise ProviderResponseError(
                    "openai",
                    "embedding response contains a non-numeric vector",
                ) from exc
        if len(vectors) != len(texts):
            raise ProviderResponseError(
                "openai",
                "embedding response count does not match the request",
            )
        return vectors
