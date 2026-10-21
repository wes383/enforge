from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Sequence

from dr_core.config import Settings
from dr_core.db.enums import SearchProviderName
from dr_core.evaluation.cases import EvaluationCase
from dr_core.providers.errors import ProviderRateLimited
from dr_core.providers.mocks import (
    MockAcademicSearchProvider,
    MockDocumentParser,
    MockEmbeddingProvider,
    MockLLMProvider,
    MockSearchProvider,
    MockWebContentProvider,
    validate_mock_url,
)
from dr_core.providers.models import (
    AcademicResult,
    ContentProviderName,
    FetchStatus,
    SearchResult,
    WebFetchResult,
)
from dr_core.providers.registry import ProviderRegistry


class UrlTextContentProvider:
    name = ContentProviderName.MOCK

    def __init__(self, texts: dict[str, str], *, title: str) -> None:
        self._texts = texts
        self._title = title

    @property
    def enabled(self) -> bool:
        return True

    def fetch(self, url: str) -> WebFetchResult:
        safe_url = validate_mock_url(url)
        text = self._texts.get(url) or self._texts.get(safe_url) or next(
            iter(self._texts.values()),
            "",
        )
        return WebFetchResult(
            provider=self.name,
            status=FetchStatus.SUCCESS,
            url=safe_url,
            final_url=safe_url,
            title=self._title,
            text=text,
            description=text[:240],
            fetched_at=datetime.now(tz=UTC),
            http_status=200,
            metadata={"mock": True, "evaluation": True},
        )


class AlwaysRateLimitedSearchProvider:
    name = SearchProviderName.MOCK

    @property
    def enabled(self) -> bool:
        return True

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchResult]:
        raise ProviderRateLimited(
            self.name.value,
            "Simulated provider rate limit",
            retry_after_seconds=1.0,
            status_code=429,
        )


def _web_results(
    case: EvaluationCase,
    *,
    snippets: Sequence[str] | None = None,
    count: int = 4,
    host_suffix: str = "evaluation",
) -> list[SearchResult]:
    results: list[SearchResult] = []
    for index in range(count):
        snippet = (
            snippets[index % len(snippets)]
            if snippets
            else f"Deterministic evaluation snippet {index + 1} about {case.topic}."
        )
        results.append(
            SearchResult(
                provider=SearchProviderName.MOCK,
                title=f"Evaluation source {index + 1}: {case.topic[:70]}",
                url=f"https://example.org/{host_suffix}/{case.id}/{index + 1}",
                snippet=snippet,
                published_at=date(2021 + index % 4, 1 + index % 11, 1),
                score=round(0.95 - index * 0.05, 2),
                rank=index,
                metadata={"mock": True, "evaluation": True},
            )
        )
    return results


def _academic_results(
    case: EvaluationCase,
    abstracts: Sequence[str],
    *,
    incomplete: bool = False,
    count: int = 3,
) -> list[AcademicResult]:
    results: list[AcademicResult] = []
    for index in range(count):
        abstract = abstracts[index % len(abstracts)]
        results.append(
            AcademicResult(
                provider=SearchProviderName.MOCK,
                provider_id=f"evaluation-{case.id}-{index + 1}",
                title=f"Evaluation study {index + 1}: {case.topic[:70]}",
                authors=[] if incomplete else ["A. Researcher", "B. Reviewer"],
                doi=f"10.5555/evaluation-{case.id}-{index + 1}",
                url=(
                    None
                    if incomplete
                    else f"https://example.org/academic/{case.id}/{index + 1}"
                ),
                published_year=None if incomplete else 2020 + index,
                published_at=None if incomplete else date(2020 + index, 6, 1),
                venue=None if incomplete else "Journal of Evaluation Studies",
                publisher=None if incomplete else "Evaluation Press",
                abstract=abstract,
                citation_count=None if incomplete else 12 + index,
                work_type="journal-article",
                rank=index,
                metadata={"mock": True, "evaluation": True},
            )
        )
    return results


def _registry(
    case: EvaluationCase,
    settings: Settings,
    *,
    web_search: Sequence[object] | None = None,
    academic_search: Sequence[object] | None = None,
    web_content: Sequence[object] | None = None,
) -> ProviderRegistry:
    return ProviderRegistry(
        web_search=list(
            web_search if web_search is not None else [MockSearchProvider()]
        ),
        academic_search=list(
            academic_search
            if academic_search is not None
            else [MockAcademicSearchProvider()]
        ),
        web_content=list(
            web_content
            if web_content is not None
            else [MockWebContentProvider()]
        ),
        parsers=[MockDocumentParser()],
        embedding=MockEmbeddingProvider(dimension=settings.embedding_dimension),
        llm=MockLLMProvider(),
    )


def build_case_registry(case: EvaluationCase, settings: Settings) -> ProviderRegistry:
    if case.scenario == "academic_review":
        abstracts = [
            (
                f"Academic evidence about {case.topic} reports improved answer "
                "faithfulness when retrieval quality and citation checks are "
                "applied. The study compares benchmark results across systems."
            ),
            (
                f"A second review of {case.topic} finds that evaluation methods "
                "vary widely and that citation support should be measured with "
                "traceable source spans."
            ),
        ]
        return _registry(
            case,
            settings,
            academic_search=[
                MockAcademicSearchProvider(
                    results=_academic_results(case, abstracts, count=4)
                )
            ],
        )

    if case.scenario == "incomplete_metadata":
        abstracts = [
            (
                f"Citation counts for {case.topic} vary substantially across "
                "indexes because coverage and document matching differ."
            )
        ]
        return _registry(
            case,
            settings,
            web_search=[],
            academic_search=[
                MockAcademicSearchProvider(
                    results=_academic_results(
                        case,
                        abstracts,
                        incomplete=True,
                        count=4,
                    )
                )
            ],
            web_content=[],
        )

    if case.scenario in {"controversial", "conflicting_literature"}:
        support_sentence = (
            "Enforcement improves compliance in regulated industries because "
            "inspections increase detection and deterrence."
            if "enforcement" in case.topic.casefold()
            else "A higher minimum wage reduces employment because employers cut hours."
        )
        refute_sentence = (
            "However, enforcement does not improve compliance in regulated "
            "industries; firms adapt and violations persist."
            if "enforcement" in case.topic.casefold()
            else (
                "Contrary evidence shows that a higher minimum wage does not reduce "
                "employment; the study refutes the predicted job losses."
            )
        )
        abstracts = [support_sentence, refute_sentence]
        if case.scenario == "conflicting_literature":
            return _registry(
                case,
                settings,
                web_search=[],
                academic_search=[
                    MockAcademicSearchProvider(
                        results=_academic_results(case, abstracts, count=4)
                    )
                ],
                web_content=[],
            )
        results = _web_results(
            case,
            snippets=[support_sentence, refute_sentence],
            count=6,
        )
        texts = {
            result.url: (
                support_sentence + " " + support_sentence
                if index % 2 == 0
                else refute_sentence + " " + refute_sentence
            )
            for index, result in enumerate(results)
        }
        return _registry(
            case,
            settings,
            web_search=[MockSearchProvider(results=results)],
            academic_search=[MockAcademicSearchProvider(results=[])],
            web_content=[UrlTextContentProvider(texts, title=case.topic)],
        )

    if case.scenario == "abstract_only":
        abstract = (
            f"Short abstract about {case.topic}: green space is associated with "
            "lower stress in observational studies, but causal evidence is limited."
        )
        return _registry(
            case,
            settings,
            web_content=[
                MockWebContentProvider(
                    status=FetchStatus.ABSTRACT_ONLY,
                    text=abstract,
                    description=abstract,
                )
            ],
        )

    if case.scenario == "snippet_conflict":
        snippet = (
            "Search snippet claims the full text is unavailable and that "
            "enforcement has no effect on compliance."
        )
        full_text = (
            "The verified full text shows that enforcement improves compliance "
            "in regulated industries. Inspections increase detection and "
            "deterrence across the studied firms. The earlier search snippet was "
            "incomplete and did not reflect the published findings."
        )
        results = _web_results(case, snippets=[snippet], count=4)
        texts = {result.url: full_text for result in results}
        return _registry(
            case,
            settings,
            web_search=[MockSearchProvider(results=results)],
            academic_search=[MockAcademicSearchProvider(results=[])],
            web_content=[UrlTextContentProvider(texts, title=case.topic)],
        )

    if case.scenario == "rate_limited":
        return _registry(
            case,
            settings,
            web_search=[AlwaysRateLimitedSearchProvider()],
        )

    return _registry(case, settings)
