from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

from dr_core.observability.tracing import ResearchTracer, TraceSpan
from dr_core.providers.models import (
    AcademicReference,
    AcademicResult,
    LLMResponse,
    ParsedDocument,
    SearchResult,
    WebFetchResult,
)


def _label(provider: Any) -> str:
    name = getattr(provider, "name", None)
    return str(getattr(name, "value", name) or type(provider).__name__)


def _attempts(provider: Any) -> int | None:
    http = getattr(provider, "_http", None)
    attempts = getattr(http, "last_attempts", None)
    return int(attempts) if isinstance(attempts, int) and attempts > 0 else None


def _retry_metadata(provider: Any, span: TraceSpan) -> None:
    attempts = _attempts(provider)
    if attempts is not None:
        span.update(metadata={"attempts": attempts, "retries": max(0, attempts - 1)})


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


class _TracedSearchProvider:
    def __init__(self, provider, tracer: ResearchTracer) -> None:
        self._provider = provider
        self._tracer = tracer

    @property
    def name(self):
        return self._provider.name

    @property
    def enabled(self) -> bool:
        return self._provider.enabled

    def close(self) -> None:
        close = getattr(self._provider, "close", None)
        if callable(close):
            close()

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchResult]:
        with self._tracer.span(
            f"search.{_label(self._provider)}",
            metadata={
                "provider": _label(self._provider),
                "operation": "web_search",
                "query_length": len(query),
                "limit": limit,
                "offset": offset,
            },
        ) as span:
            results = self._provider.search(query, limit=limit, offset=offset)
            span.update(
                output={"result_count": len(results)},
                metadata={"result_count": len(results)},
            )
            _retry_metadata(self._provider, span)
            return results


class _TracedAcademicProvider:
    def __init__(self, provider, tracer: ResearchTracer) -> None:
        self._provider = provider
        self._tracer = tracer

    @property
    def name(self):
        return self._provider.name

    @property
    def enabled(self) -> bool:
        return self._provider.enabled

    @property
    def supports_citations(self) -> bool:
        return bool(getattr(self._provider, "supports_citations", False))

    def close(self) -> None:
        close = getattr(self._provider, "close", None)
        if callable(close):
            close()

    def search_works(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[AcademicResult]:
        with self._tracer.span(
            f"academic_search.{_label(self._provider)}",
            metadata={
                "provider": _label(self._provider),
                "operation": "academic_search",
                "query_length": len(query),
                "limit": limit,
                "offset": offset,
            },
        ) as span:
            results = self._provider.search_works(query, limit=limit, offset=offset)
            span.update(
                output={"result_count": len(results)},
                metadata={"result_count": len(results)},
            )
            _retry_metadata(self._provider, span)
            return results

    def get_work(self, identifier: str) -> AcademicResult | None:
        with self._tracer.span(
            f"academic_work.{_label(self._provider)}",
            metadata={
                "provider": _label(self._provider),
                "operation": "get_work",
                "identifier_length": len(identifier),
            },
        ) as span:
            result = self._provider.get_work(identifier)
            span.update(output={"found": result is not None})
            _retry_metadata(self._provider, span)
            return result

    def get_references(
        self,
        identifier: str,
        *,
        limit: int = 50,
    ) -> list[AcademicReference]:
        with self._tracer.span(
            f"academic_references.{_label(self._provider)}",
            metadata={
                "provider": _label(self._provider),
                "operation": "get_references",
                "limit": limit,
            },
        ) as span:
            results = self._provider.get_references(identifier, limit=limit)
            span.update(output={"result_count": len(results)})
            _retry_metadata(self._provider, span)
            return results

    def get_citations(
        self,
        identifier: str,
        *,
        limit: int = 50,
    ) -> list[AcademicResult]:
        with self._tracer.span(
            f"academic_citations.{_label(self._provider)}",
            metadata={
                "provider": _label(self._provider),
                "operation": "get_citations",
                "limit": limit,
            },
        ) as span:
            results = self._provider.get_citations(identifier, limit=limit)
            span.update(output={"result_count": len(results)})
            _retry_metadata(self._provider, span)
            return results


class _TracedContentProvider:
    def __init__(self, provider, tracer: ResearchTracer) -> None:
        self._provider = provider
        self._tracer = tracer

    @property
    def name(self):
        return self._provider.name

    @property
    def enabled(self) -> bool:
        return self._provider.enabled

    def close(self) -> None:
        close = getattr(self._provider, "close", None)
        if callable(close):
            close()

    def fetch(self, url: str) -> WebFetchResult:
        with self._tracer.span(
            f"content_fetch.{_label(self._provider)}",
            metadata={
                "provider": _label(self._provider),
                "operation": "fetch",
                "host": _host(url),
            },
        ) as span:
            result = self._provider.fetch(url)
            span.update(
                output={
                    "status": result.status.value,
                    "http_status": result.http_status,
                    "text_length": len(result.text),
                },
                metadata={"status": result.status.value},
            )
            _retry_metadata(self._provider, span)
            return result


class _TracedParser:
    def __init__(self, provider, tracer: ResearchTracer) -> None:
        self._provider = provider
        self._tracer = tracer

    @property
    def name(self) -> str:
        return self._provider.name

    @property
    def version(self) -> str:
        return self._provider.version

    def parse(
        self,
        content: bytes,
        *,
        mime_type: str,
        filename: str | None = None,
    ) -> ParsedDocument:
        with self._tracer.span(
            f"document_parse.{_label(self._provider)}",
            metadata={
                "provider": _label(self._provider),
                "operation": "parse",
                "mime_type": mime_type,
                "input_bytes": len(content),
            },
        ) as span:
            result = self._provider.parse(
                content,
                mime_type=mime_type,
                filename=filename,
            )
            span.update(
                output={
                    "status": result.status.value,
                    "character_count": result.character_count,
                }
            )
            return result


class _TracedEmbeddingProvider:
    def __init__(self, provider, tracer: ResearchTracer) -> None:
        self._provider = provider
        self._tracer = tracer

    @property
    def model_name(self) -> str:
        return self._provider.model_name

    @property
    def dimension(self) -> int:
        return self._provider.dimension

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        with self._tracer.span(
            "embedding",
            metadata={
                "provider": type(self._provider).__name__,
                "model": self._provider.model_name,
                "operation": "embed",
                "batch_size": len(texts),
            },
        ) as span:
            vectors = self._provider.embed_texts(texts)
            span.update(output={"vector_count": len(vectors)})
            return vectors


class _TracedLLMProvider:
    def __init__(self, provider, tracer: ResearchTracer) -> None:
        self._provider = provider
        self._tracer = tracer

    @property
    def name(self) -> str:
        return self._provider.name

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        metadata: dict[str, Any] = {
            "provider": self._provider.name,
            "operation": "llm.generate",
            "prompt_chars": len(prompt),
            "system_chars": len(system or ""),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if self._tracer.include_content:
            metadata["input"] = prompt[:2000]
        return self._traced_call(
            "llm.generate",
            metadata=metadata,
            temperature=temperature,
            max_tokens=max_tokens,
            call=lambda: self._provider.generate(
                prompt,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            ),
        )

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        metadata: dict[str, Any] = {
            "provider": self._provider.name,
            "operation": "llm.chat",
            "message_count": len(messages),
            "tool_count": len(tools or []),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if self._tracer.include_content:
            metadata["input"] = json.dumps(
                messages[-3:],
                ensure_ascii=False,
                default=str,
            )[:2000]
        return self._traced_call(
            "llm.chat",
            metadata=metadata,
            temperature=temperature,
            max_tokens=max_tokens,
            call=lambda: self._provider.chat(
                messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
            ),
        )

    def _traced_call(
        self,
        operation: str,
        *,
        metadata: dict[str, Any],
        temperature: float,
        max_tokens: int | None,
        call: Callable[[], LLMResponse],
    ) -> LLMResponse:
        with self._tracer.generation(
            operation,
            model=self._provider.name,
            metadata=metadata,
            model_parameters={
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        ) as span:
            response = call()
            output: dict[str, Any] = {
                "response_chars": len(response.text),
                "finish_reason": response.finish_reason,
            }
            if response.tool_calls:
                output["tool_calls"] = len(response.tool_calls)
            if self._tracer.include_content:
                output["output"] = response.text[:2000]
            span.update(
                output=output,
                usage={
                    "input": response.input_tokens,
                    "output": response.output_tokens,
                    "total": response.input_tokens + response.output_tokens,
                },
                metadata={"model": response.model},
            )
            span.model = response.model
            return response


def instrument_registry(registry, tracer: ResearchTracer) -> None:
    registry.web_search = [
        _TracedSearchProvider(provider, tracer)
        for provider in registry.web_search
    ]
    registry.academic_search = [
        _TracedAcademicProvider(provider, tracer)
        for provider in registry.academic_search
    ]
    registry.web_content = [
        _TracedContentProvider(provider, tracer)
        for provider in registry.web_content
    ]
    registry.parsers = [
        _TracedParser(provider, tracer) for provider in registry.parsers
    ]
    if registry.embedding is not None:
        registry.embedding = _TracedEmbeddingProvider(registry.embedding, tracer)
    if registry.llm is not None:
        registry.llm = _TracedLLMProvider(registry.llm, tracer)
