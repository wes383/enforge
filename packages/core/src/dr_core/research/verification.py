from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from dr_core.db.enums import (
    CitationAuditStatus,
    CitationIssueType,
    ParserStatus,
)
from dr_core.db.models import DocumentChunk, SourceVersion
from dr_core.db.repositories import (
    ClaimEvidenceRepository,
    ClaimRepository,
    DraftSectionRepository,
    EvidenceRepository,
)
from dr_core.normalization import claim_fingerprint
from dr_core.providers.base import LLMProvider
from dr_core.research.claims import (
    ClaimAssessment,
    extract_claims,
    merged_status,
    review_claim_with_llm,
    verify_claim,
)
from dr_core.research.models import ClaimVerification
from dr_core.research.reporting import source_views
from dr_core.research.text import citation_markers
from dr_core.schemas import (
    CitationAuditCreate,
    ClaimCreate,
    ClaimEvidenceCreate,
)

logger = logging.getLogger(__name__)


def audit_section(section, sources: dict[UUID, Any]) -> CitationAuditCreate:
    markers = citation_markers(section.content_markdown)
    citation_map = dict(section.citation_map or {})
    exempt = any(
        word in section.title.casefold()
        for word in (
            "局限",
            "矛盾",
            "limitation",
            "contradiction",
            "approval feedback",
            "审批反馈",
        )
    )
    if not markers and not exempt:
        return CitationAuditCreate(
            draft_section_id=section.id,
            status=CitationAuditStatus.WARNING,
            issue_type=CitationIssueType.MISSING_CITATION,
            details="Section contains no citation markers or verified source references.",
        )

    dangling = [
        f"S{marker}" for marker in markers if f"S{marker}" not in citation_map
    ]
    if dangling:
        return CitationAuditCreate(
            draft_section_id=section.id,
            citation_marker=dangling[0],
            status=CitationAuditStatus.FAILED,
            issue_type=CitationIssueType.DANGLING_REFERENCE,
            details=f"Citation markers without source mapping: {', '.join(dangling)}",
        )

    for marker, source_id in citation_map.items():
        try:
            value = UUID(str(source_id))
        except ValueError:
            value = None
        if value is None or value not in sources:
            return CitationAuditCreate(
                draft_section_id=section.id,
                citation_marker=marker,
                status=CitationAuditStatus.FAILED,
                issue_type=CitationIssueType.METADATA_MISMATCH,
                details=f"Citation source {source_id} is not available in this project.",
            )
    return CitationAuditCreate(
        draft_section_id=section.id,
        status=CitationAuditStatus.PASSED,
        details="All citation markers resolve to stored project sources.",
    )


def verify_run_claims(
    database,
    storage,
    settings,
    parsers,
    project_id: UUID,
    run_id: UUID,
    *,
    llm: LLMProvider | None = None,
) -> tuple[list[ClaimVerification], int]:
    with database.session() as session:
        sections = DraftSectionRepository(session).list_latest_for_run(
            project_id,
            run_id,
        )
        evidence = EvidenceRepository(session).list_for_run(project_id, run_id)
        sources = source_views(session, project_id)
    evidence_by_id = {item.id: item for item in evidence}
    evidence_by_source: dict[UUID, list] = {}
    for item in evidence:
        evidence_by_source.setdefault(item.source_id, []).append(item)
    traceable = build_traceability_checker(
        database,
        storage,
        settings,
        parsers,
        project_id,
    )
    models: list[ClaimVerification] = []
    llm_reviewed = 0
    for section in sections:
        for candidate in extract_claims(section):
            assessment = verify_claim(
                candidate,
                section=section,
                sources_by_id=sources,
                evidence_by_id=evidence_by_id,
                evidence_by_source=evidence_by_source,
                traceable=traceable,
            )
            if llm is not None:
                cited = [
                    evidence_by_id[evidence_id]
                    for evidence_id in assessment.evidence_ids
                    if evidence_id in evidence_by_id
                ]
                before = assessment.status
                assessment = review_claim_with_llm(llm, assessment, cited)
                if assessment.status is not before:
                    llm_reviewed += 1
            models.append(
                persist_claim(
                    database,
                    project_id,
                    run_id,
                    section,
                    assessment,
                )
            )
    return models, llm_reviewed


def persist_claim(
    database,
    project_id: UUID,
    run_id: UUID,
    section,
    assessment: ClaimAssessment,
) -> ClaimVerification:
    candidate = assessment.candidate
    rationale = "; ".join(assessment.reasons)[:2000] or None
    with database.session_scope() as session:
        repo = ClaimRepository(session)
        existing = repo.find_by_fingerprint(
            run_id,
            claim_fingerprint(candidate.text),
        )
        status = merged_status(
            existing.verification_status if existing is not None else None,
            assessment.status,
        )
        claim = repo.create(
            project_id,
            run_id,
            ClaimCreate(
                claim_text=candidate.text,
                claim_type=candidate.claim_type,
                verification_status=status,
            ),
        )
        if claim.verification_status is not status:
            claim = repo.update_verification(
                claim.id,
                status,
                project_id=project_id,
            )
        links = ClaimEvidenceRepository(session)
        for evidence_id, relation in assessment.relations.items():
            links.link(
                project_id,
                claim.id,
                ClaimEvidenceCreate(
                    evidence_id=evidence_id,
                    relation=relation,
                    rationale=rationale,
                ),
            )
        claim_id = claim.id
    return ClaimVerification(
        claim_id=claim_id,
        section_id=section.id,
        section_index=section.section_index,
        claim_text=candidate.text,
        claim_type=candidate.claim_type,
        status=status,
        citation_markers=list(candidate.markers),
        evidence_ids=assessment.evidence_ids,
        reasons=assessment.reasons,
    )


def build_traceability_checker(
    database,
    storage,
    settings,
    parsers,
    project_id: UUID,
):
    text_cache: dict[UUID, str | None] = {}
    chunk_cache: dict[UUID, str | None] = {}
    parser = parsers[0] if parsers else None

    def normalized(value: str | None) -> str | None:
        if not value:
            return None
        return " ".join(value.split())

    def checker(item) -> bool:
        quote = normalized(item.quoted_text)
        if not quote:
            return False
        locator = dict(item.locator or {})
        chunk_value = locator.get("chunk_id")
        if chunk_value:
            try:
                chunk_id = UUID(str(chunk_value))
            except ValueError:
                chunk_id = None
            if chunk_id is not None:
                if chunk_id not in chunk_cache:
                    with database.session() as session:
                        chunk = session.get(DocumentChunk, chunk_id)
                        chunk_cache[chunk_id] = (
                            chunk.text
                            if chunk is not None and chunk.project_id == project_id
                            else None
                        )
                chunk_text = normalized(chunk_cache[chunk_id])
                if chunk_text is not None:
                    if quote in chunk_text:
                        return True
                    chunk_cache[chunk_id] = chunk_text
        version_id = item.source_version_id
        if version_id not in text_cache:
            text_cache[version_id] = read_version_text(
                database,
                storage,
                settings,
                parser,
                project_id,
                version_id,
            )
        version_text = text_cache[version_id]
        return bool(version_text and quote in version_text)

    return checker


def read_version_text(
    database,
    storage,
    settings,
    parser,
    project_id: UUID,
    version_id: UUID,
) -> str | None:
    with database.session() as session:
        version = session.get(SourceVersion, version_id)
        if version is None or version.project_id != project_id:
            return None
        storage_key = version.storage_key
        mime_type = version.mime_type
        metadata = dict(version.version_metadata)
        parser_status = version.parser_status
    if storage_key and parser_status is not ParserStatus.FAILED:
        try:
            content = storage.read(storage_key)
        except Exception:
            content = None
        if content is not None:
            if mime_type in {"text/markdown", "text/plain"}:
                try:
                    return " ".join(
                        content.decode("utf-8-sig")[
                            : settings.parser_max_output_chars
                        ].split()
                    )
                except UnicodeDecodeError:
                    pass
            elif parser is not None:
                try:
                    parsed = parser.parse(
                        content,
                        mime_type=mime_type or "application/octet-stream",
                        filename=storage_key,
                    )
                    return " ".join(
                        parsed.text[: settings.parser_max_output_chars].split()
                    )
                except Exception:
                    logger.warning(
                        "Unable to re-read source version %s for claim tracing",
                        version_id,
                    )
    snippet = metadata.get("snippet")
    return " ".join(str(snippet).split()) if snippet else None
