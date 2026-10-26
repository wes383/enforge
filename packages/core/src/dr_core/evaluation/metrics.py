from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dr_core.db.enums import (
    ArtifactType,
    ClaimVerificationStatus,
    EvidenceType,
    RunEventType,
    SectionCoverageStatus,
    SearchQueryStatus,
    SourceVersionKind,
)
from dr_core.db.models import (
    Artifact,
    CitationAudit,
    Claim,
    ClaimEvidence,
    EvidenceItem,
    OutlineSectionRecord,
    ResearchRun,
    RunEvent,
    SearchQuery,
    Source,
    SourceVersion,
)
from dr_core.storage.base import FileStorage


@dataclass(frozen=True, slots=True)
class CaseMetrics:
    status: str
    citation_validity_rate: float | None
    citation_support_accuracy: float | None
    section_coverage_rate: float | None
    partial_coverage_rate: float | None
    unsupported_claim_ratio: float | None
    metadata_accuracy_rate: float | None
    # Coverage answers "could this section be written at all"; the target ratio
    # answers "did it reach the evidence it was budgeted for". It is taken from
    # the run's own evidence analysis rather than recomputed here.
    evidence_target_ratio: float | None
    full_text_evidence_ratio: float | None
    snippet_only_cited_ratio: float | None
    citation_span_traceability: float | None
    source_count: int
    academic_source_count: int
    evidence_count: int
    refute_evidence_count: int
    snippet_evidence_count: int
    full_text_evidence_count: int
    claim_count: int
    cited_claim_count: int
    failed_query_count: int
    cost_usd: float
    duration_seconds: float | None
    report_mentions_budget: bool


def collect_case_metrics(
    session: Session,
    storage: FileStorage,
    run: ResearchRun,
) -> CaseMetrics:
    audits = list(
        session.scalars(
            select(CitationAudit).where(CitationAudit.run_id == run.id)
        )
    )
    claims = list(
        session.scalars(select(Claim).where(Claim.run_id == run.id))
    )
    claim_links: dict[UUID, list[UUID]] = {}
    if claims:
        for claim_id, evidence_id in session.execute(
            select(ClaimEvidence.claim_id, ClaimEvidence.evidence_id).where(
                ClaimEvidence.claim_id.in_([claim.id for claim in claims])
            )
        ).all():
            claim_links.setdefault(claim_id, []).append(evidence_id)
    citations = set(claim_links)
    sources = list(
        session.scalars(
            select(Source).where(Source.project_id == run.project_id)
        )
    )
    sections = list(
        session.scalars(
            select(OutlineSectionRecord).where(OutlineSectionRecord.run_id == run.id)
        )
    )
    evidence = list(
        session.scalars(
            select(EvidenceItem).where(EvidenceItem.run_id == run.id)
        )
    )
    versions = {
        version.id: version
        for version in session.scalars(
            select(SourceVersion).where(
                SourceVersion.id.in_(
                    [item.source_version_id for item in evidence]
                )
            )
        )
    }
    source_ids = {item.source_id for item in evidence}
    versions_by_source: dict[UUID, list[SourceVersion]] = defaultdict(list)
    if source_ids:
        for version in session.scalars(
            select(SourceVersion)
            .where(SourceVersion.source_id.in_(source_ids))
            .order_by(SourceVersion.created_at)
        ):
            versions_by_source[version.source_id].append(version)
    failed_queries = sum(
        1
        for query in session.scalars(
            select(SearchQuery).where(SearchQuery.run_id == run.id)
        )
        if query.status is SearchQueryStatus.FAILED
    )

    passed_audits = sum(
        1 for audit in audits if audit.status.value == "passed"
    )
    cited_claims = [claim for claim in claims if claim.id in citations]
    supported_cited = sum(
        1
        for claim in cited_claims
        if claim.verification_status is ClaimVerificationStatus.SUPPORTED
    )
    unsupported = sum(
        1
        for claim in claims
        if claim.verification_status is not ClaimVerificationStatus.SUPPORTED
    )
    bibliographic_sources = [
        source
        for source in sources
        if source.doi is not None or source.source_type.value != "web_page"
    ]
    complete_sources = sum(
        1 for source in bibliographic_sources if _metadata_complete(source)
    )
    snippet_evidence = sum(
        1
        for item in evidence
        if _version_kind(versions.get(item.source_version_id))
        is SourceVersionKind.SEARCH_SNIPPET
    )
    full_text_evidence = sum(
        1
        for item in evidence
        if _version_kind(versions.get(item.source_version_id))
        is SourceVersionKind.FULL_TEXT
    )
    report_text = _latest_report_text(session, storage, run)
    evidence_by_id = {item.id: item for item in evidence}
    quoted_by_source = _QuotedVersions(storage, versions_by_source)
    snippet_only_cited = 0
    traceable_cited = 0
    for evidence_ids in claim_links.values():
        linked = [
            evidence_by_id[evidence_id]
            for evidence_id in evidence_ids
            if evidence_id in evidence_by_id
        ]
        if not linked:
            continue
        if all(
            _version_kind(versions.get(item.source_version_id))
            is SourceVersionKind.SEARCH_SNIPPET
            for item in linked
        ):
            snippet_only_cited += 1
        if any(
            quoted_by_source.contains(item.source_id, item.quoted_text)
            for item in linked
        ):
            traceable_cited += 1
    duration = None
    if run.started_at is not None and run.finished_at is not None:
        duration = round(
            (run.finished_at - run.started_at).total_seconds(),
            3,
        )
    return CaseMetrics(
        status=run.status.value,
        citation_validity_rate=_ratio(passed_audits, len(audits)),
        citation_support_accuracy=_ratio(supported_cited, len(cited_claims)),
        section_coverage_rate=_ratio(
            sum(
                1
                for section in sections
                if section.coverage_status is SectionCoverageStatus.COVERED
            ),
            len(sections),
        ),
        partial_coverage_rate=_ratio(
            sum(
                1
                for section in sections
                if section.coverage_status is SectionCoverageStatus.PARTIAL
            ),
            len(sections),
        ),
        unsupported_claim_ratio=_ratio(unsupported, len(claims)),
        metadata_accuracy_rate=_ratio(
            complete_sources,
            len(bibliographic_sources),
        ),
        evidence_target_ratio=_evidence_target_ratio(session, run.id),
        full_text_evidence_ratio=_ratio(full_text_evidence, len(evidence)),
        snippet_only_cited_ratio=_ratio(snippet_only_cited, len(cited_claims)),
        citation_span_traceability=_ratio(traceable_cited, len(cited_claims)),
        source_count=len(sources),
        academic_source_count=sum(
            1 for source in sources if source.source_type.value != "web_page"
        ),
        evidence_count=len(evidence),
        refute_evidence_count=sum(
            1
            for item in evidence
            if item.evidence_type is EvidenceType.REFUTATION
        ),
        snippet_evidence_count=snippet_evidence,
        full_text_evidence_count=full_text_evidence,
        claim_count=len(claims),
        cited_claim_count=len(cited_claims),
        failed_query_count=failed_queries,
        cost_usd=float(run.cost_usd or 0),
        duration_seconds=duration,
        report_mentions_budget="budget" in report_text.casefold(),
    )


def aggregate_metrics(metrics: Iterable[CaseMetrics]) -> dict[str, Any]:
    values = list(metrics)
    if not values:
        return {}
    return {
        "cases": len(values),
        "task_success_rate": _ratio(
            sum(1 for item in values if item.status == "completed"),
            len(values),
        ),
        "citation_validity_rate": _mean(
            item.citation_validity_rate for item in values
        ),
        "citation_support_accuracy": _mean(
            item.citation_support_accuracy for item in values
        ),
        "section_coverage_rate": _mean(
            item.section_coverage_rate for item in values
        ),
        "unsupported_claim_ratio": _mean(
            item.unsupported_claim_ratio for item in values
        ),
        "metadata_accuracy_rate": _mean(
            item.metadata_accuracy_rate for item in values
        ),
        "evidence_target_ratio": _mean(
            item.evidence_target_ratio for item in values
        ),
        "full_text_evidence_ratio": _mean(
            item.full_text_evidence_ratio for item in values
        ),
        "snippet_only_cited_ratio": _mean(
            item.snippet_only_cited_ratio for item in values
        ),
        "citation_span_traceability": _mean(
            item.citation_span_traceability for item in values
        ),
        "average_cost_usd": round(
            sum(item.cost_usd for item in values) / len(values),
            6,
        ),
        "average_duration_seconds": _mean(
            item.duration_seconds for item in values
        ),
        "total_evidence": sum(item.evidence_count for item in values),
        "total_claims": sum(item.claim_count for item in values),
        "total_refute_evidence": sum(item.refute_evidence_count for item in values),
    }


def human_review_sample(
    session: Session,
    run_id: UUID,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Claim, ClaimEvidence)
        .join(ClaimEvidence, ClaimEvidence.claim_id == Claim.id)
        .where(Claim.run_id == run_id)
        .order_by(Claim.created_at, Claim.id)
    ).all()
    sample: list[dict[str, Any]] = []
    seen: set[UUID] = set()
    for claim, link in rows:
        if claim.id in seen:
            continue
        seen.add(claim.id)
        sample.append(
            {
                "claim_id": str(claim.id),
                "claim_text": claim.claim_text,
                "model_status": claim.verification_status.value,
                "evidence_id": str(link.evidence_id),
                "relation": link.relation.value,
                "reviewer_label": None,
                "reviewer_notes": None,
            }
        )
        if len(sample) >= limit:
            break
    return sample


def compare_human_review(
    sample: list[dict[str, Any]],
    reviewed: list[dict[str, Any]],
) -> dict[str, Any]:
    labels = {
        str(item.get("claim_id")): str(item.get("reviewer_label") or "").upper()
        for item in reviewed
        if item.get("reviewer_label")
    }
    comparable = [
        item for item in sample if item["claim_id"] in labels
    ]
    agreements = sum(
        1
        for item in comparable
        if labels[item["claim_id"]] == item["model_status"].upper()
    )
    return {
        "sampled_claims": len(sample),
        "reviewed_claims": len(comparable),
        "agreement_rate": _ratio(agreements, len(comparable)),
        "agreements": agreements,
    }


def case_metrics_dict(metrics: CaseMetrics) -> dict[str, Any]:
    return asdict(metrics)


def _version_kind(version: SourceVersion | None) -> SourceVersionKind | None:
    return version.content_kind if version is not None else None


class _QuotedVersions:
    """Answers whether a quotation really occurs in the source it cites.

    A source can hold several versions: the search snippet that was indexed
    first and the full text fetched later. Evidence points at one version,
    but the quotation is traceable as soon as any version of the same source
    contains it, so the lookup is per source rather than per version. The
    text is read once per version because a source usually supports several
    quotations.
    """

    def __init__(
        self,
        storage: FileStorage,
        versions_by_source: dict[UUID, list[SourceVersion]],
    ) -> None:
        self._storage = storage
        self._versions = versions_by_source
        self._cache: dict[UUID, str] = {}

    def contains(self, source_id: UUID, quote: str) -> bool:
        needle = " ".join(quote.split())
        if not needle:
            return False
        return any(
            needle in self._text(version)
            for version in self._versions.get(source_id, ())
        )

    def _text(self, version: SourceVersion) -> str:
        cached = self._cache.get(version.id)
        if cached is not None:
            return cached
        text = ""
        if version.storage_key:
            try:
                text = self._storage.read(version.storage_key).decode(
                    "utf-8",
                    "replace",
                )
            except Exception:
                text = ""
        if not text:
            text = str((version.version_metadata or {}).get("snippet") or "")
        normalized = " ".join(text.split())
        self._cache[version.id] = normalized
        return normalized


def _evidence_target_ratio(session: Session, run_id: UUID) -> float | None:
    """The run's own last evidence-analysis target ratio."""
    event = session.scalar(
        select(RunEvent)
        .where(
            RunEvent.run_id == run_id,
            RunEvent.event_type == RunEventType.PROGRESS,
            RunEvent.dedupe_key.like("evidence:analyzed:round:%"),
        )
        .order_by(RunEvent.created_at.desc())
        .limit(1)
    )
    if event is None:
        return None
    try:
        return round(float((event.payload or {}).get("target_ratio")), 4)
    except (TypeError, ValueError):
        return None


def _metadata_complete(source: Source) -> bool:
    return bool(
        source.title.strip()
        and source.authors
        and source.published_at is not None
        and (source.venue or source.publisher)
        and (source.doi or source.canonical_url)
    )


def _latest_report_text(
    session: Session,
    storage: FileStorage,
    run: ResearchRun,
) -> str:
    artifact = session.scalar(
        select(Artifact)
        .where(
            Artifact.run_id == run.id,
            Artifact.artifact_type == ArtifactType.RESEARCH_REPORT,
        )
        .order_by(Artifact.version.desc())
        .limit(1)
    )
    if artifact is None:
        return ""
    try:
        return storage.read(artifact.storage_key).decode("utf-8")
    except Exception:
        return ""


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _mean(values) -> float | None:
    numbers = [value for value in values if value is not None]
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 4)
