import json

import httpx
import pytest

from dr_core.config.settings import Settings
from dr_core.db.enums import SearchProviderName
from dr_core.normalization import normalize_url
from dr_core.providers.cache import InMemoryProviderCache
from dr_core.providers.content import FirecrawlProvider
from dr_core.providers.errors import (
    ProviderDisabled,
    ProviderNotConfigured,
    ProviderRequestError,
    ProviderUnavailable,
    UnsafeUrlError,
)
from dr_core.providers.http import RetryPolicy
from dr_core.providers.mocks import (
    MockAcademicSearchProvider,
    MockEmbeddingProvider,
    MockLLMProvider,
    MockSearchProvider,
    MockWebContentProvider,
)
from dr_core.providers.models import (
    AcademicResult,
    ContentProviderName,
    FetchStatus,
    SearchResult,
    WebFetchResult,
)
from dr_core.providers.orchestration import (
    collect_academic_search,
    collect_references,
    collect_web_search,
    fetch_web_content,
    lookup_academic_work,
)
from dr_core.providers.registry import build_provider_registry
from dr_core.providers.search import (
    CrossrefProvider,
    ExaSearchProvider,
    SemanticScholarProvider,
    TavilySearchProvider,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _offline_settings(**overrides) -> Settings:
    """Settings with every external provider pinned off, ignoring ambient env."""
    pinned = {
        "tavily_enabled": False,
        "tavily_api_key": None,
        "exa_enabled": False,
        "exa_api_key": None,
        "firecrawl_enabled": False,
        "firecrawl_api_key": None,
        "crossref_enabled": False,
        "semantic_scholar_enabled": False,
        "semantic_scholar_api_key": None,
        "openai_api_key": None,
    }
    pinned.update(overrides)
    return Settings(_env_file=None, **pinned)


def _email_payload() -> dict:
    return {
        "message": {
            "items": [
                {
                    "DOI": "10.1000/ABC",
                    "title": ["Traceable Evidence"],
                    "author": [
                        {"given": "Ada", "family": "Lovelace"},
                        {"name": "Research Group"},
                    ],
                    "issued": {"date-parts": [[2023, 4, 5]]},
                    "container-title": ["Journal of Tests"],
                    "publisher": "Test Press",
                    "URL": "https://example.com/paper",
                    "abstract": "<jats:p>Abstract text</jats:p>",
                    "reference": [
                        {
                            "DOI": "10.1000/ref",
                            "article-title": "Referenced Work",
                            "year": "2020",
                        }
                    ],
                    "is-referenced-by-count": 12,
                    "references-count": 30,
                    "type": "journal-article",
                }
            ]
        }
    }


def test_tavily_normalizes_paginates_and_caches():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer secret-key"
        assert payload["max_results"] == 3
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "One",
                        "url": "https://example.com/one?utm_source=x",
                        "content": "<b>First</b> snippet",
                        "published_date": "2024-02-03",
                        "score": 1.4,
                    },
                    {
                        "title": "Two",
                        "url": "https://example.com/two",
                        "content": "Second snippet",
                        "score": 0.7,
                    },
                    {
                        "title": "Three",
                        "url": "https://example.com/three",
                        "content": "Third snippet",
                    },
                ]
            },
            request=request,
        )

    provider = TavilySearchProvider(
        "secret-key",
        client=_client(handler),
        cache=InMemoryProviderCache(),
        cache_ttl_seconds=60,
        retry_policy=RetryPolicy(max_attempts=1),
    )
    results = provider.search("traceable evidence", limit=2, offset=1)
    assert [result.title for result in results] == ["Two", "Three"]
    assert [result.rank for result in results] == [1, 2]
    assert results[0].url == "https://example.com/two"
    assert results[0].score == 0.7

    provider.search("traceable evidence", limit=2, offset=1)
    assert len(requests) == 1

    with pytest.raises(ProviderNotConfigured):
        TavilySearchProvider(None).search("query")
    with pytest.raises(ProviderDisabled):
        TavilySearchProvider("key", enabled=False).search("query")


def test_exa_normalizes_summary_and_sends_key_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "exa-key"
        payload = json.loads(request.content)
        assert payload["numResults"] == 4
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Exa result",
                        "url": "https://example.com/exa",
                        "summary": "A compact summary",
                        "publishedDate": "2023-06-01",
                        "score": 0.42,
                        "author": "Author",
                    }
                ]
            },
            request=request,
        )

    provider = ExaSearchProvider(
        "exa-key",
        client=_client(handler),
        retry_policy=RetryPolicy(max_attempts=1),
    )
    result = provider.search("query", limit=4)[0]
    assert result.provider is SearchProviderName.EXA
    assert result.snippet == "A compact summary"
    assert result.published_at.isoformat() == "2023-06-01"
    assert result.score == 0.42


def test_crossref_normalizes_search_work_and_references():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"].startswith("Enforge/0.1")
        assert "mailto@example.com" in request.headers["user-agent"]
        if request.url.path.endswith("/works"):
            assert request.url.params["query.bibliographic"] == "traceable"
            select = request.url.params["select"].split(",")
            assert "references-count" in select
            assert "reference-count" not in select
        return httpx.Response(200, json=_email_payload(), request=request)

    provider = CrossrefProvider(
        mailto="mailto@example.com",
        client=_client(handler),
        retry_policy=RetryPolicy(max_attempts=1),
    )
    result = provider.search_works("traceable", limit=5)[0]
    assert result.doi == "10.1000/abc"
    assert result.authors == ["Ada Lovelace", "Research Group"]
    assert result.abstract == "Abstract text"
    assert result.citation_count == 12
    assert result.reference_count == 30
    assert result.work_type == "journal-article"
    assert result.published_at.isoformat() == "2023-04-05"

    work = provider.get_work("https://doi.org/10.1000/ABC")
    assert work is not None
    assert work.title == "Traceable Evidence"

    references = provider.get_references("10.1000/abc")
    assert references[0].doi == "10.1000/ref"
    assert references[0].title == "Referenced Work"
    assert references[0].year == 2020
    assert provider.get_citations("10.1000/abc") == []
    assert provider.supports_citations is False


def test_semantic_scholar_normalizes_relations_and_missing_work():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "fields" in request.url.params
        if "missing" in request.url.path:
            return httpx.Response(404, request=request)
        if request.url.path.endswith("/references"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "citedPaper": {
                                "paperId": "ref-1",
                                "title": "Referenced Paper",
                                "year": 2020,
                                "externalIds": {"DOI": "10.1000/ref"},
                            }
                        }
                    ]
                },
                request=request,
            )
        if request.url.path.endswith("/citations"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "citingPaper": {
                                "paperId": "cite-1",
                                "title": "Citing Paper",
                                "year": 2025,
                            }
                        }
                    ]
                },
                request=request,
            )
        paper = {
            "paperId": "paper-1",
            "title": "Semantic Work",
            "authors": [{"name": "Ada Lovelace"}],
            "year": 2024,
            "publicationDate": "2024-03-01",
            "venue": "Test Venue",
            "externalIds": {"DOI": "10.1000/s2"},
            "citationCount": 7,
            "referenceCount": 8,
            "openAccessPdf": {"url": "https://example.com/full.pdf"},
            "publicationTypes": ["JournalArticle"],
        }
        if request.url.path.endswith("/paper/search"):
            return httpx.Response(200, json={"data": [paper]}, request=request)
        return httpx.Response(200, json=paper, request=request)

    provider = SemanticScholarProvider(
        client=_client(handler),
        retry_policy=RetryPolicy(max_attempts=1),
    )
    result = provider.search_works("semantic", limit=2)[0]
    assert result.doi == "10.1000/s2"
    assert result.venue == "Test Venue"
    assert result.open_access_url == "https://example.com/full.pdf"
    assert result.work_type == "JournalArticle"

    work = provider.get_work("10.1000/s2")
    assert work is not None
    references = provider.get_references("10.1000/s2")
    assert references[0].title == "Referenced Paper"
    citations = provider.get_citations("10.1000/s2")
    assert citations[0].title == "Citing Paper"

    assert provider.get_work("missing-paper") is None


def _firecrawl_payload(markdown: str, **metadata) -> dict:
    meta = {
        "title": "Fetched title",
        "description": "Description",
        "sourceURL": "https://example.com/article",
        "statusCode": 200,
        **metadata,
    }
    return {"success": True, "data": {"markdown": markdown, "metadata": meta}}


def test_firecrawl_success_and_content_limits():
    payload = _firecrawl_payload("# Heading\n\n" + "Body text. " * 20)

    provider = FirecrawlProvider(
        "firecrawl-key",
        min_content_chars=10,
        max_bytes=10000,
        client=_client(
            lambda request: httpx.Response(200, json=payload, request=request)
        ),
        url_validator=normalize_url,
    )
    result = provider.fetch("https://example.com/article")
    assert result.status is FetchStatus.SUCCESS
    assert result.provider is ContentProviderName.FIRECRAWL
    assert result.title == "Fetched title"
    assert result.content_hash is not None
    assert result.metadata["size_bytes"] > 0

    too_large = FirecrawlProvider(
        "firecrawl-key",
        min_content_chars=0,
        max_bytes=10,
        client=_client(
            lambda request: httpx.Response(200, json=payload, request=request)
        ),
        url_validator=normalize_url,
    )
    oversized = too_large.fetch("https://example.com/article")
    assert oversized.status is FetchStatus.TOO_LARGE
    assert oversized.error_code == "too_large"

    abstract = FirecrawlProvider(
        "firecrawl-key",
        min_content_chars=400,
        max_bytes=10000,
        client=_client(
            lambda request: httpx.Response(
                200,
                json=_firecrawl_payload("Short abstract."),
                request=request,
            )
        ),
        url_validator=normalize_url,
    ).fetch("https://example.com/article")
    assert abstract.status is FetchStatus.ABSTRACT_ONLY
    assert abstract.description == "Description"


def test_firecrawl_paywall_and_unsafe_urls():
    paywalled = FirecrawlProvider(
        "firecrawl-key",
        client=_client(
            lambda request: httpx.Response(
                200,
                json={"success": False, "error": "Subscription required"},
                request=request,
            )
        ),
        url_validator=normalize_url,
    ).fetch("https://example.com/article")
    assert paywalled.status is FetchStatus.PAYWALLED
    assert paywalled.error_code == "paywall"

    blocked_calls: list[int] = []

    def blocked_handler(request: httpx.Request) -> httpx.Response:
        blocked_calls.append(1)
        return httpx.Response(200, json={}, request=request)

    def validator(url: str) -> str:
        if "127.0.0.1" in url:
            raise UnsafeUrlError("firecrawl", "private address")
        return normalize_url(url)

    provider = FirecrawlProvider(
        "firecrawl-key",
        client=_client(blocked_handler),
        url_validator=validator,
    )
    with pytest.raises(UnsafeUrlError):
        provider.fetch("http://127.0.0.1/secret")
    assert blocked_calls == []

    final_private = FirecrawlProvider(
        "firecrawl-key",
        client=_client(
            lambda request: httpx.Response(
                200,
                json=_firecrawl_payload("Text", sourceURL="http://127.0.0.1/"),
                request=request,
            )
        ),
        url_validator=validator,
    ).fetch("https://example.com/article")
    assert final_private.status is FetchStatus.BLOCKED


def test_search_orchestration_isolates_failure_and_deduplicates():
    duplicate = SearchResult(
        provider=SearchProviderName.MOCK,
        title="Duplicate",
        url="https://example.com/shared",
        snippet="Shared result",
        score=0.5,
    )
    better = duplicate.model_copy(update={"score": 0.9})
    failing = MockSearchProvider(
        fail_with=ProviderUnavailable("mock", "offline")
    )
    first = MockSearchProvider(results=[duplicate])
    second = MockSearchProvider(results=[better])

    bundle = collect_web_search([failing, first, second], "query", limit=10)
    assert len(bundle.results) == 1
    assert bundle.results[0].score == 0.9
    assert len(bundle.failures) == 1
    assert bundle.failures[0].code == "unavailable"


def test_academic_orchestration_falls_back_and_merges_metadata():
    first_result = AcademicResult(
        provider=SearchProviderName.MOCK,
        title="Same paper",
        doi="10.1000/same",
    )
    richer = AcademicResult(
        provider=SearchProviderName.MOCK,
        title="Same paper",
        doi="10.1000/same",
        abstract="Richer abstract",
        citation_count=5,
    )
    failing = MockAcademicSearchProvider(
        fail_with=ProviderUnavailable("mock", "offline")
    )
    first = MockAcademicSearchProvider(results=[first_result])
    second = MockAcademicSearchProvider(results=[richer])

    bundle = collect_academic_search([failing, first, second], "query")
    assert len(bundle.results) == 1
    assert bundle.results[0].abstract == "Richer abstract"
    assert len(bundle.failures) == 1

    result, failures = lookup_academic_work([failing, first], "10.1000/same")
    assert result is not None
    assert result.title == "Same paper"
    assert len(failures) == 1

    references, _ = collect_references([first], "10.1000/same")
    assert references == []


def test_fetch_orchestration_falls_back_to_next_provider():
    failing = MockWebContentProvider(
        fail_with=ProviderUnavailable("mock", "offline"),
        url_validator=normalize_url,
    )
    success = MockWebContentProvider(url_validator=normalize_url)
    result, failures = fetch_web_content(
        [failing, success],
        "https://example.com/article",
    )
    assert result is not None
    assert result.status is FetchStatus.SUCCESS
    assert len(failures) == 1

    paywalled = MockWebContentProvider(
        status=FetchStatus.PAYWALLED,
        url_validator=normalize_url,
    )
    result, failures = fetch_web_content([paywalled], "https://example.com/a")
    assert result is not None
    assert result.status is FetchStatus.PAYWALLED
    assert failures[0].code == "paywalled"


def test_registry_builds_mocks_or_configured_providers():
    mock_registry = build_provider_registry(
        _offline_settings(),
        use_mocks=True,
    )
    assert mock_registry.web_search
    assert mock_registry.academic_search
    assert mock_registry.web_content
    assert mock_registry.parsers
    assert mock_registry.embedding is not None
    assert mock_registry.llm is not None
    mock_registry.close()

    empty = build_provider_registry(_offline_settings())
    assert empty.web_search == []
    assert empty.academic_search == []
    assert empty.web_content == []
    assert empty.llm is None
    assert empty.parsers
    empty.close()

    configured = build_provider_registry(
        _offline_settings(
            tavily_enabled=True,
            tavily_api_key="secret",
            provider_cache_ttl_seconds=0,
        )
    )
    assert len(configured.web_search) == 1
    assert isinstance(configured.web_search[0], TavilySearchProvider)
    configured.close()

    missing_key = build_provider_registry(
        _offline_settings(tavily_enabled=True)
    )
    assert missing_key.web_search == []
    missing_key.close()


def test_mock_embeddings_and_llm_are_deterministic():
    embeddings = MockEmbeddingProvider(dimension=8)
    first = embeddings.embed_texts(["hello", "world"])
    second = embeddings.embed_texts(["hello", "world"])
    assert first == second
    assert len(first[0]) == 8

    llm = MockLLMProvider()
    response = llm.generate("hello")
    assert response.model == "mock-llm"
    assert response.text.startswith("Mock LLM response")
