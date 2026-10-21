from __future__ import annotations

import contextvars
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from langgraph.types import interrupt
from sqlalchemy import func, select

from dr_core.config.settings import LLM_SECTION_CALL_BUDGET
from dr_core.db.enums import (
    ApprovalKind,
    ApprovalStatus,
    ArtifactType,
    CitationAuditStatus,
    CitationIssueType,
    DraftSectionStatus,
    EvidenceType,
    EvidenceVerificationStatus,
    OutlineStatus,
    ParserStatus,
    ProjectLanguage,
    QuestionCoverageStatus,
    RunEventType,
    RunStatus,
    SearchQueryStatus,
    SourceOrigin,
    SourceType,
    SourceVersionKind,
)
from dr_core.db.models import (
    ResearchQuestion,
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
    ResearchQuestionRepository,
    RunApprovalRepository,
    SearchQueryRepository,
    SourceRepository,
    SourceVersionRepository,
)
from dr_core.normalization import normalize_doi, normalize_url, text_sha256
from dr_core.providers.errors import ProviderError
from dr_core.providers.models import FetchStatus
from dr_core.providers.orchestration import fetch_web_content
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
from dr_core.research.generation import (
    build_gap_queries,
    build_outline,
    build_plan,
    build_queries,
    source_numbering,
)
from dr_core.research.indexing import IndexResult, ensure_source_index
from dr_core.research.models import (
    ApprovalDecision,
    CitationAudit,
    CitationIssue,
    EvidenceMatrix,
    Outline,
    ReferenceAudit,
    ResearchBrief,
    ResearchGap,
    ResearchPlan,
    ResearchQuestion as ResearchQuestionModel,
    SearchQuery as SearchQueryModel,
    SourceRecord,
)
from dr_core.research.planning import build_plan_with_llm
from dr_core.research.quality import build_evidence_matrix
from dr_core.research.reporting import (
    claim_status_counts,
    persist_report_artifacts,
    reference_lines,
    report_markdown,
    source_views,
)
from dr_core.research.retrieval import HybridRetriever, RetrievalFilters, RetrievalHit
from dr_core.research.state import ResearchState
from dr_core.research.text import (
    best_sentence_span,
    citation_markers,
    context_window,
    sentence_spans,
)
from dr_core.research.verification import audit_section, verify_run_claims
from dr_core.research.writing import compose_section
from dr_core.schemas import (
    DraftSectionCreate,
    ResearchQuestionCreate,
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


def plan_research(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(_planning_node_timeout(ctx)):
            ctx.guard()
            brief = _brief(state)
            feedback_state = dict(state.get("feedback") or {})
            feedback_text = feedback_state.get("feedback")
            current_plan = _plan_or_none(state)
            version = 1 if current_plan is None else current_plan.version + 1
            planner = _llm_planner(ctx)
            plan = (
                build_plan_with_llm(
                    brief,
                    planner,
                    version=version,
                    feedback=feedback_text,
                )
                if planner is not None
                else None
            )
            used_planner = plan is not None
            limitations = state.get("limitations") or []
            if plan is None:
                if planner is not None:
                    limitations = _with_limitations(
                        state,
                        _text(
                            brief,
                            "研究计划模型输出未通过校验，研究问题改用确定性模板生成。",
                            "The model plan failed validation, so research questions "
                            "came from the deterministic template.",
                        ),
                    )
                plan = build_plan(
                    brief,
                    version=version,
                    feedback=feedback_text,
                )
            plan = _sync_plan_questions(ctx, plan)
            ctx.emit(
                RunEventType.PHASE_COMPLETED,
                message="Research plan prepared",
                payload={
                    "plan_version": plan.version,
                    "planner": "llm" if used_planner else "template",
                    "question_ids": [
                        str(item.question_id) for item in plan.questions
                    ],
                },
                dedupe_key=f"phase:planning:completed:{plan.version}",
                stage=RunStatus.PLANNING,
            )
            return {
                "plan": plan.model_dump(mode="json"),
                "feedback": {},
                "limitations": limitations,
                "current_node": "plan_research",
            }

    return node


def await_plan_approval(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            if not ctx.settings.research_plan_approval_required:
                return {"feedback": {}, "current_node": "await_plan_approval"}
            plan = _plan(state)
            approval_id = _request_approval(
                ctx,
                ApprovalKind.PLAN,
                plan.version,
                plan.model_dump(mode="json"),
                "Research plan ready for approval",
            )
            value = interrupt(
                {
                    "approval_id": str(approval_id),
                    "kind": ApprovalKind.PLAN.value,
                    "version": plan.version,
                }
            )
            decision = _approval_decision(value)
            if decision.decision is ApprovalStatus.REJECTED:
                ctx.ensure_status(
                    RunStatus.PLANNING,
                    event_type=RunEventType.PHASE_STARTED,
                    message="Plan revision requested by approver",
                    dedupe_key=f"phase:planning:revised:{plan.version}",
                )
                return {
                    "feedback": {
                        "decision": "rejected",
                        "feedback": decision.feedback or "Revise the research plan",
                        "version": plan.version,
                    },
                    "current_node": "await_plan_approval",
                }

            if decision.decision is ApprovalStatus.MODIFIED:
                if not decision.content:
                    raise PermanentRunError(
                        "Modified plan approval is missing updated plan content"
                    )
                revised = ResearchPlan.model_validate(decision.content)
                revised = revised.model_copy(update={"version": plan.version + 1})
                revised = _sync_plan_questions(ctx, revised)
                plan = revised

            ctx.ensure_status(
                RunStatus.SEARCHING,
                event_type=RunEventType.PHASE_STARTED,
                message="Research plan approved; searching started",
                dedupe_key=f"phase:searching:started:{state.get('round_index', 1)}",
            )
            return {
                "plan": plan.model_dump(mode="json"),
                "feedback": {},
                "current_node": "await_plan_approval",
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
                _plan(state),
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
                                query.research_question_id,
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
                                query.research_question_id,
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
            merged: dict[str, SourceRecord] = {}
            for record in _source_records(state):
                for key in _source_keys(record):
                    existing = merged.get(key)
                    if existing is None:
                        merged[key] = record
                    else:
                        merged[key] = _merge_source_record(existing, record)
            records = list({item.source_id: item for item in merged.values()}.values())

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
            plan = _plan(state)
            query_tokens = _plan_tokens(plan)
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
            ][: brief.max_fetches_per_round]
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

            ctx.emit(
                RunEventType.PROGRESS,
                message="Full-text fetch completed",
                payload={
                    "attempted": len(eligible),
                    "fetched": len(fetch_results) - failed,
                    "failed": failed,
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


def extract_evidence(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            brief = _brief(state)
            plan = _plan(state)
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
            retrieved_questions: set[UUID | None] = set()
            with ctx.database.session() as session:
                existing = EvidenceRepository(session).list_for_run(
                    ctx.project_id,
                    ctx.run_id,
                )
                retrieved_questions.update(
                    item.research_question_id for item in existing
                )
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
                    for question in plan.questions:
                        if total_evidence >= brief.max_evidence:
                            break
                        result = retriever.search(
                            question.question,
                            filters=filters,
                            limit=min(ctx.settings.research_retrieval_limit, 8),
                        )
                        retrieval_degraded = (
                            retrieval_degraded or result.degraded_reason
                        )
                        retrieval_hits += len(result.hits)
                        used_sources: set[UUID] = set()
                        for hit in result.hits:
                            if total_evidence >= brief.max_evidence:
                                break
                            if hit.source_id in used_sources:
                                continue
                            matches = best_sentence_span(
                                hit.text,
                                question.question,
                                limit=1,
                            )
                            if not matches:
                                continue
                            sentence, _, start, end = matches[0]
                            with ctx.database.session_scope() as session:
                                item = EvidenceRepository(session).create(
                                    ctx.project_id,
                                    ctx.run_id,
                                    _evidence_from_hit(
                                        hit,
                                        question,
                                        sentence,
                                        start,
                                        end,
                                    ),
                                )
                                if str(item.id) in evidence_ids:
                                    continue
                                evidence_ids.add(str(item.id))
                            total_evidence += 1
                            created += 1
                            used_sources.add(hit.source_id)
                            retrieved_questions.add(question.question_id)

            uncovered = [
                question
                for question in plan.questions
                if question.question_id not in retrieved_questions
            ]
            if uncovered and total_evidence < brief.max_evidence:
                for record in records:
                    ctx.guard()
                    if total_evidence >= brief.max_evidence:
                        break
                    if not record.source_version_id:
                        continue
                    primary = _source_text(ctx, record, parser)
                    texts = [primary] if primary else []
                    if record.snippet and record.snippet not in texts:
                        texts.append(record.snippet)
                    if not texts:
                        skipped += 1
                        continue
                    source_questions = uncovered
                    if record.question_ids:
                        source_questions = [
                            question
                            for question in uncovered
                            if question.question_id in record.question_ids
                        ]
                    for question in source_questions:
                        if total_evidence >= brief.max_evidence:
                            break
                        match = None
                        matched_text = None
                        for text in texts:
                            matches = best_sentence_span(
                                text,
                                question.question,
                                limit=1,
                            )
                            if matches:
                                match = matches[0]
                                matched_text = text
                                break
                        if match is None or matched_text is None:
                            continue
                        sentence, _, start, end = match
                        with ctx.database.session_scope() as session:
                            item = EvidenceRepository(session).create(
                                ctx.project_id,
                                ctx.run_id,
                                _evidence_from_text(
                                    record,
                                    question,
                                    matched_text,
                                    sentence,
                                    start,
                                    end,
                                ),
                            )
                            if str(item.id) in evidence_ids:
                                continue
                            evidence_ids.add(str(item.id))
                        total_evidence += 1
                        created += 1
                        uncovered = [
                            candidate
                            for candidate in uncovered
                            if candidate.question_id != question.question_id
                        ]

            ctx.emit(
                RunEventType.EVIDENCE_EXTRACTED,
                message="Evidence extraction completed",
                payload={
                    "round": state.get("round_index", 1),
                    "new_evidence": created,
                    "total_evidence": total_evidence,
                    "retrieval_hits": retrieval_hits,
                    "retrieval_degraded": retrieval_degraded,
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
            plan = _plan(state)
            with ctx.database.session_scope() as session:
                evidence = EvidenceRepository(session).list_for_run(
                    ctx.project_id,
                    ctx.run_id,
                )
                sources = {
                    source.id: source
                    for source in SourceRepository(session).list_for_project(
                        ctx.project_id,
                        limit=500,
                    )
                }
                matrix = build_evidence_matrix(
                    plan.questions,
                    evidence,
                    sources,
                    language=brief.language,
                )
                question_repo = ResearchQuestionRepository(session)
                for row in matrix.rows:
                    if row.question_id is not None:
                        question_repo.update_coverage(
                            row.question_id,
                            row.coverage_status,
                            project_id=ctx.project_id,
                        )
            ctx.emit(
                RunEventType.PROGRESS,
                message="Evidence analysis completed",
                payload={
                    "evidence": matrix.total_evidence,
                    "sources": matrix.source_count,
                    "independent_sources": matrix.independent_source_count,
                    "coverage_ratio": matrix.coverage_ratio,
                    "contradictions": matrix.contradiction_count,
                    "duplicate_groups": len(matrix.duplicate_source_groups),
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
            gaps: list[ResearchGap] = []
            for row in matrix.rows:
                if row.coverage_status is QuestionCoverageStatus.COVERED:
                    continue
                severe = row.coverage_status is QuestionCoverageStatus.INSUFFICIENT
                gaps.append(
                    ResearchGap(
                        question_id=row.question_id,
                        question=row.question,
                        reason=row.gap
                        or _text(
                            brief,
                            (
                                "该问题缺少足够的支持证据。"
                                if severe
                                else "该问题只有部分证据，尚不足以形成稳定结论。"
                            ),
                            (
                                "The question lacks sufficient supporting evidence."
                                if severe
                                else "Only partial evidence is available for this question."
                            ),
                        ),
                        severity="high" if severe else "medium",
                        suggested_queries=_gap_queries(brief, row.question),
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
                        if row.coverage_status is QuestionCoverageStatus.COVERED
                    ),
                    matrix.rows[0] if matrix.rows else None,
                )
                if target is not None:
                    gaps.append(
                        ResearchGap(
                            question_id=target.question_id,
                            question=target.question,
                            reason=_text(
                                brief,
                                "现有证据缺少明确的反方或批评性观点，需要定向补充。",
                                "The current evidence lacks explicit opposing or critical views.",
                            ),
                            severity="medium",
                            suggested_queries=_counter_queries(brief, target.question),
                        )
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
            if gaps and budget_stopped:
                limitations = _with_limitations(
                    {"limitations": limitations},
                    _text(
                        brief,
                        "部分研究问题未完全覆盖，相关结论按现有证据强度表述。",
                        "Some research questions remain incomplete; conclusions reflect current evidence strength.",
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


def generate_outline(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            brief = _brief(state)
            plan = _plan(state)
            matrix = _matrix(state)
            gaps = [
                ResearchGap.model_validate(value)
                for value in state.get("gaps", [])
            ]
            feedback_state = dict(state.get("feedback") or {})
            feedback = (
                feedback_state.get("feedback")
                if feedback_state.get("decision") == "rejected"
                else None
            )
            with ctx.database.session_scope() as session:
                evidence = EvidenceRepository(session).list_for_run(
                    ctx.project_id,
                    ctx.run_id,
                )
                by_question: dict[UUID | None, list] = {}
                for item in evidence:
                    by_question.setdefault(
                        item.research_question_id,
                        [],
                    ).append(item)
                repo = OutlineRepository(session)
                latest = repo.latest(ctx.project_id, ctx.run_id)
                candidate = build_outline(
                    brief,
                    plan,
                    matrix,
                    gaps,
                    version=latest.version if latest else 1,
                    evidence_by_question=by_question,
                    feedback=feedback,
                )
                content = candidate.model_dump(mode="json")
                if latest is not None and latest.content == content:
                    outline = latest
                else:
                    next_version = (latest.version + 1) if latest else 1
                    candidate = build_outline(
                        brief,
                        plan,
                        matrix,
                        gaps,
                        version=next_version,
                        evidence_by_question=by_question,
                        feedback=feedback,
                    )
                    outline = repo.create_version(
                        ctx.project_id,
                        ctx.run_id,
                        _outline_create(candidate, feedback),
                    )
                outline_id = outline.id
                outline_payload = dict(outline.content)
                outline_payload["version"] = outline.version
            ctx.emit(
                RunEventType.PHASE_COMPLETED,
                message="Report outline generated",
                payload={
                    "outline_id": str(outline_id),
                    "version": outline_payload["version"],
                    "sections": len(outline_payload.get("sections", [])),
                },
                dedupe_key=f"outline:generated:{outline_payload['version']}",
                stage=RunStatus.BUILDING_EVIDENCE,
            )
            return {
                "outline": outline_payload,
                "outline_id": str(outline_id),
                "feedback": {},
                "current_node": "generate_outline",
            }

    return node


def await_outline_approval(ctx: ResearchContext):
    def node(state: ResearchState) -> ResearchState:
        with node_timeout(ctx.settings.research_node_timeout_seconds):
            ctx.guard()
            outline = Outline.model_validate(state.get("outline") or {})
            if not ctx.settings.research_outline_approval_required:
                _approve_outline(ctx, state.get("outline_id"))
                return {"feedback": {}, "current_node": "await_outline_approval"}

            approval_id = _request_approval(
                ctx,
                ApprovalKind.OUTLINE,
                outline.version,
                {
                    "outline_id": state.get("outline_id"),
                    "outline": outline.model_dump(mode="json"),
                },
                "Report outline ready for approval",
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
                    RunStatus.BUILDING_EVIDENCE,
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
                outline = Outline.model_validate(decision.content)
                _approve_outline(ctx, state.get("outline_id"))
            ctx.ensure_status(
                RunStatus.WRITING,
                event_type=RunEventType.PHASE_STARTED,
                message="Outline approved; writing started",
                dedupe_key=f"phase:writing:started:{outline.version}",
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
            sections_state: list[dict] = []
            for position, section in enumerate(outline.sections):
                ctx.guard()
                next_title = (
                    outline.sections[position + 1].title
                    if position + 1 < len(outline.sections)
                    else None
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
                    next_section_title=next_title,
                    llm=llm,
                )
                content = draft.content
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
            return {
                "sections": sections_state,
                "outline": outline_payload,
                "current_node": "write_sections",
            }

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
            models, llm_reviewed = verify_run_claims(
                ctx.database,
                ctx.storage,
                ctx.settings,
                ctx.registry.parsers,
                ctx.project_id,
                ctx.run_id,
                llm=_claim_reviewer(ctx),
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

                    audit_value = audit_section(section, sources)
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
                    matrix,
                    sections,
                    references,
                    citations,
                    limitations,
                    claims=claim_records,
                    reference_audit=reference_audit,
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


def route_after_plan_approval(state: ResearchState) -> str:
    feedback = state.get("feedback") or {}
    if feedback.get("decision") == "rejected":
        return "plan_research"
    return "generate_queries"


def route_after_gaps(state: ResearchState) -> str:
    gaps = state.get("gaps") or []
    if not gaps or state.get("budget_stopped"):
        return "generate_outline"
    return "revise_search_plan"


def route_after_outline_approval(state: ResearchState) -> str:
    feedback = state.get("feedback") or {}
    if feedback.get("decision") == "rejected":
        return "generate_outline"
    return "write_sections"


def _brief(state: ResearchState) -> ResearchBrief:
    return ResearchBrief.model_validate(state["brief"])


def _plan_or_none(state: ResearchState) -> ResearchPlan | None:
    if not state.get("plan"):
        return None
    return ResearchPlan.model_validate(state["plan"])


def _plan(state: ResearchState) -> ResearchPlan:
    plan = _plan_or_none(state)
    if plan is None:
        raise PermanentRunError("Research plan is missing from workflow state")
    return plan


def _matrix(state: ResearchState) -> EvidenceMatrix:
    return EvidenceMatrix.model_validate(state.get("matrix") or {})


def _source_records(state: ResearchState) -> list[SourceRecord]:
    return [
        SourceRecord.model_validate(value)
        for value in state.get("sources", [])
    ]


def _sync_plan_questions(
    ctx: ResearchContext,
    plan: ResearchPlan,
) -> ResearchPlan:
    updated = []
    with ctx.database.session_scope() as session:
        repo = ResearchQuestionRepository(session)
        for question in plan.questions:
            record = repo.create(
                ctx.project_id,
                ctx.run_id,
                ResearchQuestionCreate(
                    question=question.question,
                    priority=question.priority,
                ),
            )
            updated.append(
                question.model_copy(
                    update={
                        "question_id": record.id,
                        "coverage_status": record.coverage_status,
                    }
                )
            )
    return plan.model_copy(update={"questions": updated})


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
                research_question_id=query.research_question_id,
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


def _web_source_record(ingested, result, question_id) -> SourceRecord:
    return SourceRecord(
        source_id=ingested.source_id,
        source_version_id=ingested.version_id,
        title=result.title,
        url=result.url,
        fetch_url=result.url,
        authors=[],
        published_at=result.published_at,
        source_type=SourceType.WEB_PAGE,
        source_origin=SourceOrigin.WEB_SEARCH,
        provider=result.provider.value,
        score=result.score,
        snippet=result.snippet,
        has_full_text=False,
        parser_status=(
            ingested.parser_status.value if ingested.parser_status else None
        ),
        question_ids=[question_id] if question_id else [],
        rank=result.rank,
    )


def _academic_source_record(ingested, result, question_id) -> SourceRecord:
    return SourceRecord(
        source_id=ingested.source_id,
        source_version_id=ingested.version_id,
        title=result.title,
        url=result.url,
        fetch_url=result.open_access_url or result.url,
        doi=result.doi,
        authors=list(result.authors),
        published_at=result.published_at,
        source_type=_academic_source_type(result.work_type),
        source_origin=SourceOrigin.ACADEMIC_SEARCH,
        provider=result.provider.value,
        score=result.score,
        snippet=result.abstract,
        has_full_text=False,
        parser_status=(
            ingested.parser_status.value if ingested.parser_status else None
        ),
        question_ids=[question_id] if question_id else [],
        rank=result.rank,
    )


def _merge_source_record(
    existing: SourceRecord,
    candidate: SourceRecord,
) -> SourceRecord:
    updates = {}
    for field in ("doi", "url", "fetch_url", "authors", "published_at", "snippet"):
        if not getattr(existing, field) and getattr(candidate, field):
            updates[field] = getattr(candidate, field)
    if not existing.source_version_id and candidate.source_version_id:
        updates["source_version_id"] = candidate.source_version_id
    if existing.score is None or (
        candidate.score is not None and candidate.score > existing.score
    ):
        updates["score"] = candidate.score
    if existing.rank_score is None or (
        candidate.rank_score is not None
        and candidate.rank_score > (existing.rank_score or 0)
    ):
        updates["rank_score"] = candidate.rank_score
    if candidate.has_full_text and not existing.has_full_text:
        updates["has_full_text"] = True
        updates["source_version_id"] = candidate.source_version_id
        updates["parser_status"] = candidate.parser_status
    merged_questions = list(
        dict.fromkeys([*existing.question_ids, *candidate.question_ids])
    )
    if merged_questions != existing.question_ids:
        updates["question_ids"] = merged_questions
    return existing.model_copy(update=updates) if updates else existing


def _source_keys(record: SourceRecord) -> list[str]:
    keys = [f"id:{record.source_id}"]
    if record.doi:
        try:
            keys.append(f"doi:{normalize_doi(record.doi)}")
        except ValueError:
            pass
    if record.url:
        try:
            keys.append(f"url:{normalize_url(record.url)}")
        except ValueError:
            pass
    return keys


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


def _evidence_type(sentence: str) -> EvidenceType:
    lowered = sentence.casefold()
    refutation_terms = (
        "however",
        "but ",
        "contradict",
        "contrary",
        "refute",
        "相反",
        "然而",
        "但是",
        "反驳",
    )
    if any(term in lowered for term in refutation_terms):
        return EvidenceType.REFUTATION
    if any(character.isdigit() for character in sentence):
        return EvidenceType.STATISTIC
    return EvidenceType.DIRECT_SUPPORT


def _evidence_from_hit(
    hit: RetrievalHit,
    question,
    sentence: str,
    start: int,
    end: int,
):
    from dr_core.schemas import EvidenceCreate

    raw = hit.text[start:end]
    verified = " ".join(raw.split()) == sentence
    before, after = context_window(hit.text, start, end)
    locator = {
        **hit.locator,
        "url": hit.url,
        "source_title": hit.source_title,
        "character_start": start,
        "character_end": end,
        "sentence_index": _sentence_index(hit.text, start),
        "retrieval": {
            "score": hit.score,
            "keyword_score": hit.keyword_score,
            "vector_score": hit.vector_score,
            "keyword_rank": hit.keyword_rank,
            "vector_rank": hit.vector_rank,
        },
    }
    return EvidenceCreate(
        source_id=hit.source_id,
        source_version_id=hit.source_version_id,
        research_question_id=question.question_id,
        evidence_type=_evidence_type(sentence),
        claim_summary=sentence[:1000],
        quoted_text=sentence,
        context_before=before or None,
        context_after=after or None,
        locator=locator,
        verification_status=(
            EvidenceVerificationStatus.VERIFIED
            if verified
            else EvidenceVerificationStatus.UNVERIFIED
        ),
    )


def _evidence_from_text(
    record: SourceRecord,
    question,
    text: str,
    sentence: str,
    start: int,
    end: int,
):
    from dr_core.schemas import EvidenceCreate

    raw = text[start:end]
    verified = " ".join(raw.split()) == sentence
    before, after = context_window(text, start, end)
    locator = {
        "kind": "search_snippet" if not record.has_full_text else "source_text",
        "source_version_id": str(record.source_version_id),
        "url": record.fetch_url or record.url,
        "provider": record.provider,
        "source_title": record.title,
        "character_start": start,
        "character_end": end,
        "sentence_index": _sentence_index(text, start),
        "text_length": len(text),
    }
    return EvidenceCreate(
        source_id=record.source_id,
        source_version_id=record.source_version_id,
        research_question_id=question.question_id,
        evidence_type=_evidence_type(sentence),
        claim_summary=sentence[:1000],
        quoted_text=sentence,
        context_before=before or None,
        context_after=after or None,
        locator=locator,
        verification_status=(
            EvidenceVerificationStatus.VERIFIED
            if verified
            else EvidenceVerificationStatus.UNVERIFIED
        ),
    )


def _sentence_index(text: str, start: int) -> int | None:
    for index, (_, span_start, _) in enumerate(sentence_spans(text)):
        if span_start == start:
            return index
    return None


def _outline_create(outline: Outline, feedback: str | None) -> object:
    from dr_core.schemas import OutlineCreate

    return OutlineCreate(
        content=outline.model_dump(mode="json"),
        version_note=(
            f"Regenerated from approval feedback: {feedback[:2000]}"
            if feedback
            else "Initial outline"
        ),
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



def _section_evidence(section, evidence: list) -> list:
    by_id = {item.id: item for item in evidence}
    selected = [
        by_id[evidence_id]
        for evidence_id in section.evidence_ids
        if evidence_id in by_id
    ]
    if section.question_id is not None:
        selected_ids = {item.id for item in selected}
        selected.extend(
            item
            for item in evidence
            if item.research_question_id == section.question_id
            and item.id not in selected_ids
        )
    if selected:
        return selected
    if section.question_id is not None:
        matching = [
            item
            for item in evidence
            if item.research_question_id == section.question_id
        ]
        if matching:
            return matching
    return list(evidence)


def _llm_provider(ctx: ResearchContext, enabled: bool):
    if not enabled:
        return None
    llm = ctx.registry.llm
    if llm is None or getattr(llm, "name", "") == "mock-llm":
        return None
    return llm


def _llm_planner(ctx: ResearchContext):
    return _llm_provider(ctx, ctx.settings.research_llm_planning_enabled)


def _llm_writer(ctx: ResearchContext):
    return _llm_provider(ctx, ctx.settings.research_llm_writing_enabled)


def _claim_reviewer(ctx: ResearchContext):
    return _llm_provider(ctx, ctx.settings.research_claim_llm_review)


def _llm_node_timeout(ctx: ResearchContext, calls: int) -> int:
    """Node budget widened by the worst-case cost of `calls` model requests."""
    base = ctx.settings.research_node_timeout_seconds
    if calls <= 0:
        return base
    return base + calls * ctx.settings.llm_call_budget_seconds


def _planning_node_timeout(ctx: ResearchContext) -> int:
    if _llm_planner(ctx) is None:
        return ctx.settings.research_node_timeout_seconds
    return _llm_node_timeout(ctx, 1)


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
    return _llm_node_timeout(ctx, count)


def _verification_node_timeout(ctx: ResearchContext) -> int:
    if _claim_reviewer(ctx) is None:
        return ctx.settings.research_node_timeout_seconds
    return _llm_node_timeout(
        ctx,
        ctx.settings.research_claim_llm_review_limit,
    )












def _gap_queries(brief: ResearchBrief, question: str) -> list[str]:
    if brief.language is ProjectLanguage.ZH:
        return [
            f"{question} 证据 数据 案例",
            f"{question} 反方观点 局限 批评",
        ]
    return [
        f"{question} evidence data cases",
        f"{question} counterarguments limitations critique",
    ]


def _counter_queries(brief: ResearchBrief, question: str) -> list[str]:
    if brief.language is ProjectLanguage.ZH:
        return [
            f"{question} 反方证据 批评 争议",
            f"{question} 风险 适用边界",
        ]
    return [
        f"{question} counter-evidence critique controversy",
        f"{question} risks boundary conditions",
    ]


def _plan_tokens(plan: ResearchPlan) -> set[str]:
    tokens: set[str] = set()
    for question in plan.questions:
        tokens.update(_tokens(question.question))
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


def _academic_source_type(work_type: str | None) -> SourceType:
    normalized = (work_type or "").casefold()
    if "journal" in normalized:
        return SourceType.JOURNAL_ARTICLE
    if "proceedings" in normalized or "conference" in normalized:
        return SourceType.CONFERENCE_PAPER
    if "posted" in normalized or "preprint" in normalized:
        return SourceType.PREPRINT
    if "book" in normalized:
        return SourceType.BOOK
    if "report" in normalized:
        return SourceType.REPORT
    if "dataset" in normalized:
        return SourceType.DATASET
    return SourceType.WEB_PAGE
