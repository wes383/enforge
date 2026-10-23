from __future__ import annotations

import contextvars
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from langgraph.types import interrupt
from sqlalchemy import func, select

from dr_core.config.settings import LLM_POLISH_CALL_BUDGET, LLM_SECTION_CALL_BUDGET
from dr_core.db.enums import (
    ApprovalKind,
    ApprovalStatus,
    ArtifactType,
    CitationAuditStatus,
    CitationIssueType,
    DraftSectionStatus,
    EvidenceType,
    OutlineStatus,
    ParserStatus,
    ProjectLanguage,
    RunEventType,
    RunStatus,
    SearchQueryStatus,
    SectionCoverageStatus,
    SectionRole,
    SourceOrigin,
    SourceType,
    SourceVersionKind,
)
from dr_core.db.models import (
    OutlineSectionRecord,
    ResearchRun,
    Source,
)
from dr_core.db.repositories import (
    ArtifactRepository,
    CitationAuditRepository,
    ClaimEvidenceRepository,
    ClaimRepository,
    DraftSectionRepository,
    EvidenceRepository,
    OutlineRepository,
    OutlineSectionRepository,
    RunApprovalRepository,
    SearchQueryRepository,
    SourceRepository,
    SourceVersionRepository,
)
from dr_core.normalization import text_sha256
from dr_core.providers.errors import ProviderError
from dr_core.providers.models import FetchStatus
from dr_core.providers.orchestration import fetch_web_content
from dr_core.research.agent import run_research_agent
from dr_core.research.claims import (
    audit_references,
    extract_claims,
    merged_status,
    review_claim_with_llm,
    verify_claim,
)
from dr_core.research.context import (
    NodeTimeoutError,
    ResearchContext,
    RunCancelled,
    RunFinished,
    node_timeout,
)
from dr_core.research.evidence_agent import run_evidence_agent
from dr_core.research.evidence_records import (
    evidence_from_hit as _evidence_from_hit,
    evidence_from_text as _evidence_from_text,
)
from dr_core.research.generation import (
    build_gap_queries,
    build_outline_template,
    build_queries,
    query_pair,
    source_numbering,
)
from dr_core.research.indexing import IndexResult, ensure_source_index
from dr_core.research.matching import SemanticSentenceMatcher
from dr_core.research.models import (
    ApprovalDecision,
    CitationAudit,
    CitationIssue,
    EvidenceMatrix,
    Outline,
    OutlineSection,
    ReferenceAudit,
    ResearchBrief,
    ResearchGap,
    SearchQuery as SearchQueryModel,
    SourceRecord,
)
from dr_core.research.planning import build_outline_with_llm, outline_token_budget
from dr_core.research.polish import (
    DraftArticleSection,
    polish_draft_sections,
    polish_reason_kind,
)
from dr_core.research.quality import (
    build_evidence_matrix,
    section_evidence_target,
    source_identity,
)
from dr_core.research.reporting import (
    claim_status_counts,
    persist_report_artifacts,
    reference_lines,
    report_markdown,
    source_views,
)
from dr_core.research.retrieval import HybridRetriever, RetrievalFilters
from dr_core.research.source_records import (
    academic_source_record as _academic_source_record,
    merge_duplicate_records as _merge_duplicate_records,
    merge_source_record as _merge_source_record,
    web_source_record as _web_source_record,
)
from dr_core.research.state import ResearchState
from dr_core.research.tools import ResearchToolbox
from dr_core.research.text import best_sentence_span_for_queries, tokenize
from dr_core.research.usage import (
    ProgressReportingLLM,
    ReasoningBudgetLLM,
    UsageRecordingLLM,
)
from dr_core.research.verification import audit_section, verify_run_claims
from dr_core.research.write_search import WriteSearchSession
from dr_core.research.writing import compose_section, summarize_section_content
from dr_core.schemas import (
    DraftSectionCreate,
    OutlineCreate,
    OutlineSectionCreate,
    RunEventCreate,
    SearchQueryCreate,
)
from dr_core.services.errors import PermanentRunError
from dr_core.services.ingestion import (
    ingest_academic_result,
    ingest_web_search_result,
    mark_parse_started,
    persist_parse_failure,
    persist_parsed_document,
    persist_web_fetch,
)
from dr_core.services.runs import lock_run, transition_run

logger = logging.getLogger(__name__)


def parse_brief(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            project = ctx.project()
            max_rounds = (
                1
                if project.research_depth.value == "quick"
                else ctx.settings.research_max_search_rounds
            )
            max_sources = ctx.settings.research_max_sources
            if project.research_depth.value == "quick":
                max_sources = min(max_sources, 8)
            brief = ResearchBrief(
                project_id=project.id,
                run_id=ctx.run_id,
                title=project.title,
                topic=project.topic,
                research_scope=project.research_scope,
                article_type=project.article_type,
                language=project.language,
                target_audience=project.target_audience,
                research_depth=project.research_depth,
                citation_style=project.citation_style,
                target_words=project.target_words,
                date_from=project.date_from,
                date_to=project.date_to,
                budget_usd=project.budget_usd,
                max_search_rounds=max_rounds,
                max_sources=max_sources,
                max_queries_per_round=ctx.settings.research_max_queries_per_round,
                max_fetches_per_round=ctx.settings.research_max_fetches_per_round,
                max_evidence=ctx.settings.research_max_evidence,
                token_budget=ctx.settings.research_token_budget,
                cost_budget_usd=Decimal(
                    str(ctx.settings.research_cost_budget_usd)
                ),
            )
            return {
                "brief": brief.model_dump(mode="json"),
                "round_index": 1,
                "limitations": [],
                "budget_stopped": False,
                "feedback": {},
                "current_node": "parse_brief",
            }

    return node


def research_agent(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(_research_agent_node_timeout(ctx)):
            ctx.guard()
            brief = _brief(state)
            feedback_state = dict(state.get("feedback") or {})
            feedback = (
                feedback_state.get("feedback")
                if feedback_state.get("decision") == "rejected"
                else None
            )
            previous = _outline_or_none(state)
            version = 1 if previous is None else previous.version + 1
            ctx.emit(
                RunEventType.PHASE_STARTED,
                message="Planning agent started",
                dedupe_key=f"phase:planning-agent:started:{version}",
                stage=RunStatus.PLANNING,
            )
            existing = _source_records(state)
            outcome = None
            agent_llm = _llm_research_agent(ctx)
            if agent_llm is not None:
                toolbox = ResearchToolbox(
                    ctx,
                    brief,
                    max_sources=min(
                        ctx.settings.research_agent_planning_max_sources,
                        ctx.settings.research_agent_max_sources,
                    ),
                    max_searches=ctx.settings.research_agent_max_searches,
                    max_fetches=ctx.settings.research_agent_max_fetches,
                    max_citation_calls=(
                        ctx.settings.research_agent_max_citation_calls
                    ),
                    max_read_chars=ctx.settings.research_agent_read_chars,
                    existing=existing,
                )
                tool_events = 0

                def record_tool(name: str, result: dict) -> None:
                    nonlocal tool_events
                    tool_events += 1
                    results = result.get("results")
                    query = result.get("query")
                    message = f"Planning agent used {name}"
                    if name == "search_sources" and isinstance(query, str) and query:
                        message = f'Planning agent searched "{query[:120]}"'
                    elif name == "read_source":
                        message = "Planning agent read a source"
                    elif name == "fetch_source":
                        message = "Planning agent fetched a page"
                    elif name == "follow_citations":
                        message = "Planning agent followed citations"
                    ctx.emit(
                        RunEventType.PROGRESS,
                        message=message,
                        payload={
                            "tool": name,
                            "query": query,
                            "direction": result.get("direction"),
                            "results": (
                                len(results) if isinstance(results, list) else None
                            ),
                            "source_id": (
                                result.get("source_id")
                                or result.get("from_source_id")
                            ),
                            "title": (
                                result.get("title") or result.get("from_title")
                            ),
                            "status": result.get("status"),
                            "error": (result.get("error") or "")[:300] or None,
                            "tool_calls": tool_events,
                        },
                        dedupe_key=f"planning-agent:tool:{tool_events}",
                        stage=RunStatus.PLANNING,
                    )

                outcome = run_research_agent(
                    brief,
                    agent_llm,
                    toolbox,
                    max_rounds=ctx.settings.research_agent_max_rounds,
                    max_tool_calls=ctx.settings.research_agent_max_tool_calls,
                    version=version,
                    feedback=feedback,
                    max_tokens_floor=ctx.settings.research_agent_max_tokens_floor,
                    on_tool=record_tool,
                )
                if outcome.error:
                    logger.warning(
                        "Planning agent for run %s ended early: %s",
                        ctx.run_id,
                        outcome.error,
                    )

            limitations = list(state.get("limitations") or [])
            sources = list(outcome.sources) if outcome else existing
            outline = outcome.outline if outcome else None
            planner = "agent" if outline is not None else "template"
            if outline is None:
                outliner = _progress_llm(
                    ctx,
                    _llm_outliner(ctx),
                    stage=RunStatus.PLANNING,
                    label="Designing the article outline",
                    key_prefix=f"llm:outline:v{version}",
                    payload={"node": "research_agent", "version": version},
                )
                if outliner is not None:
                    outline = build_outline_with_llm(
                        brief,
                        outliner,
                        version=version,
                        feedback=feedback,
                        background=state.get("recon") or None,
                        max_tokens=outline_token_budget(
                            brief,
                            floor=ctx.settings.research_outline_max_tokens_floor,
                            per_section=(
                                ctx.settings.research_outline_tokens_per_section
                            ),
                        ),
                    )
                    planner = "llm" if outline is not None else "template"
            if outline is None:
                outline = build_outline_template(
                    brief,
                    version=version,
                    feedback=feedback,
                )
                if agent_llm is not None or _llm_outliner(ctx) is not None:
                    limitations = _with_limitations(
                        state,
                        _text(
                            brief,
                            "规划模型未产出可用提纲，已改用确定性模板。",
                            "The planning model did not produce a usable outline, "
                            "so the deterministic template supplied it.",
                        ),
                    )

            outline, outline_id = _persist_outline(
                ctx,
                outline,
                feedback=feedback,
            )
            ctx.emit(
                RunEventType.PHASE_COMPLETED,
                message="Article outline prepared for approval",
                payload={
                    "planner": planner,
                    "version": outline.version,
                    "sections": len(outline.sections),
                    "section_titles": [
                        section.title for section in outline.sections
                    ],
                    "sources": len(sources),
                    "searches": outcome.searches if outcome else 0,
                    "tool_calls": outcome.tool_calls if outcome else 0,
                    "llm_calls": outcome.llm_calls if outcome else 0,
                    "error": outcome.error if outcome else None,
                },
                dedupe_key=f"phase:planning-agent:completed:{outline.version}",
                stage=RunStatus.PLANNING,
            )
            return {
                "outline": outline.model_dump(mode="json"),
                "outline_id": str(outline_id),
                "recon": (
                    state.get("recon")
                    if outcome is None or outcome.recon is None
                    else outcome.recon.model_dump(mode="json")
                ),
                "sources": [
                    record.model_dump(mode="json") for record in sources
                ],
                "limitations": limitations,
                "feedback": {},
                "current_node": "research_agent",
            }

    return node


def generate_queries(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            brief = _brief(state)
            round_index = int(state.get("round_index", 1))
            ctx.ensure_status(
                RunStatus.SEARCHING,
                event_type=RunEventType.PHASE_STARTED,
                message=f"Searching round {round_index} started",
                dedupe_key=f"phase:searching:started:{round_index}",
            )
            queries = build_queries(
                _outline(state),
                round_index=round_index,
                web_providers=_provider_names(ctx.registry.web_search),
                academic_providers=_provider_names(ctx.registry.academic_search),
                limit=brief.max_queries_per_round,
            )
            limitations = _with_limitations(
                state,
                *(
                    [_no_provider_message(brief)]
                    if not queries
                    else []
                ),
            )
            return {
                "queries": [query.model_dump(mode="json") for query in queries],
                "limitations": limitations,
                "current_node": "generate_queries",
            }

    return node


def search_sources(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            brief = _brief(state)
            round_index = int(state.get("round_index", 1))
            sources = _source_records(state)
            by_id = {item.source_id: item for item in sources}
            created = 0
            failures: list[str] = []
            for query_value in state.get("queries", []):
                ctx.guard()
                if len(by_id) >= brief.max_sources:
                    break
                query = SearchQueryModel.model_validate(query_value)
                query_row = _prepare_query_row(ctx, query)
                if query_row is None:
                    continue
                provider = _provider_for_query(ctx, query)
                if provider is None:
                    _mark_query_failed(
                        ctx,
                        query_row.id,
                        f"Provider {query.provider.value} is unavailable",
                    )
                    failures.append(query.provider.value)
                    continue

                remaining = brief.max_sources - len(by_id)
                try:
                    if query.kind == "web":
                        results = provider.search(
                            query.query_text,
                            limit=min(5, remaining),
                        )
                        ingested = [
                            _web_source_record(
                                ingest_web_search_result(
                                    ctx.database,
                                    ctx.project_id,
                                    result,
                                    query=query.query_text,
                                ),
                                result,
                                query.outline_section_id,
                            )
                            for result in results
                        ]
                    else:
                        results = provider.search_works(
                            query.query_text,
                            limit=min(5, remaining),
                        )
                        ingested = [
                            _academic_source_record(
                                ingest_academic_result(
                                    ctx.database,
                                    ctx.project_id,
                                    result,
                                ),
                                result,
                                query.outline_section_id,
                            )
                            for result in results
                        ]
                except ProviderError as exc:
                    _mark_query_failed(
                        ctx,
                        query_row.id,
                        f"{exc.code}: {exc}",
                    )
                    failures.append(query.provider.value)
                    continue

                for record in ingested:
                    existing = by_id.get(record.source_id)
                    if existing is None:
                        by_id[record.source_id] = record
                        created += 1
                    else:
                        by_id[record.source_id] = _merge_source_record(
                            existing,
                            record,
                        )
                _mark_query_completed(ctx, query_row.id, len(ingested))
                if len(by_id) >= brief.max_sources:
                    break

            limitations = _with_limitations(state, *(
                [
                    _text(
                        brief,
                        f"本轮有 {len(failures)} 个检索请求失败，已继续使用其他来源。",
                        f"{len(failures)} search requests failed; other sources were used.",
                    )
                ]
                if failures
                else []
            ))
            ctx.emit(
                RunEventType.PROGRESS,
                message=f"Search round {round_index} completed",
                payload={
                    "round": round_index,
                    "new_sources": created,
                    "total_sources": len(by_id),
                },
                dedupe_key=f"search:round:{round_index}:completed",
                stage=RunStatus.SEARCHING,
            )
            ordered = sorted(by_id.values(), key=lambda item: item.rank)
            return {
                "sources": [item.model_dump(mode="json") for item in ordered],
                "limitations": limitations,
                "current_node": "search_sources",
            }

    return node


def normalize_sources(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            brief = _brief(state)
            records = _merge_duplicate_records(_source_records(state))

            with ctx.database.session() as session:
                uploads = [
                    source
                    for source in SourceRepository(session).list_for_project(
                        ctx.project_id,
                        limit=500,
                    )
                    if source.source_origin is SourceOrigin.USER_UPLOAD
                ]
                for source in uploads:
                    if any(item.source_id == source.id for item in records):
                        continue
                    versions = SourceVersionRepository(session).list_for_source(
                        source.id,
                        project_id=ctx.project_id,
                    )
                    version = next(
                        (
                            item
                            for item in versions
                            if item.storage_key
                            and item.content_kind
                            in {
                                SourceVersionKind.UPLOADED_DOCUMENT,
                                SourceVersionKind.FULL_TEXT,
                            }
                        ),
                        None,
                    )
                    if version is None:
                        continue
                    records.append(
                        SourceRecord(
                            source_id=source.id,
                            source_version_id=version.id,
                            title=source.title,
                            url=source.canonical_url,
                            doi=source.doi,
                            authors=list(source.authors),
                            published_at=source.published_at,
                            source_type=source.source_type,
                            source_origin=source.source_origin,
                            provider="user_upload",
                            score=1.0,
                            has_full_text=True,
                            parser_status=version.parser_status.value,
                            rank=0,
                        )
                    )
            if not records:
                limitations = _with_limitations(
                    state,
                    _text(
                        brief,
                        "检索未返回可用来源。",
                        "Search returned no usable sources.",
                    ),
                )
            else:
                limitations = list(state.get("limitations") or [])
            return {
                "sources": [item.model_dump(mode="json") for item in records],
                "limitations": limitations,
                "current_node": "normalize_sources",
            }

    return node


def rank_sources(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            brief = _brief(state)
            query_tokens = _outline_tokens(_outline(state))
            ranked = []
            current_year = datetime.now(tz=UTC).year
            excluded_by_date = 0
            for record in _source_records(state):
                if record.published_at and brief.date_from:
                    if record.published_at < brief.date_from:
                        excluded_by_date += 1
                        continue
                if record.published_at and brief.date_to:
                    if record.published_at > brief.date_to:
                        excluded_by_date += 1
                        continue
                score = (record.score or 0.35)
                if record.source_origin is SourceOrigin.USER_UPLOAD:
                    score += 1.0
                if record.source_type in {
                    SourceType.JOURNAL_ARTICLE,
                    SourceType.CONFERENCE_PAPER,
                    SourceType.PREPRINT,
                }:
                    score += 0.12
                if record.has_full_text:
                    score += 0.1
                if record.published_at and record.published_at.year >= current_year - 2:
                    score += 0.05
                title_tokens = _tokens(record.title)
                if query_tokens:
                    score += min(0.2, len(query_tokens & title_tokens) / len(query_tokens))
                ranked.append(
                    record.model_copy(update={"rank_score": round(score, 4)})
                )
            ranked.sort(
                key=lambda item: (-(item.rank_score or 0), item.title)
            )
            dropped = max(0, len(ranked) - brief.max_sources)
            selected = [
                item.model_copy(update={"rank": index})
                for index, item in enumerate(ranked[: brief.max_sources])
            ]
            limitations = _with_limitations(
                state,
                *(
                    [
                        _text(
                            brief,
                            f"{excluded_by_date} 个来源的出版日期超出项目时间范围，已排除。",
                            f"{excluded_by_date} sources fell outside the project date range and were excluded.",
                        )
                    ]
                    if excluded_by_date
                    else []
                ),
                *(
                    [
                        _text(
                            brief,
                            f"来源数量超过上限，已保留评分最高的 {brief.max_sources} 个来源。",
                            f"Source count exceeded the limit; kept the top {brief.max_sources}.",
                        )
                    ]
                    if dropped
                    else []
                ),
            )
            return {
                "sources": [item.model_dump(mode="json") for item in selected],
                "limitations": limitations,
                "current_node": "rank_sources",
            }

    return node


# Source types whose landing page is usually a publisher or repository page that
# a content provider cannot read. Their abstract is the only text that will ever
# be available, so it is indexed even when the fetch budget skips them.
_ACADEMIC_SOURCE_TYPES = frozenset(
    {
        SourceType.BOOK,
        SourceType.JOURNAL_ARTICLE,
        SourceType.CONFERENCE_PAPER,
        SourceType.PREPRINT,
    }
)


def fetch_full_text(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            brief = _brief(state)
            ctx.ensure_status(
                RunStatus.PROCESSING_SOURCES,
                event_type=RunEventType.PHASE_STARTED,
                message="Processing selected sources",
                dedupe_key="phase:processing:started",
            )
            records = _source_records(state)
            eligible = [
                record
                for record in records
                if not record.has_full_text and (record.fetch_url or record.url)
            ]
            # Only a fetched source can be quoted, so fetch the ones the
            # outline's search terms point at first.
            sections = _searchable_sections(_outline(state))

            def priority(record) -> int:
                return _fetch_priority(record, sections)

            scholarly = [
                record
                for record in eligible
                if record.source_type in _ACADEMIC_SOURCE_TYPES
            ]
            other = [
                record
                for record in eligible
                if record.source_type not in _ACADEMIC_SOURCE_TYPES
            ]
            scholarly.sort(key=priority, reverse=True)
            other.sort(key=priority, reverse=True)
            # The same lexical score that orders web pages puts every English
            # scholarly title at zero against a Chinese outline, so a reserved
            # share of the budget is the only way its open-access full text is
            # ever fetched.
            budget = brief.max_fetches_per_round
            reserved = min(len(scholarly), max(1, budget // 3)) if scholarly else 0
            eligible = scholarly[:reserved] + other[: budget - reserved]
            if not eligible:
                return {
                    "sources": [item.model_dump(mode="json") for item in records],
                    "current_node": "fetch_full_text",
                }

            providers = [item for item in ctx.registry.web_content if item.enabled]
            if not providers:
                limitations = _with_limitations(
                    state,
                    _text(
                        brief,
                        "未配置网页正文提供商，来源仅使用检索摘要。",
                        "No web content provider is configured; only search snippets are available.",
                    ),
                )
                return {
                    "sources": [item.model_dump(mode="json") for item in records],
                    "limitations": limitations,
                    "current_node": "fetch_full_text",
                }

            urls = [record.fetch_url or record.url for record in eligible]
            fetch_results = {}
            with ThreadPoolExecutor(
                max_workers=min(
                    ctx.settings.research_max_fetch_concurrency,
                    len(urls),
                )
            ) as executor:
                futures = {
                    executor.submit(
                        contextvars.copy_context().run,
                        fetch_web_content,
                        providers,
                        url,
                    ): (record, url)
                    for record, url in zip(eligible, urls)
                }
                for future in futures:
                    record, url = futures[future]
                    try:
                        result, _ = future.result()
                    except Exception as exc:
                        logger.warning(
                            "Content fetch failed for %s: %s",
                            url,
                            type(exc).__name__,
                        )
                        continue
                    if result is not None:
                        fetch_results[record.source_id] = (record, result)

            updated: dict[UUID, SourceRecord] = {
                item.source_id: item for item in records
            }
            failed = 0
            limitations: list[str] = []
            for source_id, (record, result) in fetch_results.items():
                persisted = persist_web_fetch(
                    ctx.database,
                    ctx.project_id,
                    source_id,
                    result,
                    storage=ctx.storage,
                )
                if persisted.version_id and persisted.content_kind is (
                    SourceVersionKind.FULL_TEXT
                ):
                    updated[source_id] = record.model_copy(
                        update={
                            "source_version_id": persisted.version_id,
                            "has_full_text": True,
                            "parser_status": ParserStatus.PENDING.value,
                        }
                    )
                elif persisted.version_id:
                    updated[source_id] = record.model_copy(
                        update={
                            "source_version_id": persisted.version_id,
                            "parser_status": ParserStatus.SKIPPED.value,
                        }
                    )
                else:
                    failed += 1
                    limitations.append(
                        _text(
                            brief,
                            f"来源“{record.title[:80]}”无法获取正文（{result.status.value}）。",
                            f"Full text was unavailable for “{record.title[:80]}” ({result.status.value}).",
                        )
                    )
                ctx.emit(
                    RunEventType.SOURCE_FETCHED,
                    message=f"Source fetch status: {result.status.value}",
                    payload={
                        "source_id": str(source_id),
                        "status": result.status.value,
                        "http_status": result.http_status,
                        "has_full_text": persisted.content_kind
                        is SourceVersionKind.FULL_TEXT,
                    },
                    dedupe_key=f"source:fetched:{source_id}",
                    stage=RunStatus.PROCESSING_SOURCES,
                )

            # An academic source usually points at a publisher page the content
            # provider cannot read, so it is never fetched and never chunked;
            # the retriever cannot return it and its abstract never becomes
            # evidence. Indexing the abstract closes that gap without a network
            # call, and the cross-lingual matcher can then quote it.
            abstracts_indexed = 0
            parser = next(iter(ctx.registry.parsers), None)
            for record in updated.values():
                if record.has_full_text or not record.snippet:
                    continue
                if record.source_type not in _ACADEMIC_SOURCE_TYPES:
                    continue
                indexed = _index_source(ctx, record, parser=parser)
                if indexed.chunk_count and indexed.embedded:
                    abstracts_indexed += 1

            ctx.emit(
                RunEventType.PROGRESS,
                message="Full-text fetch completed",
                payload={
                    "attempted": len(eligible),
                    "fetched": len(fetch_results) - failed,
                    "failed": failed,
                    "abstracts_indexed": abstracts_indexed,
                },
                dedupe_key="sources:fetched:round",
                stage=RunStatus.PROCESSING_SOURCES,
            )
            limitations = _with_limitations(state, *limitations)
            return {
                "sources": [
                    item.model_dump(mode="json") for item in updated.values()
                ],
                "limitations": limitations,
                "current_node": "fetch_full_text",
            }

    return node


def parse_documents(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            brief = _brief(state)
            parser = next(iter(ctx.registry.parsers), None)
            records = _source_records(state)
            updated: list[SourceRecord] = []
            failed = 0
            indexed_chunks = 0
            embedded_chunks = 0
            index_errors = 0
            for record in records:
                ctx.guard()
                if not record.source_version_id:
                    updated.append(record)
                    continue
                with ctx.database.session() as session:
                    version = SourceVersionRepository(session).get(
                        record.source_version_id,
                        project_id=ctx.project_id,
                    )
                    parser_status = version.parser_status
                    mime_type = version.mime_type
                    storage_key = version.storage_key
                    size_bytes = version.size_bytes
                    if parser_status is ParserStatus.PARSED:
                        result = _index_source(
                            ctx,
                            record,
                            parser=parser,
                        )
                        indexed_chunks += result.chunk_count
                        embedded_chunks += int(result.embedded)
                        index_errors += int(bool(result.error))
                        updated.append(
                            record.model_copy(
                                update={"parser_status": parser_status.value}
                            )
                        )
                        continue
                    if storage_key is None:
                        result = _index_source(
                            ctx,
                            record,
                            parser=parser,
                        )
                        indexed_chunks += result.chunk_count
                        embedded_chunks += int(result.embedded)
                        index_errors += int(bool(result.error))
                        updated.append(record)
                        continue
                if parser is None:
                    failed += 1
                    continue
                try:
                    if (
                        size_bytes is not None
                        and size_bytes > ctx.settings.parser_max_input_bytes
                    ):
                        raise ValueError("Document exceeds the parser input limit")
                    content = ctx.storage.read(storage_key)
                    mark_parse_started(
                        ctx.database,
                        ctx.project_id,
                        record.source_version_id,
                        parser_version=parser.version,
                    )
                    parsed = parser.parse(
                        content,
                        mime_type=mime_type or "text/markdown",
                        filename=storage_key,
                    )
                    persist_parsed_document(
                        ctx.database,
                        ctx.project_id,
                        record.source_version_id,
                        parsed,
                    )
                    result = _index_source(
                        ctx,
                        record,
                        parser=parser,
                        parsed=parsed,
                    )
                    indexed_chunks += result.chunk_count
                    embedded_chunks += int(result.embedded)
                    index_errors += int(bool(result.error))
                    updated.append(
                        record.model_copy(
                            update={"parser_status": ParserStatus.PARSED.value}
                        )
                    )
                    ctx.emit(
                        RunEventType.SOURCE_PARSED,
                        message="Source parsed",
                        payload={
                            "source_id": str(record.source_id),
                            "parser_status": ParserStatus.PARSED.value,
                            "page_count": parsed.page_count,
                        },
                        dedupe_key=f"source:parsed:{record.source_version_id}",
                        stage=RunStatus.PROCESSING_SOURCES,
                    )
                except (RunCancelled, RunFinished):
                    raise
                except NodeTimeoutError:
                    raise
                except Exception as exc:
                    failed += 1
                    try:
                        persist_parse_failure(
                            ctx.database,
                            ctx.project_id,
                            record.source_version_id,
                            parser_version=parser.version,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    except Exception:
                        logger.exception(
                            "Unable to persist parse failure for %s",
                            record.source_version_id,
                        )
                    updated.append(
                        record.model_copy(
                            update={"parser_status": ParserStatus.FAILED.value}
                        )
                    )
                    result = _index_source(
                        ctx,
                        record,
                        parser=parser,
                    )
                    indexed_chunks += result.chunk_count
                    embedded_chunks += int(result.embedded)
                    index_errors += int(bool(result.error))
                    ctx.emit(
                        RunEventType.SOURCE_PARSED,
                        message="Source parsing failed",
                        payload={
                            "source_id": str(record.source_id),
                            "parser_status": ParserStatus.FAILED.value,
                        },
                        dedupe_key=f"source:parsed-failed:{record.source_version_id}",
                        stage=RunStatus.PROCESSING_SOURCES,
                    )

            ctx.emit(
                RunEventType.PROGRESS,
                message="Source index updated",
                payload={
                    "round": state.get("round_index", 1),
                    "chunks": indexed_chunks,
                    "embedded": embedded_chunks,
                    "index_errors": index_errors,
                },
                dedupe_key=f"sources:indexed:round:{state.get('round_index', 1)}",
                stage=RunStatus.PROCESSING_SOURCES,
            )
            limitations = _with_limitations(
                state,
                *(
                    [
                        _text(
                            brief,
                            f"{failed} 个来源解析失败，仅使用可用文本或摘要。",
                            f"{failed} sources could not be parsed; available text was used.",
                        )
                    ]
                    if failed
                    else []
                ),
            )
            if index_errors:
                limitations = _with_limitations(
                    {"limitations": limitations},
                    _text(
                        brief,
                        f"{index_errors} 个来源未能建立完整文本索引，相关检索可能不完整。",
                        f"{index_errors} sources could not be fully indexed; retrieval may be incomplete.",
                    ),
                )
            return {
                "sources": [item.model_dump(mode="json") for item in updated],
                "limitations": limitations,
                "current_node": "parse_documents",
            }

    return node


@dataclass
class _SectionEvidenceState:
    """Tracks how well a section is supported by independent sources."""

    support: int = 0
    identities: set[str] = field(default_factory=set)
    source_counts: dict[UUID, int] = field(default_factory=dict)
    quoted: set[str] = field(default_factory=set)
    target_evidence: int = 4
    target_identities: int = 3
    per_source_limit: int = 3

    @property
    def covered(self) -> bool:
        """Enough independent support to call the section covered."""
        return self.support >= 2 and len(self.identities) >= 2

    @property
    def target_reached(self) -> bool:
        """Enough support for what this section has to write about.

        The target scales with the section's own word budget, so a short
        section stops earlier than a long one instead of every section
        sharing one hard-coded floor.
        """
        return (
            self.support >= self.target_evidence
            and len(self.identities) >= self.target_identities
        )

    def can_quote(self, source_id: UUID) -> bool:
        """A source may support one section more than once, but not forever.

        A long article draws several details from the same study or article; a
        single quote per source capped every section at its source count.
        Independence is still counted through ``identities``, so a second quote
        from the same source never counts as a second independent source.
        """
        return self.source_counts.get(source_id, 0) < self.per_source_limit

    def already_quoted(self, sentence: str) -> bool:
        return _quote_key(sentence) in self.quoted

    def remember(self, sentence: str) -> None:
        self.quoted.add(_quote_key(sentence))

    def track(
        self,
        source_id: UUID,
        identity: str,
        *,
        is_refutation: bool,
    ) -> None:
        self.source_counts[source_id] = self.source_counts.get(source_id, 0) + 1
        if is_refutation:
            return
        self.support += 1
        self.identities.add(identity)


def _quote_key(sentence: str) -> str:
    return " ".join(sentence.split()).casefold()[:400]


def _source_identity_for(
    sources: dict[UUID, Any],
    source_id: UUID,
) -> str:
    source = sources.get(source_id)
    if source is None:
        return f"source:{source_id}"
    return source_identity(source)


def extract_evidence(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            brief = _brief(state)
            sections = _searchable_sections(_outline(state))
            ctx.ensure_status(
                RunStatus.BUILDING_EVIDENCE,
                event_type=RunEventType.PHASE_STARTED,
                message="Building evidence",
                dedupe_key="phase:evidence:started",
            )
            evidence_ids = set(state.get("evidence_ids") or [])
            with ctx.database.session() as session:
                existing = EvidenceRepository(session).list_for_run(
                    ctx.project_id,
                    ctx.run_id,
                )
                evidence_ids.update(str(item.id) for item in existing)
                total_evidence = len(existing)
                sources = source_views(session, ctx.project_id)
            section_states: dict[UUID, _SectionEvidenceState] = {}
            for section in sections:
                target_evidence, target_identities = section_evidence_target(
                    section,
                    ctx.settings,
                )
                section_states[section.section_id] = _SectionEvidenceState(
                    target_evidence=target_evidence,
                    target_identities=target_identities,
                    per_source_limit=(
                        ctx.settings.research_section_evidence_per_source
                    ),
                )
            for item in existing:
                if item.outline_section_id is None:
                    continue
                section_state = section_states.get(item.outline_section_id)
                if section_state is None:
                    continue
                section_state.track(
                    item.source_id,
                    _source_identity_for(sources, item.source_id),
                    is_refutation=(
                        item.evidence_type is EvidenceType.REFUTATION
                    ),
                )
            if total_evidence >= brief.max_evidence:
                return {
                    "evidence_ids": sorted(evidence_ids),
                    "current_node": "extract_evidence",
                }

            records = _source_records(state)
            parser = next(iter(ctx.registry.parsers), None)
            created = 0
            skipped = 0
            retrieval_hits = 0
            retrieval_degraded: str | None = None
            retrieval_vector_used = False
            matcher = _semantic_matcher(ctx)
            semantic_matches = 0
            source_version_ids = [
                record.source_version_id
                for record in records
                if record.source_version_id is not None
            ]
            if source_version_ids:
                filters = RetrievalFilters(
                    source_version_ids=source_version_ids,
                    published_from=brief.date_from,
                    published_to=brief.date_to,
                )
                with ctx.database.session() as session:
                    retriever = HybridRetriever(
                        session,
                        ctx.project_id,
                        settings=ctx.settings,
                        embedding=ctx.registry.embedding,
                        run_id=ctx.run_id,
                    )
                    for section in sections:
                        if total_evidence >= brief.max_evidence:
                            break
                        section_state = section_states[section.section_id]
                        if section_state.target_reached:
                            continue
                        queries = _section_queries(section)
                        unproductive = 0
                        for query in queries:
                            if total_evidence >= brief.max_evidence:
                                break
                            if section_state.target_reached:
                                break
                            known = len(section_state.identities)
                            result = retriever.search(
                                query,
                                filters=filters,
                                limit=ctx.settings.research_retrieval_candidate_limit,
                            )
                            retrieval_degraded = (
                                retrieval_degraded or result.degraded_reason
                            )
                            retrieval_vector_used = (
                                retrieval_vector_used or result.vector_used
                            )
                            retrieval_hits += len(result.hits)
                            for hit in result.hits:
                                if total_evidence >= brief.max_evidence:
                                    break
                                if section_state.target_reached:
                                    break
                                if not section_state.can_quote(hit.source_id):
                                    continue
                                match, match_kind, similarity = _match_sentence(
                                    hit.text,
                                    queries,
                                    matcher,
                                )
                                if match is None:
                                    continue
                                sentence, _, start, end = match
                                if section_state.already_quoted(sentence):
                                    continue
                                if match_kind == "semantic":
                                    semantic_matches += 1
                                with ctx.database.session_scope() as session:
                                    item = EvidenceRepository(session).create(
                                        ctx.project_id,
                                        ctx.run_id,
                                        _evidence_from_hit(
                                            hit,
                                            section_id=section.section_id,
                                            sentence=sentence,
                                            start=start,
                                            end=end,
                                            match=match_kind,
                                            similarity=similarity,
                                        ),
                                    )
                                    if str(item.id) in evidence_ids:
                                        continue
                                    evidence_ids.add(str(item.id))
                                total_evidence += 1
                                created += 1
                                section_state.remember(sentence)
                                section_state.track(
                                    hit.source_id,
                                    _source_identity_for(sources, hit.source_id),
                                    is_refutation=(
                                        item.evidence_type
                                        is EvidenceType.REFUTATION
                                    ),
                                )
                            if len(section_state.identities) > known:
                                unproductive = 0
                            else:
                                unproductive += 1
                                if unproductive >= 2:
                                    # Two queries in a row brought no new
                                    # independent source: the section's
                                    # material is exhausted.
                                    break

            pending = [
                section
                for section in sections
                if not section_states[section.section_id].target_reached
            ]
            if pending and total_evidence < brief.max_evidence:
                for record in records:
                    ctx.guard()
                    if total_evidence >= brief.max_evidence:
                        break
                    if not record.source_version_id:
                        continue
                    primary = _source_text(ctx, record, parser)
                    texts: list[tuple[str, str]] = []
                    if primary:
                        texts.append((primary, "source_text"))
                    if record.snippet and record.snippet not in texts:
                        texts.append((record.snippet, "search_snippet"))
                    if not texts:
                        skipped += 1
                        continue
                    source_sections = pending
                    if record.section_ids:
                        source_sections = [
                            section
                            for section in pending
                            if section.section_id in record.section_ids
                        ]
                    for section in source_sections:
                        if total_evidence >= brief.max_evidence:
                            break
                        section_state = section_states[section.section_id]
                        if section_state.target_reached:
                            continue
                        if not section_state.can_quote(record.source_id):
                            continue
                        queries = _section_queries(section)
                        match = None
                        matched_text = None
                        matched_kind = "source_text"
                        match_source = "lexical"
                        similarity: float | None = None
                        for text, kind in texts:
                            match, match_source, similarity = _match_sentence(
                                text,
                                queries,
                                matcher,
                            )
                            if match is not None:
                                matched_text = text
                                matched_kind = kind
                                break
                        if match is None or matched_text is None:
                            continue
                        if match_source == "semantic":
                            semantic_matches += 1
                        sentence, _, start, end = match
                        if section_state.already_quoted(sentence):
                            continue
                        with ctx.database.session_scope() as session:
                            item = EvidenceRepository(session).create(
                                ctx.project_id,
                                ctx.run_id,
                                _evidence_from_text(
                                    record,
                                    section_id=section.section_id,
                                    text=matched_text,
                                    sentence=sentence,
                                    start=start,
                                    end=end,
                                    kind=matched_kind,
                                    match=match_source,
                                    similarity=similarity,
                                ),
                            )
                            if str(item.id) in evidence_ids:
                                continue
                            evidence_ids.add(str(item.id))
                        total_evidence += 1
                        created += 1
                        section_state.remember(sentence)
                        section_state.track(
                            record.source_id,
                            _source_identity_for(sources, record.source_id),
                            is_refutation=(
                                item.evidence_type is EvidenceType.REFUTATION
                            ),
                        )

            ctx.emit(
                RunEventType.EVIDENCE_EXTRACTED,
                message="Evidence extraction completed",
                payload={
                    "round": state.get("round_index", 1),
                    "new_evidence": created,
                    "total_evidence": total_evidence,
                    "retrieval_hits": retrieval_hits,
                    "retrieval_degraded": retrieval_degraded,
                    "retrieval_mode": (
                        "hybrid" if retrieval_vector_used else "keyword"
                    ),
                    "semantic_matches": semantic_matches,
                },
                dedupe_key=(
                    f"evidence:extracted:round:{state.get('round_index', 1)}"
                ),
                stage=RunStatus.BUILDING_EVIDENCE,
            )
            limitations = list(state.get("limitations") or [])
            if total_evidence == 0:
                limitations = _with_limitations(
                    state,
                    _text(
                        brief,
                        "未能从现有来源中提取可追溯证据。",
                        "No traceable evidence could be extracted from the available sources.",
                    ),
                )
            elif retrieval_degraded:
                limitations = _with_limitations(
                    {"limitations": limitations},
                    _text(
                        brief,
                        "语义检索不可用，证据检索已降级为关键词检索。",
                        "Semantic retrieval was unavailable; evidence retrieval used keyword search.",
                    ),
                )
            return {
                "evidence_ids": sorted(evidence_ids),
                "limitations": limitations,
                "current_node": "extract_evidence",
            }

    return node


def analyze_evidence(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            brief = _brief(state)
            matrix = _refresh_evidence_matrix(
                ctx,
                _outline(state).sections,
                brief,
            )
            ctx.emit(
                RunEventType.PROGRESS,
                message="Evidence analysis completed",
                payload={
                    "evidence": matrix.total_evidence,
                    "sources": matrix.source_count,
                    "independent_sources": matrix.independent_source_count,
                    "coverage_ratio": matrix.coverage_ratio,
                    "target_ratio": matrix.target_ratio,
                    "contradictions": matrix.contradiction_count,
                    "duplicate_groups": len(matrix.duplicate_source_groups),
                    "under_target_sections": [
                        row.section_title
                        for row in matrix.rows
                        if row.target_evidence > 0 and row.evidence_ratio < 1.0
                    ],
                },
                dedupe_key=(
                    f"evidence:analyzed:round:{state.get('round_index', 1)}"
                ),
                stage=RunStatus.BUILDING_EVIDENCE,
            )
            return {
                "matrix": matrix.model_dump(mode="json"),
                "current_node": "analyze_evidence",
            }

    return node


def analyze_gaps(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            brief = _brief(state)
            matrix = _matrix(state)
            round_index = int(state.get("round_index", 1))
            outline = _outline(state)
            roles = {
                section.section_id: section.role
                for section in outline.sections
            }
            terms_by_section = {
                section.section_id: section.search_terms
                for section in outline.sections
            }
            gaps = _section_gaps(
                matrix,
                brief=brief,
                roles=roles,
                terms_by_section=terms_by_section,
                previous_gaps=[
                    ResearchGap.model_validate(value)
                    for value in (state.get("gaps") or [])
                ],
                round_index=round_index,
            )

            budget = ctx.run_budget()
            limitations = list(state.get("limitations") or [])
            budget_stopped = False
            if round_index >= brief.max_search_rounds:
                budget_stopped = True
                limitations = _with_limitations(
                    state,
                    _text(
                        brief,
                        f"已达到最大检索轮数（{brief.max_search_rounds}），停止补充检索。",
                        f"Reached the maximum search rounds ({brief.max_search_rounds}).",
                    ),
                )
            if len(state.get("sources") or []) >= brief.max_sources:
                budget_stopped = True
                limitations = _with_limitations(
                    {"limitations": limitations},
                    _text(
                        brief,
                        f"已达到最大来源数量（{brief.max_sources}），停止补充检索。",
                        f"Reached the maximum source count ({brief.max_sources}).",
                    ),
                )
            if budget["token_exhausted"]:
                budget_stopped = True
                limitations = _with_limitations(
                    {"limitations": limitations},
                    _text(
                        brief,
                        "已达到模型 token 预算，停止进一步研究。",
                        "The model token budget was exhausted; further research stopped.",
                    ),
                )
            if budget["cost_exhausted"]:
                budget_stopped = True
                limitations = _with_limitations(
                    {"limitations": limitations},
                    _text(
                        brief,
                        "已达到费用预算，停止进一步研究。",
                        "The cost budget was exhausted; further research stopped.",
                    ),
                )
            ctx.emit(
                RunEventType.PROGRESS,
                message="Research gaps analyzed",
                payload={
                    "gaps": len(gaps),
                    "budget_stopped": budget_stopped,
                    "round": round_index,
                },
                dedupe_key=f"gaps:analyzed:round:{round_index}",
                stage=RunStatus.BUILDING_EVIDENCE,
            )
            return {
                "gaps": [gap.model_dump(mode="json") for gap in gaps],
                "limitations": limitations,
                "budget_stopped": budget_stopped,
                "current_node": "analyze_gaps",
            }

    return node


def _section_gaps(
    matrix: EvidenceMatrix,
    *,
    brief: ResearchBrief,
    roles: dict[UUID, SectionRole],
    terms_by_section: dict[UUID, list[str]],
    previous_gaps: list[ResearchGap],
    round_index: int,
) -> list[ResearchGap]:
    """Turn the evidence matrix into the gaps the run should chase next.

    A covered section is not automatically done: it only means two supporting
    items from two independent sources exist. When the section is still below
    its evidence target the run may look for more material, but the gap closes
    as soon as a round fails to improve the ratio.
    """
    previous_ratios = {
        gap.section_id: gap.achieved_ratio
        for gap in previous_gaps
        if gap.section_id is not None and gap.target_evidence > 0
    }
    gaps: list[ResearchGap] = []
    for row in matrix.rows:
        if roles.get(row.section_id) is SectionRole.LIMITATIONS:
            continue
        severe = row.coverage_status is SectionCoverageStatus.INSUFFICIENT
        under_target = row.target_evidence > 0 and row.evidence_ratio < 1.0
        if row.coverage_status is SectionCoverageStatus.COVERED:
            if not under_target:
                continue
            previous_ratio = previous_ratios.get(row.section_id)
            if previous_ratio is not None and previous_ratio >= row.evidence_ratio:
                # The last round already chased this section and did not
                # improve it, so more searching would not help.
                continue
            gaps.append(
                ResearchGap(
                    section_id=row.section_id,
                    section_title=row.section_title,
                    reason=_text(
                        brief,
                        (
                            f"该章节已有可用证据，但只达到目标量的"
                            f"{round(row.evidence_ratio * 100)}%，"
                            f"仍可补充独立来源以支撑更细的判断。"
                        ),
                        (
                            "The section has usable evidence but only "
                            f"{round(row.evidence_ratio * 100)}% of its "
                            "target, so more independent sources would "
                            "support finer conclusions."
                        ),
                    ),
                    severity="low",
                    suggested_queries=_gap_queries(
                        brief,
                        row.section_title,
                        terms_by_section.get(row.section_id, []),
                    ),
                    target_evidence=row.target_evidence,
                    achieved_ratio=row.evidence_ratio,
                )
            )
            continue
        gaps.append(
            ResearchGap(
                section_id=row.section_id,
                section_title=row.section_title,
                reason=row.gap
                or _text(
                    brief,
                    (
                        "该章节缺少足够的支持证据。"
                        if severe
                        else "该章节只有部分证据，尚不足以形成稳定结论。"
                    ),
                    (
                        "The section lacks sufficient supporting evidence."
                        if severe
                        else "Only partial evidence is available for this section."
                    ),
                ),
                severity="high" if severe else "medium",
                suggested_queries=_gap_queries(
                    brief,
                    row.section_title,
                    terms_by_section.get(row.section_id, []),
                ),
            )
        )
    if (
        matrix.total_evidence > 0
        and matrix.contradiction_count == 0
        and round_index == 1
    ):
        target = next(
            (
                row
                for row in matrix.rows
                if row.coverage_status is SectionCoverageStatus.COVERED
                and roles.get(row.section_id) is not SectionRole.LIMITATIONS
            ),
            matrix.rows[0] if matrix.rows else None,
        )
        if target is not None:
            gaps.append(
                ResearchGap(
                    section_id=target.section_id,
                    section_title=target.section_title,
                    reason=_text(
                        brief,
                        "现有证据缺少明确的反方或批评性观点，需要定向补充。",
                        "The current evidence lacks explicit opposing or critical views.",
                    ),
                    severity="medium",
                    suggested_queries=_counter_queries(
                        brief,
                        target.section_title,
                        terms_by_section.get(target.section_id, []),
                    ),
                )
            )
    return gaps


def revise_search_plan(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            brief = _brief(state)
            round_index = int(state.get("round_index", 1)) + 1
            ctx.ensure_status(
                RunStatus.SEARCHING,
                event_type=RunEventType.PHASE_STARTED,
                message=f"Targeted search round {round_index} started",
                dedupe_key=f"phase:searching:started:{round_index}",
            )
            gaps = [
                ResearchGap.model_validate(value)
                for value in state.get("gaps", [])
            ]
            queries = build_gap_queries(
                gaps,
                round_index=round_index,
                web_providers=_provider_names(ctx.registry.web_search),
                academic_providers=_provider_names(ctx.registry.academic_search),
                limit=brief.max_queries_per_round,
                language=brief.language,
            )
            limitations = list(state.get("limitations") or [])
            budget_stopped = bool(state.get("budget_stopped"))
            if not queries:
                budget_stopped = True
                limitations = _with_limitations(
                    {"limitations": limitations},
                    _text(
                        brief,
                        "研究缺口未能生成补充检索，将继续基于现有证据写作。",
                        "No follow-up queries could be generated for the gaps.",
                    ),
                )
            ctx.emit(
                RunEventType.PROGRESS,
                message="Research plan revised around identified gaps",
                payload={
                    "round": round_index,
                    "queries": len(queries),
                    "gaps": len(gaps),
                },
                dedupe_key=f"search:revised:round:{round_index}",
                stage=RunStatus.SEARCHING,
            )
            return {
                "queries": [query.model_dump(mode="json") for query in queries],
                "round_index": round_index,
                "limitations": limitations,
                "budget_stopped": budget_stopped,
                "current_node": "revise_search_plan",
            }

    return node


def evidence_agent(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(_evidence_agent_node_timeout(ctx)):
            ctx.guard()
            brief = _brief(state)
            outline = _outline(state)
            gaps = [
                ResearchGap.model_validate(value)
                for value in state.get("gaps", [])
            ]
            previous_rounds = int(state.get("agent_rounds") or 0)
            round_index = int(state.get("round_index", 1)) + (
                1 if previous_rounds else 0
            )
            ctx.ensure_status(
                RunStatus.SEARCHING,
                event_type=RunEventType.PHASE_STARTED,
                message=f"Evidence agent round {round_index} started",
                dedupe_key=f"phase:evidence-agent:started:{round_index}",
            )
            ctx.emit(
                RunEventType.PHASE_STARTED,
                message="Evidence agent started",
                dedupe_key=f"evidence-agent:started:{round_index}",
                stage=RunStatus.SEARCHING,
            )
            llm = _llm_evidence_agent(ctx)
            if llm is None:
                return {"current_node": "evidence_agent"}

            toolbox = ResearchToolbox(
                ctx,
                brief,
                max_sources=ctx.settings.research_agent_max_sources,
                max_searches=ctx.settings.research_agent_max_searches,
                max_fetches=ctx.settings.research_agent_max_fetches,
                max_citation_calls=ctx.settings.research_agent_max_citation_calls,
                max_read_chars=ctx.settings.research_agent_read_chars,
                existing=_source_records(state),
                section_ids=[section.section_id for section in outline.sections],
                require_section=True,
                round_index=round_index,
            )
            tool_events = 0

            def record_tool(name: str, result: dict) -> None:
                nonlocal tool_events
                tool_events += 1
                results = result.get("results")
                query = result.get("query")
                message = f"Evidence agent used {name}"
                if name == "search_sources" and isinstance(query, str) and query:
                    message = f'Evidence agent searched "{query[:120]}"'
                elif name == "read_source":
                    message = "Evidence agent read a source"
                elif name == "fetch_source":
                    message = "Evidence agent fetched a page"
                elif name == "follow_citations":
                    message = "Evidence agent followed citations"
                ctx.emit(
                    RunEventType.PROGRESS,
                    message=message,
                    payload={
                        "tool": name,
                        "query": query,
                        "section_id": result.get("section_id"),
                        "direction": result.get("direction"),
                        "results": (
                            len(results) if isinstance(results, list) else None
                        ),
                        "source_id": (
                            result.get("source_id")
                            or result.get("from_source_id")
                        ),
                        "title": (
                            result.get("title") or result.get("from_title")
                        ),
                        "status": result.get("status"),
                        "error": (result.get("error") or "")[:300] or None,
                        "round": round_index,
                        "tool_calls": tool_events,
                    },
                    dedupe_key=f"evidence-agent:tool:{round_index}:{tool_events}",
                    stage=RunStatus.SEARCHING,
                )

            outcome = run_evidence_agent(
                brief,
                outline,
                gaps,
                llm,
                toolbox,
                round_index=round_index,
                max_rounds=ctx.settings.research_agent_max_rounds,
                max_tool_calls=ctx.settings.research_agent_max_tool_calls,
                max_tokens_floor=(
                    ctx.settings.research_evidence_agent_max_tokens_floor
                ),
                on_tool=record_tool,
            )
            if outcome.error:
                logger.warning(
                    "Evidence agent for run %s ended early: %s",
                    ctx.run_id,
                    outcome.error,
                )
            ctx.emit(
                RunEventType.PHASE_COMPLETED,
                message="Evidence agent completed",
                payload={
                    "round": round_index,
                    "completed": outcome.completed,
                    "sources": len(outcome.sources),
                    "searches": outcome.searches,
                    "fetches": outcome.fetches,
                    "citation_calls": outcome.citation_calls,
                    "tool_calls": outcome.tool_calls,
                    "llm_calls": outcome.llm_calls,
                    "section_notes": len(outcome.section_notes),
                    "error": outcome.error,
                },
                dedupe_key=f"phase:evidence-agent:completed:{round_index}",
                stage=RunStatus.SEARCHING,
            )
            return {
                "sources": [
                    record.model_dump(mode="json")
                    for record in outcome.sources
                ],
                "evidence_notes": list(outcome.section_notes),
                "agent_rounds": previous_rounds + 1,
                "round_index": round_index,
                "current_node": "evidence_agent",
            }

    return node


def attach_evidence(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            outline = _outline(state)
            matrix = _matrix(state)
            coverage = {
                row.section_id: row.coverage_status for row in matrix.rows
            }
            with ctx.database.session() as session:
                evidence = EvidenceRepository(session).list_for_run(
                    ctx.project_id,
                    ctx.run_id,
                )
            by_section: dict[UUID | None, list] = {}
            for item in evidence:
                by_section.setdefault(item.outline_section_id, []).append(item)

            sections: list[OutlineSection] = []
            coverage_updates: dict[UUID, SectionCoverageStatus] = {}
            for section in outline.sections:
                items = by_section.get(section.section_id, [])
                support = [
                    item
                    for item in items
                    if item.evidence_type is not EvidenceType.REFUTATION
                ]
                refute = [
                    item
                    for item in items
                    if item.evidence_type is EvidenceType.REFUTATION
                ]
                status = coverage.get(
                    section.section_id,
                    section.coverage_status,
                )
                sections.append(
                    section.model_copy(
                        update={
                            "key_claims": [
                                item.claim_summary[:300]
                                for item in support[:3]
                            ],
                            "evidence_ids": [item.id for item in items],
                            "support_evidence_ids": [
                                item.id for item in support
                            ],
                            "refute_evidence_ids": [item.id for item in refute],
                            "coverage_status": status,
                        }
                    )
                )
                coverage_updates[section.section_id] = status
            with ctx.database.session_scope() as session:
                repository = OutlineSectionRepository(session)
                for section_id, status in coverage_updates.items():
                    repository.update_coverage(
                        section_id,
                        status,
                        project_id=ctx.project_id,
                    )
            attached = Outline(
                version=outline.version,
                title=outline.title,
                sections=sections,
                summary=outline.summary,
            )
            ctx.emit(
                RunEventType.PROGRESS,
                message="Evidence attached to outline sections",
                payload={
                    "sections": len(sections),
                    "evidence": len(evidence),
                    "covered": sum(
                        1
                        for status in coverage_updates.values()
                        if status is SectionCoverageStatus.COVERED
                    ),
                },
                dedupe_key="outline:evidence-attached",
                stage=RunStatus.BUILDING_EVIDENCE,
            )
            return {
                "outline": attached.model_dump(mode="json"),
                "current_node": "attach_evidence",
            }

    return node


def await_outline_approval(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            brief = _brief(state)
            outline = _outline(state)
            if not ctx.settings.research_outline_approval_required:
                outline = _finalize_approved_outline(
                    ctx,
                    outline,
                    state.get("outline_id"),
                )
                ctx.ensure_status(
                    RunStatus.SEARCHING,
                    event_type=RunEventType.PHASE_STARTED,
                    message="Outline approved; evidence collection started",
                    dedupe_key=f"phase:searching:started:{outline.version}",
                )
                return {
                    "outline": outline.model_dump(mode="json"),
                    "feedback": {},
                    "current_node": "await_outline_approval",
                }

            approval_id = _request_approval(
                ctx,
                ApprovalKind.OUTLINE,
                outline.version,
                {
                    "outline_id": state.get("outline_id"),
                    "outline": outline.model_dump(mode="json"),
                },
                "Article outline ready for approval",
            )
            value = interrupt(
                {
                    "approval_id": str(approval_id),
                    "kind": ApprovalKind.OUTLINE.value,
                    "version": outline.version,
                }
            )
            decision = _approval_decision(value)
            if decision.decision is ApprovalStatus.REJECTED:
                ctx.ensure_status(
                    RunStatus.PLANNING,
                    event_type=RunEventType.PHASE_STARTED,
                    message="Outline revision requested by approver",
                    dedupe_key=f"outline:revision:{outline.version}",
                )
                return {
                    "feedback": {
                        "decision": "rejected",
                        "feedback": decision.feedback or "Revise the outline",
                        "version": outline.version,
                    },
                    "current_node": "await_outline_approval",
                }
            if decision.decision is ApprovalStatus.MODIFIED:
                if not decision.content:
                    raise PermanentRunError(
                        "Modified outline approval is missing updated outline content"
                    )
                content = decision.content
                if "sections" not in content and isinstance(
                    content.get("outline"), dict
                ):
                    content = content["outline"]
                try:
                    outline = Outline.model_validate(content)
                except Exception as exc:
                    raise PermanentRunError(
                        f"Modified outline content is invalid: {exc}"
                    ) from exc
            outline = _finalize_approved_outline(
                ctx,
                outline,
                state.get("outline_id"),
            )
            ctx.ensure_status(
                RunStatus.SEARCHING,
                event_type=RunEventType.PHASE_STARTED,
                message="Outline approved; evidence collection started",
                dedupe_key=f"phase:searching:started:{outline.version}",
            )
            return {
                "outline": outline.model_dump(mode="json"),
                "feedback": {},
                "current_node": "await_outline_approval",
            }

    return node


def write_sections(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(_writing_node_timeout(ctx, state)):
            ctx.guard()
            brief = _brief(state)
            ctx.ensure_status(
                RunStatus.WRITING,
                event_type=RunEventType.PHASE_STARTED,
                message="Writing report sections",
                dedupe_key="phase:writing:started",
            )
            outline = Outline.model_validate(state.get("outline") or {})
            outline_id = _outline_id(state)
            _approve_outline(ctx, outline_id)
            with ctx.database.session_scope() as session:
                evidence = EvidenceRepository(session).list_for_run(
                    ctx.project_id,
                    ctx.run_id,
                )
                sources = source_views(session, ctx.project_id)
            numbering = source_numbering(evidence)
            matrix = _matrix(state)
            gaps = [
                ResearchGap.model_validate(value)
                for value in state.get("gaps", [])
            ]
            limitations = list(state.get("limitations") or [])
            llm = _llm_writer(ctx)
            parser = next(iter(ctx.registry.parsers), None)
            search_session = (
                WriteSearchSession(
                    ctx,
                    brief,
                    parser=parser,
                    numbering=numbering,
                    sources=sources,
                    existing=_source_records(state),
                    evidence_count=len(evidence),
                )
                if llm is not None and _write_search_available(ctx)
                else None
            )
            max_search_requests = (
                ctx.settings.research_write_search_max_requests_per_section
                if search_session is not None
                else 0
            )
            new_evidence_ids: set[str] = set()
            prior_sections: list[dict] = []
            sections_state: list[dict] = []
            template_sections: list[str] = []
            total_sections = len(outline.sections)
            ordered_sections = _writing_order(outline.sections)
            disclosure_target = _disclosure_target(outline.sections)
            for section in ordered_sections:
                ctx.guard()
                supplement = None
                if search_session is not None:

                    def supplement(requests, current=section):
                        nonlocal matrix
                        outcome = search_session.supplement(current, requests)
                        if (
                            outcome.searches
                            or outcome.sources_added
                            or outcome.errors
                        ):
                            ctx.emit(
                                RunEventType.PROGRESS,
                                message="Supplemental sources retrieved while writing",
                                payload={
                                    "section_index": current.index,
                                    "queries": list(outcome.queries),
                                    "sources": outcome.sources_added,
                                    "evidence": outcome.evidence_added,
                                    "errors": list(outcome.errors[:3]),
                                },
                                dedupe_key=(
                                    f"section:{current.index}:write-search"
                                ),
                                stage=RunStatus.WRITING,
                            )
                        if outcome.evidence:
                            evidence.extend(outcome.evidence)
                            new_evidence_ids.update(
                                str(item.id) for item in outcome.evidence
                            )
                            matrix = _refresh_evidence_matrix(
                                ctx,
                                outline.sections,
                                brief,
                            )
                        return outcome

                section_llm = _progress_llm(
                    ctx,
                    llm,
                    stage=RunStatus.WRITING,
                    label=(
                        f"Writing section {section.index + 1}/{total_sections}"
                    ),
                    key_prefix=f"llm:section:{section.index}",
                    payload={
                        "node": "write_sections",
                        "section_index": section.index,
                        "title": section.title,
                    },
                )
                draft = compose_section(
                    section=section,
                    brief=brief,
                    matrix=matrix,
                    gaps=gaps,
                    limitations=limitations,
                    evidence=_section_evidence(section, evidence),
                    sources=sources,
                    numbering=numbering,
                    llm=section_llm,
                    supplement=supplement,
                    max_search_requests=max_search_requests,
                    prior_sections=prior_sections,
                    max_tokens=max(
                        ctx.settings.research_write_max_tokens_floor,
                        (section.target_words or 0)
                        * ctx.settings.research_write_tokens_per_word,
                    ),
                )
                content = draft.content
                if llm is not None and not draft.used_llm:
                    template_sections.append(section.title)
                if (
                    template_sections
                    and disclosure_target is not None
                    and section.section_id == disclosure_target.section_id
                ):
                    # The section is written last, so every template fallback
                    # is known and the article can name them.
                    content = (
                        f"{content.rstrip()}\n\n"
                        f"{_template_disclosure(template_sections, brief)}\n"
                    )
                prior_sections.append(
                    {
                        "index": section.index,
                        "title": section.title,
                        "summary": draft.summary
                        or summarize_section_content(content),
                    }
                )
                content_hash = text_sha256(content)
                with ctx.database.session_scope() as session:
                    repo = DraftSectionRepository(session)
                    latest = repo.get_latest(
                        ctx.project_id,
                        ctx.run_id,
                        section.index,
                    )
                    if (
                        latest is not None
                        and latest.content_hash == content_hash
                        and latest.title == section.title
                    ):
                        persisted = latest
                        created = False
                    else:
                        persisted = repo.create_version(
                            ctx.project_id,
                            ctx.run_id,
                            DraftSectionCreate(
                                outline_id=outline_id,
                                section_index=section.index,
                                title=section.title,
                                content_markdown=content,
                                citation_map=draft.citation_map,
                                evidence_ids=draft.evidence_ids,
                                generation_status=DraftSectionStatus.DRAFTED,
                            ),
                        )
                        created = True
                    draft_id = persisted.id
                    draft_version = persisted.version
                if created:
                    ctx.emit(
                        RunEventType.SECTION_GENERATED,
                        message="Report section generated",
                        payload={
                            "section_index": section.index,
                            "version": draft_version,
                            "citations": len(draft.citation_map),
                            "used_llm": draft.used_llm,
                        },
                        dedupe_key=(
                            f"section:{section.index}:generated:{draft_version}"
                        ),
                        stage=RunStatus.WRITING,
                    )
                sections_state.append(
                    {
                        "section_id": str(draft_id),
                        "index": section.index,
                        "title": section.title,
                        "version": draft_version,
                    }
                )
            if outline_id is not None:
                with ctx.database.session_scope() as session:
                    OutlineRepository(session).update_section_statuses(
                        outline_id,
                        {section.index: "drafted" for section in outline.sections},
                        project_id=ctx.project_id,
                    )
            outline_payload = outline.model_dump(mode="json")
            for item in outline_payload["sections"]:
                item["status"] = "drafted"
            result: ResearchState = {
                "sections": sorted(
                    sections_state, key=lambda item: item["index"]
                ),
                "outline": outline_payload,
                "current_node": "write_sections",
            }
            if new_evidence_ids:
                result["evidence_ids"] = sorted(
                    {
                        str(value)
                        for value in (state.get("evidence_ids") or [])
                    }
                    | new_evidence_ids
                )
                result["matrix"] = matrix.model_dump(mode="json")
            return result

    return node


def polish_article(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        if state.get("article_polished"):
            return {"current_node": "polish_article"}
        llm = _llm_polisher(ctx)
        if llm is None:
            return {"current_node": "polish_article"}
        with node_timeout(_polish_node_timeout(ctx)):
            ctx.guard()
            brief = _brief(state)
            with ctx.database.session_scope() as session:
                rows = DraftSectionRepository(session).list_latest_for_run(
                    ctx.project_id,
                    ctx.run_id,
                )
            drafts = [
                DraftArticleSection(
                    index=row.section_index,
                    title=row.title,
                    content=row.content_markdown,
                )
                for row in rows
            ]
            polish_llm = _progress_llm(
                ctx,
                llm,
                stage=RunStatus.WRITING,
                label="Polishing the article",
                key_prefix="llm:polish",
                payload={"node": "polish_article", "sections": len(drafts)},
            )
            changes, reason = polish_draft_sections(
                brief=brief,
                sections=drafts,
                llm=polish_llm,
                max_input_chars=ctx.settings.research_polish_max_input_chars,
                max_output_tokens=ctx.settings.research_polish_max_tokens_floor,
            )
            result: ResearchState = {
                "article_polished": True,
                "current_node": "polish_article",
            }
            applied: list = []
            if changes:
                by_index = {row.section_index: row for row in rows}
                with ctx.database.session_scope() as session:
                    repo = DraftSectionRepository(session)
                    for change in changes:
                        ctx.guard()
                        original = by_index[change.index]
                        if text_sha256(change.content) == original.content_hash:
                            continue
                        applied.append(
                            repo.create_version(
                                ctx.project_id,
                                ctx.run_id,
                                DraftSectionCreate(
                                    outline_id=original.outline_id,
                                    section_index=original.section_index,
                                    title=original.title,
                                    content_markdown=change.content,
                                    # The rewrite keeps the markers of its
                                    # section, so the stored mapping still
                                    # describes it.
                                    citation_map=dict(original.citation_map or {}),
                                    evidence_ids=list(original.evidence_ids or []),
                                    generation_status=DraftSectionStatus.DRAFTED,
                                ),
                            )
                        )
            for section in applied:
                ctx.emit(
                    RunEventType.SECTION_GENERATED,
                    message="Report section polished",
                    payload={
                        "section_index": section.section_index,
                        "version": section.version,
                        "citations": len(section.citation_map or {}),
                        "used_llm": True,
                        "polished": True,
                    },
                    dedupe_key=(
                        f"section:{section.section_index}:polished:{section.version}"
                    ),
                    stage=RunStatus.WRITING,
                )
            if applied:
                reason_kind = "applied"
                summary = (
                    f"Article rewritten for fluency in {len(applied)} sections"
                )
            elif changes:
                reason_kind = "unchanged"
                summary = "Article kept as written; the rewrite changed nothing"
            else:
                reason_kind = polish_reason_kind(reason)
                if reason_kind == "model_error":
                    summary = (
                        "Article kept as written; the polish model call failed "
                        f"({reason})"
                    )
                elif reason_kind == "rejected":
                    summary = (
                        "Article kept as written; polish rejected the rewrite "
                        f"({reason})"
                    )
                else:
                    summary = "Article kept as written; polish was skipped"
            ctx.emit(
                RunEventType.PROGRESS,
                message=summary,
                payload={
                    "sections": len(applied),
                    "reason": reason or None,
                    "reason_kind": reason_kind,
                },
                dedupe_key="article:polished",
                stage=RunStatus.WRITING,
            )
            if applied:
                entries = {
                    int(item["index"]): item
                    for item in (state.get("sections") or [])
                    if isinstance(item, dict) and "index" in item
                }
                for section in applied:
                    entries[section.section_index] = {
                        "section_id": str(section.id),
                        "index": section.section_index,
                        "title": section.title,
                        "version": section.version,
                    }
                result["sections"] = [entries[key] for key in sorted(entries)]
            return result

    return node


def verify_claims(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(_verification_node_timeout(ctx)):
            ctx.guard()
            ctx.ensure_status(
                RunStatus.VERIFYING,
                event_type=RunEventType.PHASE_STARTED,
                message="Verifying factual claims",
                dedupe_key="phase:verifying:claims",
            )
            ctx.guard()
            reviewer = _progress_llm(
                ctx,
                _claim_reviewer(ctx),
                stage=RunStatus.VERIFYING,
                label="Reviewing factual claims",
                key_prefix="llm:claim-review",
                payload={
                    "node": "verify_claims",
                    "limit": ctx.settings.research_claim_llm_review_limit,
                },
            )
            models, llm_reviewed = verify_run_claims(
                ctx.database,
                ctx.storage,
                ctx.settings,
                ctx.registry.parsers,
                ctx.project_id,
                ctx.run_id,
                llm=reviewer,
            )
            claims_state = [model.model_dump(mode="json") for model in models]
            summary = claim_status_counts(claims_state)
            ctx.emit(
                RunEventType.PROGRESS,
                message="Factual claims verified",
                payload={
                    **summary,
                    "llm_reviewed": llm_reviewed,
                },
                dedupe_key="claims:verified",
                stage=RunStatus.VERIFYING,
            )
            limitations = list(state.get("limitations") or [])
            unsupported = summary.get("unsupported", 0) + summary.get(
                "unverifiable",
                0,
            )
            if unsupported:
                limitations = _with_limitations(
                    {"limitations": limitations},
                    _text(
                        _brief(state),
                        f"有 {unsupported} 条事实主张缺少充分证据支持，已在核验记录中标出。",
                        f"{unsupported} claims lacked sufficient support and are flagged in the verification record.",
                    ),
                )
            return {
                "claims": claims_state,
                "limitations": limitations,
                "current_node": "verify_claims",
            }

    return node


def verify_citations(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            brief = _brief(state)
            ctx.ensure_status(
                RunStatus.VERIFYING,
                event_type=RunEventType.PHASE_STARTED,
                message="Verifying citations",
                dedupe_key="phase:verifying:started",
            )
            issues: list[CitationIssue] = []
            passed = warnings = failed = 0
            with ctx.database.session_scope() as session:
                sections = DraftSectionRepository(session).list_latest_for_run(
                    ctx.project_id,
                    ctx.run_id,
                )
                sources = source_views(session, ctx.project_id)
                roles_by_index = {
                    index: row.role
                    for index, row in enumerate(
                        OutlineSectionRepository(session).list_for_run(
                            ctx.project_id,
                            ctx.run_id,
                        )
                    )
                }
                for section in sections:
                    ctx.guard()
                    existing = CitationAuditRepository(session).list_for_section(
                        ctx.project_id,
                        section.id,
                    )
                    if existing:
                        for audit in existing:
                            if audit.status is CitationAuditStatus.PASSED:
                                passed += 1
                            elif audit.status is CitationAuditStatus.WARNING:
                                warnings += 1
                                issues.append(
                                    CitationIssue(
                                        section_index=section.section_index,
                                        status=audit.status,
                                        issue_type=audit.issue_type,
                                        details=audit.details,
                                    )
                                )
                            else:
                                failed += 1
                                issues.append(
                                    CitationIssue(
                                        section_index=section.section_index,
                                        status=audit.status,
                                        issue_type=audit.issue_type,
                                        details=audit.details,
                                    )
                                )
                        continue

                    audit_value = audit_section(
                        section,
                        sources,
                        role=roles_by_index.get(section.section_index),
                    )
                    CitationAuditRepository(session).create(
                        ctx.project_id,
                        ctx.run_id,
                        audit_value,
                    )
                    section.generation_status = (
                        DraftSectionStatus.FAILED
                        if audit_value.status is CitationAuditStatus.FAILED
                        else DraftSectionStatus.VERIFIED
                    )
                    session.flush()
                    if audit_value.status is CitationAuditStatus.PASSED:
                        passed += 1
                    elif audit_value.status is CitationAuditStatus.WARNING:
                        warnings += 1
                        issues.append(
                            CitationIssue(
                                section_index=section.section_index,
                                status=audit_value.status,
                                issue_type=audit_value.issue_type,
                                details=audit_value.details,
                            )
                        )
                    else:
                        failed += 1
                        issues.append(
                            CitationIssue(
                                section_index=section.section_index,
                                status=audit_value.status,
                                issue_type=audit_value.issue_type,
                                details=audit_value.details,
                            )
                        )
                        ctx.emit(
                            RunEventType.CITATION_AUDIT_FAILED,
                            message="Citation audit failed",
                            payload={
                                "section_index": section.section_index,
                                "issue_type": (
                                    audit_value.issue_type.value
                                    if audit_value.issue_type
                                    else None
                                ),
                            },
                            dedupe_key=f"citation:failed:{section.id}",
                            stage=RunStatus.VERIFYING,
                        )
            audit = CitationAudit(
                issues=issues,
                passed=passed,
                warnings=warnings,
                failed=failed,
            )
            with ctx.database.session_scope() as session:
                sections = DraftSectionRepository(session).list_latest_for_run(
                    ctx.project_id,
                    ctx.run_id,
                )
                sources = source_views(session, ctx.project_id)
                evidence_source_ids = {
                    item.source_id
                    for item in EvidenceRepository(session).list_for_run(
                        ctx.project_id,
                        ctx.run_id,
                    )
                }
            reference_audit = audit_references(
                sections,
                sources,
                evidence_source_ids,
            )
            limitations = list(state.get("limitations") or [])
            if failed:
                limitations = _with_limitations(
                    {"limitations": limitations},
                    _text(
                        brief,
                        f"有 {failed} 个章节未通过引用核验，报告保留核验记录。",
                        f"{failed} sections failed citation verification; audit records were preserved.",
                    ),
                )
            if reference_audit.dangling_markers:
                limitations = _with_limitations(
                    {"limitations": limitations},
                    _text(
                        brief,
                        "报告中存在无法映射到来源的引用标记："
                        + ", ".join(reference_audit.dangling_markers[:10]),
                        "The report contains citation markers without a source mapping: "
                        + ", ".join(reference_audit.dangling_markers[:10]),
                    ),
                )
            if reference_audit.invalid_metadata_source_ids:
                limitations = _with_limitations(
                    {"limitations": limitations},
                    _text(
                        brief,
                        f"有 {len(reference_audit.invalid_metadata_source_ids)} 条参考文献元数据不完整，"
                        "已保留警告而未补写信息。",
                        f"{len(reference_audit.invalid_metadata_source_ids)} references have "
                        "incomplete metadata; warnings were recorded instead of inventing fields.",
                    ),
                )
            if reference_audit.unused_source_ids:
                limitations = _with_limitations(
                    {"limitations": limitations},
                    _text(
                        brief,
                        f"有 {len(reference_audit.unused_source_ids)} 个证据来源未在正文中引用。",
                        f"{len(reference_audit.unused_source_ids)} evidence sources were not cited in the report.",
                    ),
                )
            outline_id = _outline_id(state)
            if outline_id is not None:
                statuses = {
                    section.section_index: (
                        "verified"
                        if section.generation_status is DraftSectionStatus.VERIFIED
                        else "drafted"
                    )
                    for section in sections
                }
                with ctx.database.session_scope() as session:
                    OutlineRepository(session).update_section_statuses(
                        outline_id,
                        statuses,
                        project_id=ctx.project_id,
                    )
                outline_payload = dict(state.get("outline") or {})
                outline_sections = [
                    dict(item) for item in outline_payload.get("sections", [])
                ]
                for item in outline_sections:
                    status = statuses.get(int(item.get("index", -1)))
                    if status is not None:
                        item["status"] = status
                outline_payload["sections"] = outline_sections
            else:
                outline_payload = dict(state.get("outline") or {})
            return {
                "citation_audit": audit.model_dump(mode="json"),
                "reference_audit": reference_audit.model_dump(mode="json"),
                "outline": outline_payload,
                "limitations": limitations,
                "current_node": "verify_citations",
            }

    return node


def finalize_report(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            brief = _brief(state)
            matrix = _matrix(state)
            audit = CitationAudit.model_validate(
                state.get("citation_audit") or {}
            )
            reference_audit = ReferenceAudit.model_validate(
                state.get("reference_audit") or {}
            )
            limitations = list(state.get("limitations") or [])
            claim_state = {
                str(item.get("claim_id")): item
                for item in state.get("claims") or []
            }
            with ctx.database.session_scope() as session:
                sections = DraftSectionRepository(session).list_latest_for_run(
                    ctx.project_id,
                    ctx.run_id,
                )
                citations = CitationAuditRepository(session).list_for_run(
                    ctx.project_id,
                    ctx.run_id,
                )
                claims = ClaimRepository(session).list_for_run(
                    ctx.project_id,
                    ctx.run_id,
                )
                links = ClaimEvidenceRepository(session)
                claim_records = [
                    {
                        "claim_id": str(claim.id),
                        "section_index": claim_state.get(str(claim.id), {}).get(
                            "section_index"
                        ),
                        "claim_text": claim.claim_text,
                        "claim_type": claim.claim_type.value,
                        "status": claim.verification_status.value,
                        "citation_markers": claim_state.get(str(claim.id), {}).get(
                            "citation_markers",
                            [],
                        ),
                        "reasons": claim_state.get(str(claim.id), {}).get(
                            "reasons",
                            [],
                        ),
                        "evidence": [
                            {
                                "evidence_id": str(link.evidence_id),
                                "relation": link.relation.value,
                                "rationale": link.rationale,
                            }
                            for link in links.list_for_claim(
                                ctx.project_id,
                                claim.id,
                            )
                        ],
                    }
                    for claim in claims
                ]
                sources = source_views(session, ctx.project_id)
                references = reference_lines(
                    brief,
                    sections,
                    sources,
                )
                report = report_markdown(
                    brief,
                    sections,
                    references,
                )
                reference_list = "\n".join(
                    [f"# {_text(brief, '参考文献', 'References')}", "", *references]
                )
                matrix_json = json.dumps(
                    {
                        "generated_at": datetime.now(tz=UTC).isoformat(),
                        "coverage_ratio": matrix.coverage_ratio,
                        **matrix.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                verification_json = json.dumps(
                    {
                        "generated_at": datetime.now(tz=UTC).isoformat(),
                        "citation_audit": audit.model_dump(mode="json"),
                        "reference_audit": reference_audit.model_dump(mode="json"),
                        "claims": claim_records,
                        "claim_summary": claim_status_counts(claim_records),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                artifacts = persist_report_artifacts(
                    storage=ctx.storage,
                    session=session,
                    project_id=ctx.project_id,
                    run_id=ctx.run_id,
                    report=report,
                    reference_list=reference_list,
                    matrix_json=matrix_json,
                    verification_json=verification_json,
                )
                artifact_payload = [
                    {
                        "artifact_id": str(item.id),
                        "type": item.artifact_type.value,
                        "storage_key": item.storage_key,
                        "version": item.version,
                    }
                    for item in artifacts
                ]

            with ctx.database.session_scope() as session:
                run = lock_run(session, ctx.run_id, ctx.project_id)
                if run is None:
                    raise RunFinished("Research run disappeared")
                if run.status is not RunStatus.COMPLETED:
                    transition_run(
                        session,
                        run,
                        RunStatus.COMPLETED,
                        event_type=RunEventType.PHASE_COMPLETED,
                        message="Research report completed",
                        payload={
                            "artifacts": artifact_payload,
                            "sections": len(sections),
                            "citations": len(citations),
                            "claims": len(claim_records),
                            "claim_summary": claim_status_counts(claim_records),
                            "coverage_ratio": matrix.coverage_ratio,
                        },
                        dedupe_key="run:completed",
                    )
            return {
                "artifacts": artifact_payload,
                "limitations": limitations,
                "current_node": "finalize_report",
            }

    return node


def route_after_outline_approval(ctx: ResearchContext):
    def route(state: ResearchState) -> str:
        feedback = state.get("feedback") or {}
        if feedback.get("decision") == "rejected":
            return "research_agent"
        if _llm_evidence_agent(ctx) is not None:
            return "evidence_agent"
        return "generate_queries"

    return route


def route_after_gaps(ctx: ResearchContext):
    def route(state: ResearchState) -> str:
        gaps = state.get("gaps") or []
        if not gaps or state.get("budget_stopped"):
            return "attach_evidence"
        if _llm_evidence_agent(ctx) is not None:
            return "evidence_agent"
        return "revise_search_plan"

    return route


def _brief(state: ResearchState) -> ResearchBrief:
    return ResearchBrief.model_validate(state["brief"])


def _outline_or_none(state: ResearchState) -> Outline | None:
    if not state.get("outline"):
        return None
    return Outline.model_validate(state["outline"])


def _outline(state: ResearchState) -> Outline:
    outline = _outline_or_none(state)
    if outline is None:
        raise PermanentRunError("Article outline is missing from workflow state")
    return outline


def _matrix(state: ResearchState) -> EvidenceMatrix:
    return EvidenceMatrix.model_validate(state.get("matrix") or {})


def _refresh_evidence_matrix(
    ctx: ResearchContext,
    sections: list[OutlineSection],
    brief: ResearchBrief,
) -> EvidenceMatrix:
    targets = {
        section.section_id: section_evidence_target(section, ctx.settings)
        for section in sections
    }
    with ctx.database.session_scope() as session:
        evidence = EvidenceRepository(session).list_for_run(
            ctx.project_id,
            ctx.run_id,
        )
        sources = source_views(session, ctx.project_id)
        matrix = build_evidence_matrix(
            sections,
            evidence,
            sources,
            language=brief.language,
            targets=targets,
        )
        repository = OutlineSectionRepository(session)
        for row in matrix.rows:
            if row.section_id is not None:
                repository.update_coverage(
                    row.section_id,
                    row.coverage_status,
                    project_id=ctx.project_id,
                )
    return matrix


def _searchable_sections(outline: Outline) -> list[OutlineSection]:
    return [
        section
        for section in outline.sections
        if section.role is not SectionRole.LIMITATIONS
    ]


def _section_queries(section: OutlineSection) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for value in [*section.search_terms, section.title]:
        text = " ".join(str(value or "").split())
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        queries.append(text)
    return queries


def _source_records(state: ResearchState) -> list[SourceRecord]:
    return [
        SourceRecord.model_validate(value)
        for value in state.get("sources", [])
    ]


def _normalize_outline(outline: Outline) -> Outline:
    sections: list[OutlineSection] = []
    seen_ids: set[UUID] = set()
    seen_intro = False
    seen_conclusion = False
    for index, section in enumerate(outline.sections):
        section_id = section.section_id
        if section_id in seen_ids:
            section_id = uuid4()
        seen_ids.add(section_id)
        role = section.role
        if role is SectionRole.INTRODUCTION:
            if seen_intro:
                role = SectionRole.BODY
            seen_intro = True
        elif role is SectionRole.CONCLUSION:
            if seen_conclusion:
                role = SectionRole.BODY
            seen_conclusion = True
        sections.append(
            section.model_copy(
                update={
                    "section_id": section_id,
                    "index": index,
                    "role": role,
                }
            )
        )
    return Outline(
        version=outline.version,
        title=outline.title,
        sections=sections,
        summary=outline.summary,
    )


def _outline_section_create(section: OutlineSection) -> OutlineSectionCreate:
    return OutlineSectionCreate(
        id=section.section_id,
        title=section.title,
        role=section.role,
        purpose=section.purpose,
        search_terms=list(section.search_terms),
        target_words=section.target_words,
        priority=max(0, 1000 - section.index),
    )


def _sync_outline_sections(
    ctx: ResearchContext,
    outline: Outline,
) -> Outline:
    with ctx.database.session_scope() as session:
        rows = OutlineSectionRepository(session).sync(
            ctx.project_id,
            ctx.run_id,
            [_outline_section_create(section) for section in outline.sections],
        )
        sections = [
            section.model_copy(update={"section_id": row.id})
            for section, row in zip(outline.sections, rows)
        ]
        return outline.model_copy(update={"sections": sections})


def _persist_outline(
    ctx: ResearchContext,
    outline: Outline,
    *,
    feedback: str | None,
) -> tuple[Outline, UUID]:
    outline = _normalize_outline(outline)
    outline = _sync_outline_sections(ctx, outline)
    with ctx.database.session_scope() as session:
        row = OutlineRepository(session).create_version(
            ctx.project_id,
            ctx.run_id,
            OutlineCreate(
                content=outline.model_dump(mode="json"),
                version_note=(
                    f"Regenerated from approval feedback: {feedback[:2000]}"
                    if feedback
                    else "Initial outline"
                ),
            ),
        )
        outline = outline.model_copy(update={"version": row.version})
        return outline, row.id


def _finalize_approved_outline(
    ctx: ResearchContext,
    outline: Outline,
    outline_id: str | None,
) -> Outline:
    outline = _normalize_outline(outline)
    outline = _sync_outline_sections(ctx, outline)
    with ctx.database.session_scope() as session:
        repository = OutlineRepository(session)
        if outline_id:
            row = repository.get(
                UUID(str(outline_id)),
                project_id=ctx.project_id,
            )
            outline = outline.model_copy(update={"version": row.version})
            row.content = outline.model_dump(mode="json")
            session.flush()
        else:
            row = repository.create_version(
                ctx.project_id,
                ctx.run_id,
                OutlineCreate(
                    content=outline.model_dump(mode="json"),
                    version_note="Approved outline",
                ),
            )
            outline = outline.model_copy(update={"version": row.version})
        repository.approve(row.id, project_id=ctx.project_id)
        return outline


def _request_approval(
    ctx: ResearchContext,
    kind: ApprovalKind,
    version: int,
    payload: dict,
    message: str,
) -> UUID:
    with ctx.database.session_scope() as session:
        run = lock_run(session, ctx.run_id, ctx.project_id)
        if run is None:
            raise RunFinished("Research run disappeared")
        if run.cancel_requested_at is not None:
            from dr_core.services.runs import finalize_cancelled_run

            finalize_cancelled_run(session, run)
            raise RunCancelled("Research run was cancelled")
        if run.status is not RunStatus.AWAITING_APPROVAL:
            transition_run(
                session,
                run,
                RunStatus.AWAITING_APPROVAL,
                event_type=RunEventType.AWAITING_APPROVAL,
                message=message,
                payload={"kind": kind.value, "version": version},
                dedupe_key=f"approval:{kind.value}:{version}:requested",
            )
        approval = RunApprovalRepository(session).request(
            ctx.project_id,
            ctx.run_id,
            kind=kind,
            version=version,
            payload=payload,
        )
        return approval.id


def _approval_decision(value) -> ApprovalDecision:
    if not isinstance(value, dict):
        raise PermanentRunError("Approval resume value is missing")
    return ApprovalDecision.model_validate(
        {
            "decision": value.get("decision"),
            "feedback": value.get("feedback"),
            "content": value.get("content"),
        }
    )


def _provider_names(providers) -> list:
    return [provider.name for provider in providers if provider.enabled]


def _provider_for_query(ctx: ResearchContext, query: SearchQueryModel):
    collection = (
        ctx.registry.web_search
        if query.kind == "web"
        else ctx.registry.academic_search
    )
    return next(
        (
            provider
            for provider in collection
            if provider.enabled and provider.name is query.provider
        ),
        None,
    )


def _prepare_query_row(ctx: ResearchContext, query: SearchQueryModel):
    with ctx.database.session_scope() as session:
        repo = SearchQueryRepository(session)
        row = repo.create(
            ctx.project_id,
            ctx.run_id,
            SearchQueryCreate(
                query_text=query.query_text,
                provider=query.provider,
                round_index=query.round_index,
                outline_section_id=query.outline_section_id,
            ),
        )
        if row.status is SearchQueryStatus.COMPLETED:
            return None
        if row.status is not SearchQueryStatus.RUNNING:
            repo.mark_running(row.id, project_id=ctx.project_id)
        session.flush()
        session.refresh(row)
        return row


def _mark_query_completed(ctx: ResearchContext, query_id: UUID, count: int) -> None:
    with ctx.database.session_scope() as session:
        SearchQueryRepository(session).mark_completed(
            query_id,
            project_id=ctx.project_id,
            result_count=count,
        )


def _mark_query_failed(ctx: ResearchContext, query_id: UUID, error: str) -> None:
    with ctx.database.session_scope() as session:
        SearchQueryRepository(session).mark_failed(
            query_id,
            project_id=ctx.project_id,
            error_summary=error[:4000],
        )


def _source_text(ctx: ResearchContext, record: SourceRecord, parser) -> str | None:
    with ctx.database.session() as session:
        version = SourceVersionRepository(session).get(
            record.source_version_id,
            project_id=ctx.project_id,
        )
        storage_key = version.storage_key
        mime_type = version.mime_type
        metadata = dict(version.version_metadata)
        parser_status = version.parser_status
    if storage_key and parser_status is not ParserStatus.FAILED:
        try:
            content = ctx.storage.read(storage_key)
        except Exception:
            return None
        if mime_type in {"text/markdown", "text/plain"}:
            try:
                return content.decode("utf-8-sig")[
                    : ctx.settings.parser_max_output_chars
                ]
            except UnicodeDecodeError:
                return None
        if parser is not None:
            try:
                parsed = parser.parse(
                    content,
                    mime_type=mime_type or "application/octet-stream",
                    filename=storage_key,
                )
                return parsed.text[: ctx.settings.parser_max_output_chars]
            except Exception:
                logger.warning(
                    "Unable to re-read parsed source %s",
                    record.source_version_id,
                )
    snippet = metadata.get("snippet")
    return str(snippet) if snippet else None


def _index_source(
    ctx: ResearchContext,
    record: SourceRecord,
    *,
    parser,
    parsed=None,
) -> IndexResult:
    try:
        return ensure_source_index(
            ctx,
            record.source_version_id,
            parser=parser,
            parsed=parsed,
            snippet=record.snippet,
        )
    except (RunCancelled, RunFinished, NodeTimeoutError):
        raise
    except Exception as exc:
        logger.warning(
            "Unable to index source %s: %s",
            record.source_version_id,
            exc,
        )
        return IndexResult(
            chunk_count=0,
            embedded=False,
            error=f"{type(exc).__name__}: indexing failed",
        )


def _approve_outline(ctx: ResearchContext, outline_id: UUID | str | None) -> None:
    if not outline_id:
        return
    value = outline_id if isinstance(outline_id, UUID) else UUID(str(outline_id))
    with ctx.database.session_scope() as session:
        repo = OutlineRepository(session)
        outline = repo.get(value, project_id=ctx.project_id)
        if outline.status is not OutlineStatus.APPROVED:
            repo.approve(outline.id, project_id=ctx.project_id)


def _outline_id(state: ResearchState) -> UUID | None:
    value = state.get("outline_id")
    return UUID(value) if value else None



def _fetch_priority(record: SourceRecord, sections: list[OutlineSection]) -> int:
    """Rank a source by how well it matches the outline's search terms.

    A source that matches the approved outline is worth its full-text fetch;
    an off-topic one is not, and the fetch budget is small.
    """
    tokens = tokenize(f"{record.title} {record.snippet}".casefold())
    if not tokens:
        return 0
    score = 0
    for section in sections:
        section_tokens: set[str] = set()
        for value in [*section.search_terms, section.title]:
            section_tokens |= tokenize(str(value or ""))
        if section_tokens:
            score += len(tokens & section_tokens)
    return score


def _semantic_matcher(ctx: ResearchContext) -> SemanticSentenceMatcher | None:
    """Build the cross-lingual fallback used when lexical matching finds nothing.

    Without an embedding provider the fallback stays off and evidence extraction
    keeps its previous behaviour.
    """
    if not ctx.settings.research_semantic_evidence_enabled:
        return None
    embedding = ctx.registry.embedding
    if embedding is None:
        return None
    return SemanticSentenceMatcher(
        embedding,
        min_similarity=ctx.settings.research_semantic_match_min_similarity,
    )


def _match_sentence(
    text: str,
    queries: list[str],
    matcher: SemanticSentenceMatcher | None,
) -> tuple[tuple[str, float, int, int] | None, str, float | None]:
    """Match a sentence lexically first, then by embedding similarity.

    A Chinese query and an English source sentence share no token, so the
    lexical pass returns nothing for a whole class of scholarly sources. The
    semantic pass only runs when the lexical one found nothing.
    """
    match = best_sentence_span_for_queries(text, queries)
    if match is not None:
        return match, "lexical", None
    if matcher is None:
        return None, "lexical", None
    semantic = matcher.match(text, queries)
    if semantic is None:
        return None, "lexical", None
    sentence, similarity, start, end = semantic
    return (sentence, similarity, start, end), "semantic", similarity


def _section_evidence(section, evidence: list) -> list:
    by_id = {item.id: item for item in evidence}
    selected = [
        by_id[evidence_id]
        for evidence_id in section.evidence_ids
        if evidence_id in by_id
    ]
    selected_ids = {item.id for item in selected}
    selected.extend(
        item
        for item in evidence
        if item.outline_section_id == section.section_id
        and item.id not in selected_ids
    )
    return selected


def _writing_order(sections: list[OutlineSection]) -> list[OutlineSection]:
    """Write the closing sections last.

    The limitations and conclusion close the article, and the closing section
    also carries the disclosure about sections that fell back to the
    deterministic template. Writing them last means that disclosure is
    complete before the section is persisted.
    """
    closing = [
        section
        for section in sections
        if section.role in (SectionRole.LIMITATIONS, SectionRole.CONCLUSION)
    ]
    body = [
        section
        for section in sections
        if section.role not in (SectionRole.LIMITATIONS, SectionRole.CONCLUSION)
    ]
    return [*body, *closing]


def _disclosure_target(
    sections: list[OutlineSection],
) -> OutlineSection | None:
    """Section that names the template-only sections."""
    closing = [
        section
        for section in sections
        if section.role in (SectionRole.LIMITATIONS, SectionRole.CONCLUSION)
    ]
    if closing:
        return closing[-1]
    return sections[-1] if sections else None


def _template_disclosure(template_sections: list[str], brief: ResearchBrief) -> str:
    """Tell the reader which sections carry no model-written prose.

    A section that fell back to the deterministic template is still part of
    the article, so the article has to say so instead of presenting the
    template as if it were written prose.
    """
    zh = brief.language is ProjectLanguage.ZH
    titles = "、".join(template_sections) if zh else ", ".join(template_sections)
    if zh:
        return (
            f"以下章节未能取得模型正文，仅以可核验的引文呈现：{titles}。"
            "这些章节的判断应结合原文审阅。"
        )
    return (
        "The following sections carry no model-written prose and present "
        f"verifiable quotations only: {titles}. Their claims should be read "
        "against the original sources."
    )


def _llm_provider(ctx: ResearchContext, enabled: bool):
    if not enabled:
        return None
    llm = ctx.registry.llm
    if llm is None or getattr(llm, "name", "") == "mock-llm":
        return None
    return _reasoning_budget(ctx, UsageRecordingLLM(llm, ctx.record_usage))


def _reasoning_budget(ctx: ResearchContext, llm):
    """Repeat a call whose token budget was spent on reasoning.

    The wrapper sits above the usage recorder, so the truncated attempt and
    the repeated one are both billed to the run.
    """
    retries = 0

    def notify(message: str, spent: int, larger: int) -> None:
        nonlocal retries
        retries += 1
        ctx.emit(
            RunEventType.PROGRESS,
            message=message,
            payload={
                "retry_reason": "reasoning_truncated",
                "max_tokens": spent,
                "retry_max_tokens": larger,
            },
            dedupe_key=f"llm:reasoning-retry:{retries}",
        )

    return ReasoningBudgetLLM(
        llm,
        factor=ctx.settings.llm_reasoning_retry_factor,
        ceiling=ctx.settings.llm_reasoning_retry_ceiling,
        notify=notify,
    )


def _progress_llm(
    ctx: ResearchContext,
    llm,
    *,
    stage: RunStatus,
    label: str,
    key_prefix: str,
    payload: dict | None = None,
):
    if llm is None:
        return None
    return ProgressReportingLLM(
        llm,
        ctx.emit,
        stage=stage,
        label=label,
        key_prefix=key_prefix,
        payload=payload,
    )


def _llm_outliner(ctx: ResearchContext):
    return _llm_provider(ctx, ctx.settings.research_llm_outline_enabled)


def _llm_writer(ctx: ResearchContext):
    return _llm_provider(ctx, ctx.settings.research_llm_writing_enabled)


def _llm_polisher(ctx: ResearchContext):
    return _llm_provider(ctx, ctx.settings.research_llm_polish_enabled)


def _claim_reviewer(ctx: ResearchContext):
    return _llm_provider(ctx, ctx.settings.research_claim_llm_review)


def _llm_node_timeout(ctx: ResearchContext, calls: int) -> int:
    """Node budget widened by the worst-case cost of `calls` model requests."""
    base = ctx.settings.research_node_timeout_seconds
    if calls <= 0:
        return base
    return base + calls * ctx.settings.llm_call_budget_seconds


def _llm_research_agent(ctx: ResearchContext):
    if not ctx.settings.research_agent_enabled:
        return None
    if not _write_search_available(ctx):
        return None
    llm = ctx.registry.llm
    if (
        llm is None
        or getattr(llm, "name", "") == "mock-llm"
        or not callable(getattr(llm, "chat", None))
    ):
        return None
    return _reasoning_budget(ctx, UsageRecordingLLM(llm, ctx.record_usage))


def _llm_evidence_agent(ctx: ResearchContext):
    if not ctx.settings.research_agent_enabled:
        return None
    if not _write_search_available(ctx):
        return None
    llm = ctx.registry.llm
    if (
        llm is None
        or getattr(llm, "name", "") == "mock-llm"
        or not callable(getattr(llm, "chat", None))
    ):
        return None
    return _reasoning_budget(ctx, UsageRecordingLLM(llm, ctx.record_usage))


def _evidence_agent_node_timeout(ctx: ResearchContext) -> int:
    if _llm_evidence_agent(ctx) is None:
        return ctx.settings.research_node_timeout_seconds
    return _llm_node_timeout(
        ctx,
        ctx.settings.research_agent_max_rounds + 1,
    )


def _research_agent_node_timeout(ctx: ResearchContext) -> int:
    if _llm_research_agent(ctx) is not None:
        return _llm_node_timeout(
            ctx,
            ctx.settings.research_agent_max_rounds + 1,
        )
    if _llm_outliner(ctx) is not None:
        return _llm_node_timeout(ctx, 1)
    return ctx.settings.research_node_timeout_seconds


def _writing_node_timeout(ctx: ResearchContext, state: ResearchState) -> int:
    if _llm_writer(ctx) is None:
        return ctx.settings.research_node_timeout_seconds
    outline = state.get("outline") or {}
    sections = outline.get("sections") if isinstance(outline, dict) else None
    count = (
        len(sections)
        if isinstance(sections, list) and sections
        else LLM_SECTION_CALL_BUDGET
    )
    if _write_search_available(ctx):
        count *= 2
    return _llm_node_timeout(ctx, count)


def _polish_node_timeout(ctx: ResearchContext) -> int:
    if _llm_polisher(ctx) is None:
        return ctx.settings.research_node_timeout_seconds
    return _llm_node_timeout(ctx, LLM_POLISH_CALL_BUDGET)


def _write_search_available(ctx: ResearchContext) -> bool:
    registry = getattr(ctx, "registry", None)
    providers = [
        *(getattr(registry, "web_search", None) or []),
        *(getattr(registry, "academic_search", None) or []),
    ]
    return any(getattr(provider, "enabled", False) for provider in providers)


def _verification_node_timeout(ctx: ResearchContext) -> int:
    if _claim_reviewer(ctx) is None:
        return ctx.settings.research_node_timeout_seconds
    return _llm_node_timeout(
        ctx,
        ctx.settings.research_claim_llm_review_limit,
    )












def _gap_queries(
    brief: ResearchBrief,
    section_title: str,
    search_terms: list[str] | None = None,
) -> list[str]:
    primary, latin = query_pair(section_title, search_terms or [])
    if brief.language is ProjectLanguage.ZH:
        if latin != primary:
            return [
                f"{primary} 证据 数据 案例",
                f"{latin} evidence data cases",
            ]
        return [
            f"{primary} 证据 数据 案例",
            f"{primary} 反方观点 局限 批评",
        ]
    return [
        f"{primary} evidence data cases",
        f"{primary} counterarguments limitations critique",
    ]


def _counter_queries(
    brief: ResearchBrief,
    section_title: str,
    search_terms: list[str] | None = None,
) -> list[str]:
    primary, latin = query_pair(section_title, search_terms or [])
    if brief.language is ProjectLanguage.ZH:
        if latin != primary:
            return [
                f"{primary} 反方证据 批评 争议",
                f"{latin} counter-evidence critique controversy",
            ]
        return [
            f"{primary} 反方证据 批评 争议",
            f"{primary} 风险 适用边界",
        ]
    return [
        f"{primary} counter-evidence critique controversy",
        f"{primary} risks boundary conditions",
    ]


def _outline_tokens(outline: Outline) -> set[str]:
    tokens: set[str] = set()
    for section in outline.sections:
        tokens.update(_tokens(section.title))
        for term in section.search_terms:
            tokens.update(_tokens(term))
    return tokens


def _tokens(text: str) -> set[str]:
    from dr_core.research.text import tokenize

    return tokenize(text)


def _with_limitations(state: ResearchState, *items: str) -> list[str]:
    current = list(state.get("limitations") or [])
    seen = set(current)
    for item in items:
        if item and item not in seen:
            current.append(item)
            seen.add(item)
    return current


def _no_provider_message(brief: ResearchBrief) -> str:
    return _text(
        brief,
        "未配置可用的搜索提供商，研究将仅基于已上传资料。",
        "No search provider is configured; research will rely on uploaded sources only.",
    )


def _text(brief: ResearchBrief, zh: str, en: str) -> str:
    return zh if brief.language is ProjectLanguage.ZH else en
