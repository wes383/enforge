from datetime import date, datetime, UTC

import httpx
import pytest
from pydantic import ValidationError

from dr_core.db.enums import SearchProviderName
from dr_core.providers.base import AcademicSearchProvider, SearchProvider
from dr_core.providers.cache import (
    InMemoryProviderCache,
    cache_key,
    get_or_load_json,
)
from dr_core.providers.errors import (
    ProviderError,
    ProviderRateLimited,
    ProviderRequestError,
    ProviderResponseError,
    UnsafeUrlError,
)
from dr_core.providers.http import JsonHttpClient, RetryPolicy
from dr_core.providers.mocks import (
    MockAcademicSearchProvider,
    MockSearchProvider,
)
from dr_core.providers.models import (
    AcademicResult,
    ParsedDocument,
    ParseStatus,
    SearchResult,
    WebFetchResult,
    FetchStatus,
)
from dr_core.providers.text import parse_date_value
from dr_core.providers.urls import validate_fetch_url


def test_provider_result_models_normalize_and_validate():
    result = SearchResult(
        provider=SearchProviderName.TAVILY,
        title="Example",
        url="https://example.com/page?utm_source=x&b=2&a=1#section",
        snippet="snippet",
        score=0.8,
    )
    assert result.url == "https://example.com/page?a=1&b=2"
    assert parse_date_value("2024-05-06T12:00:00Z") == date(2024, 5, 6)

    academic = AcademicResult(
        provider=SearchProviderName.CROSSREF,
        title="Paper",
        doi="https://doi.org/10.1000/ABC",
        published_at=date(2022, 4, 1),
    )
    assert academic.doi == "10.1000/abc"
    assert academic.published_year == 2022

    with pytest.raises(ValidationError):
        SearchResult(
            provider=SearchProviderName.TAVILY,
            title="Bad",
            url="file:///etc/passwd",
        )
    with pytest.raises(ValidationError):
        AcademicResult(
            provider=SearchProviderName.CROSSREF,
            title="Bad DOI",
            doi="not-a-doi",
        )


def test_web_fetch_result_hashes_text_and_enforces_status_fields():
    result = WebFetchResult(
        provider="mock",
        status=FetchStatus.SUCCESS,
        url="https://example.com/a",
        text="content",
        fetched_at=datetime.now(tz=UTC),
    )
    assert result.content_hash is not None
    assert len(result.content_hash) == 64

    with pytest.raises(ValidationError):
        WebFetchResult(
            provider="mock",
            status=FetchStatus.FAILED,
            url="https://example.com/a",
            fetched_at=datetime.now(tz=UTC),
        )
    with pytest.raises(ValidationError):
        WebFetchResult(
            provider="mock",
            status=FetchStatus.SUCCESS,
            url="https://example.com/a",
            text="content",
            content_hash="0" * 64,
            fetched_at=datetime.now(tz=UTC),
        )


def test_parsed_document_counts_must_match_text():
    document = ParsedDocument(
        status=ParseStatus.SUCCESS,
        text="hello",
        character_count=5,
        parser_name="test",
        parser_version="1",
    )
    assert document.character_count == len(document.text)
    with pytest.raises(ValidationError):
        ParsedDocument(
            status=ParseStatus.SUCCESS,
            text="hello",
            character_count=99,
            parser_name="test",
            parser_version="1",
        )


def test_provider_errors_convert_to_failures_without_secrets():
    error = ProviderRateLimited(
        "tavily",
        "rate limit exceeded",
        retry_after_seconds=12,
    )
    failure = error.to_failure()
    assert failure.provider == "tavily"
    assert failure.code == "rate_limited"
    assert failure.retryable is True
    assert failure.retry_after_seconds == 12
    assert isinstance(error, ProviderError)


def test_mock_providers_implement_protocols():
    assert isinstance(MockSearchProvider(), SearchProvider)
    assert isinstance(MockAcademicSearchProvider(), AcademicSearchProvider)
    results = MockSearchProvider().search("traceable evidence")
    assert len(results) == 3
    assert results[0].provider is SearchProviderName.MOCK


def test_cache_ttl_hits_and_eviction():
    now = [100.0]
    cache = InMemoryProviderCache(max_entries=2, clock=lambda: now[0])
    calls: list[str] = []

    def load(value: str):
        calls.append(value)
        return {"value": value}

    key = cache_key("mock", "search", "query", limit=3)
    assert get_or_load_json(cache, key, 10, lambda: load("first")) == {
        "value": "first"
    }
    assert get_or_load_json(cache, key, 10, lambda: load("second")) == {
        "value": "first"
    }
    assert calls == ["first"]
    assert cache.hits == 1
    assert cache.misses == 1

    now[0] += 11
    assert get_or_load_json(cache, key, 10, lambda: load("third")) == {
        "value": "third"
    }
    assert calls == ["first", "third"]

    cache.set("a", "1", 100)
    cache.set("b", "2", 100)
    cache.set("c", "3", 100)
    assert len(cache._entries) == 2


def _http_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_http_client_retries_500_and_429_with_backoff():
    attempts: list[int] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(500, request=request)
        if len(attempts) == 2:
            return httpx.Response(
                429,
                headers={"retry-after": "5"},
                request=request,
            )
        return httpx.Response(200, json={"ok": True}, request=request)

    client = JsonHttpClient(
        "test",
        base_url="https://api.example.com",
        client=_http_client(handler),
        retry_policy=RetryPolicy(
            max_attempts=3,
            base_delay_seconds=0.01,
            max_delay_seconds=8,
        ),
        sleep=sleeps.append,
    )
    assert client.request_json("POST", "/search", json={"q": "x"}) == {"ok": True}
    assert len(attempts) == 3
    assert sleeps[0] == pytest.approx(0.01)
    assert sleeps[1] == pytest.approx(5.0)


def test_http_client_raises_rate_limit_after_exhaustion():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "2"},
            request=request,
        )

    client = JsonHttpClient(
        "test",
        base_url="https://api.example.com",
        client=_http_client(handler),
        retry_policy=RetryPolicy(
            max_attempts=2,
            base_delay_seconds=0.01,
        ),
        sleep=lambda _: None,
    )
    with pytest.raises(ProviderRateLimited) as error:
        client.request_json("GET", "/search")
    assert error.value.retry_after_seconds == 2


def test_http_client_does_not_retry_client_errors_or_redirects():
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, request=request)

    client = JsonHttpClient(
        "test",
        base_url="https://api.example.com",
        client=_http_client(handler),
        retry_policy=RetryPolicy(max_attempts=4, base_delay_seconds=0),
        sleep=lambda _: None,
    )
    with pytest.raises(ProviderRequestError):
        client.request_json("GET", "/search")
    assert len(attempts) == 1


def test_http_client_validates_response_shape_and_404_policy():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/missing"):
            return httpx.Response(404, request=request)
        return httpx.Response(
            200,
            content=b"not-json",
            headers={"content-type": "application/json"},
            request=request,
        )

    client = JsonHttpClient(
        "test",
        base_url="https://api.example.com",
        client=_http_client(handler),
        retry_policy=RetryPolicy(max_attempts=1),
    )
    with pytest.raises(ProviderResponseError):
        client.request_json("GET", "/bad")
    assert client.request_json("GET", "/missing", allow_not_found=True) is None


def test_ssrf_validator_blocks_private_hosts_ports_and_credentials():
    public_resolver = lambda _host, _port: ["93.184.216.34"]  # noqa: E731
    assert (
        validate_fetch_url("https://Example.com/a", resolver=public_resolver)
        == "https://example.com/a"
    )

    private_cases = [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data",
        "http://intranet/",
        "http://metadata.google.internal/",
        "http://example.com:22/",
        "http://user:pass@example.com/",
        "file:///etc/passwd",
    ]
    for url in private_cases:
        with pytest.raises(UnsafeUrlError):
            validate_fetch_url(url, resolver=public_resolver)

    for address in (
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "100.64.0.1",
        "0.0.0.0",
        "fd00::1",
        "fe80::1",
        "::1",
    ):
        with pytest.raises(UnsafeUrlError):
            validate_fetch_url(
                "https://example.com/",
                resolver=lambda _host, _port, value=address: [value],
            )


def test_ssrf_validator_rejects_dns_failures():
    def failing_resolver(_host: str, _port: int):
        raise OSError("dns failure")

    with pytest.raises(UnsafeUrlError):
        validate_fetch_url("https://example.com/", resolver=failing_resolver)
