from __future__ import annotations

from dr_core.providers.models import ProviderFailure


class ProviderError(Exception):
    code = "provider_error"
    retryable = False

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        retryable: bool | None = None,
        retry_after_seconds: float | None = None,
        status_code: int | None = None,
    ) -> None:
        self.provider = provider
        self.retry_after_seconds = retry_after_seconds
        self.status_code = status_code
        if retryable is not None:
            self.retryable = retryable
        super().__init__(message)

    def to_failure(self) -> ProviderFailure:
        return ProviderFailure(
            provider=self.provider,
            code=self.code,
            message=str(self),
            retryable=self.retryable,
            retry_after_seconds=self.retry_after_seconds,
            status_code=self.status_code,
        )


class ProviderNotConfigured(ProviderError):
    code = "not_configured"


class ProviderDisabled(ProviderError):
    code = "disabled"


class ProviderRequestError(ProviderError):
    code = "http_error"


class ProviderResponseError(ProviderError):
    code = "invalid_response"


class ProviderResourceNotFound(ProviderError):
    code = "not_found"


class ProviderUnavailable(ProviderError):
    code = "unavailable"
    retryable = True


class ProviderTimeout(ProviderError):
    code = "timeout"
    retryable = True


class ProviderRateLimited(ProviderError):
    code = "rate_limited"
    retryable = True


class UnsafeUrlError(ProviderError):
    code = "unsafe_url"
    retryable = False


class DocumentParseError(ProviderError):
    code = "parse_failed"
    retryable = False


class UnsupportedDocumentType(DocumentParseError):
    code = "unsupported_type"
