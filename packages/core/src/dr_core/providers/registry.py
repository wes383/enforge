from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from dr_core.config.settings import Settings
from dr_core.embeddings import EmbeddingProvider
from dr_core.providers.base import (
    AcademicSearchProvider,
    DocumentParserProvider,
    LLMProvider,
    SearchProvider,
    WebContentProvider,
)
from dr_core.providers.cache import InMemoryProviderCache, ProviderCache
from dr_core.providers.content import FirecrawlProvider
from dr_core.providers.embeddings import OpenAICompatibleEmbeddingProvider
from dr_core.providers.http import RetryPolicy
from dr_core.providers.llm import OpenAICompatibleLLMProvider
from dr_core.providers.mocks import (
    MockAcademicSearchProvider,
    MockDocumentParser,
    MockEmbeddingProvider,
    MockLLMProvider,
    MockSearchProvider,
    MockWebContentProvider,
)
from dr_core.providers.parsing import LocalDocumentParser
from dr_core.providers.search import (
    CrossrefProvider,
    ExaSearchProvider,
    SemanticScholarProvider,
    TavilySearchProvider,
)

logger = logging.getLogger(__name__)


@dataclass
class ProviderRegistry:
    web_search: list[SearchProvider] = field(default_factory=list)
    academic_search: list[AcademicSearchProvider] = field(default_factory=list)
    web_content: list[WebContentProvider] = field(default_factory=list)
    parsers: list[DocumentParserProvider] = field(default_factory=list)
    embedding: EmbeddingProvider | None = None
    llm: LLMProvider | None = None

    def close(self) -> None:
        for provider in [
            *self.web_search,
            *self.academic_search,
            *self.web_content,
            self.embedding,
            self.llm,
        ]:
            if provider is None:
                continue
            close = getattr(provider, "close", None)
            if callable(close):
                close()


def build_provider_registry(
    settings: Settings,
    *,
    use_mocks: bool = False,
    cache: ProviderCache | None = None,
) -> ProviderRegistry:
    if use_mocks:
        return ProviderRegistry(
            web_search=[MockSearchProvider()],
            academic_search=[MockAcademicSearchProvider()],
            web_content=[MockWebContentProvider()],
            parsers=[MockDocumentParser()],
            embedding=MockEmbeddingProvider(dimension=settings.embedding_dimension),
            llm=MockLLMProvider(),
        )

    if cache is None and settings.provider_cache_ttl_seconds > 0:
        cache = InMemoryProviderCache(
            max_entries=settings.provider_cache_max_entries
        )
    retry_policy = RetryPolicy(
        max_attempts=settings.provider_max_retries + 1,
        base_delay_seconds=settings.provider_retry_backoff_seconds,
        max_delay_seconds=30.0,
    )
    common = {
        "cache": cache,
        "cache_ttl_seconds": float(settings.provider_cache_ttl_seconds),
        "timeout_seconds": settings.provider_request_timeout_seconds,
        "connect_timeout_seconds": settings.provider_connect_timeout_seconds,
        "retry_policy": retry_policy,
    }

    web_search: list[SearchProvider] = []
    if settings.tavily_enabled:
        api_key = _secret(settings.tavily_api_key)
        if api_key:
            web_search.append(TavilySearchProvider(api_key, **common))
        else:
            _warn_missing_key("Tavily", "TAVILY_API_KEY")
    if settings.exa_enabled:
        api_key = _secret(settings.exa_api_key)
        if api_key:
            web_search.append(ExaSearchProvider(api_key, **common))
        else:
            _warn_missing_key("Exa", "EXA_API_KEY")

    academic_search: list[AcademicSearchProvider] = []
    if settings.crossref_enabled:
        academic_search.append(
            CrossrefProvider(
                mailto=settings.crossref_mailto,
                **common,
            )
        )
    if settings.semantic_scholar_enabled:
        academic_search.append(
            SemanticScholarProvider(
                _secret(settings.semantic_scholar_api_key),
                **common,
            )
        )

    web_content: list[WebContentProvider] = []
    if settings.firecrawl_enabled:
        api_key = _secret(settings.firecrawl_api_key)
        if api_key:
            web_content.append(
                FirecrawlProvider(
                    api_key,
                    max_bytes=settings.web_fetch_max_bytes,
                    min_content_chars=settings.web_fetch_min_content_chars,
                    max_concurrency=settings.firecrawl_max_concurrency,
                    timeout_seconds=settings.provider_request_timeout_seconds,
                    connect_timeout_seconds=settings.provider_connect_timeout_seconds,
                    retry_policy=retry_policy,
                )
            )
        else:
            _warn_missing_key("Firecrawl", "FIRECRAWL_API_KEY")

    llm: LLMProvider | None = None
    openai_key = _secret(settings.openai_api_key)
    if openai_key:
        llm = OpenAICompatibleLLMProvider(
            openai_key,
            base_url=settings.openai_base_url,
            model=settings.openai_model,
            timeout_seconds=settings.llm_request_timeout_seconds,
            connect_timeout_seconds=settings.provider_connect_timeout_seconds,
            retry_policy=RetryPolicy(
                max_attempts=settings.llm_max_retries + 1,
                base_delay_seconds=settings.provider_retry_backoff_seconds,
                max_delay_seconds=30.0,
            ),
        )

    embedding: EmbeddingProvider | None = None
    if settings.embedding_enabled:
        embedding_key = _secret(settings.embedding_api_key)
        if embedding_key:
            embedding = OpenAICompatibleEmbeddingProvider(
                embedding_key,
                base_url=settings.embedding_base_url,
                model=settings.embedding_model,
                dimension=settings.embedding_dimension,
                timeout_seconds=settings.embedding_timeout_seconds,
                connect_timeout_seconds=settings.provider_connect_timeout_seconds,
                retry_policy=retry_policy,
            )
        else:
            logger.warning(
                "Embeddings are enabled but EMBEDDING_API_KEY is not "
                "configured; retrieval falls back to keyword search"
            )
    elif settings.research_retrieval_mode != "keyword":
        logger.warning(
            "Retrieval mode is %s but no embedding provider is configured "
            "(set EMBEDDING_ENABLED and EMBEDDING_API_KEY); retrieval falls "
            "back to keyword search",
            settings.research_retrieval_mode,
        )

    return ProviderRegistry(
        web_search=web_search,
        academic_search=academic_search,
        web_content=web_content,
        llm=llm,
        embedding=embedding,
        parsers=[
            LocalDocumentParser(
                max_input_bytes=settings.parser_max_input_bytes,
                max_output_chars=settings.parser_max_output_chars,
                max_pdf_pages=settings.parser_max_pdf_pages,
            )
        ],
    )


def _secret(value: Any) -> str:
    if value is None:
        return ""
    get_secret = getattr(value, "get_secret_value", None)
    if callable(get_secret):
        return str(get_secret()).strip()
    return str(value).strip()


def _warn_missing_key(provider: str, variable: str) -> None:
    logger.warning(
        "%s provider is enabled but %s is not configured; skipping it",
        provider,
        variable,
    )
