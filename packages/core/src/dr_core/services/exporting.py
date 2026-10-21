from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from dr_core.db.enums import (
    CitationAuditStatus,
    DraftSectionStatus,
    RunEventType,
    RunStatus,
)
from dr_core.db.models import ResearchProject, ResearchRun
from dr_core.db.repositories import (
    CitationAuditRepository,
    ClaimEvidenceRepository,
    ClaimRepository,
    DraftSectionRepository,
    EvidenceRepository,
    ResearchQuestionRepository,
    RunEventRepository,
    SourceRepository,
)
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.providers.parsing import LocalDocumentParser
from dr_core.research.claims import audit_references
from dr_core.research.models import (
    CitationAudit,
    CitationIssue,
    ReferenceAudit,
    ResearchBrief,
    ResearchQuestion as ResearchQuestionModel,
)
from dr_core.research.quality import build_evidence_matrix
from dr_core.research.reporting import (
    claim_status_counts,
    persist_report_artifacts,
    reference_lines,
    report_markdown,
    source_views,
)
from dr_core.research.verification import audit_section, verify_run_claims
from dr_core.schemas import RunEventCreate
from dr_core.services.errors import PermanentRunError


@dataclass(slots=True)
class ExportResult:
    artifacts: list
    claims: list[dict]
    citation_audit: CitationAudit
    reference_audit: ReferenceAudit
    section_count: int


def export_run(
    database,
    storage,
    settings,
    project_id: UUID,
    run_id: UUID,
) -> ExportResult:
    with database.session_scope() as session:
        run = session.get(ResearchRun, run_id)
        project = session.get(ResearchProject, project_id)
        if run is None or project is None or run.project_id != project_id:
            raise EntityNotFound("Research run not found")
        if project.deleted_at is not None:
            raise EntityNotFound("Research run not found")
        if run.status not in {RunStatus.COMPLETED, RunStatus.FAILED}:
            raise PermanentRunError(
                "Report export is only available for completed or failed runs"
            )
        sections = DraftSectionRepository(session).list_latest_for_run(
            project_id,
            run_id,
        )
        if not sections:
            raise PermanentRunError("The run has no draft sections to export")
        questions = ResearchQuestionRepository(session).list_for_run(
            project_id,
            run_id,
        )
        evidence = EvidenceRepository(session).list_for_run(project_id, run_id)
        source_records = SourceRepository(session).list_for_project(
            project_id,
            limit=500,
        )
        sources = source_views(session, project_id)

    brief = ResearchBrief(
        project_id=project.id,
        run_id=run.id,
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
    )
    matrix = build_evidence_matrix(
        [
            ResearchQuestionModel(
                question_id=item.id,
                question=item.question,
                priority=item.priority,
                parent_id=item.parent_id,
                coverage_status=item.coverage_status,
            )
            for item in questions
        ],
        evidence,
        {source.id: source for source in source_records},
        language=project.language,
    )

    audited_section_ids: set[UUID] = set()
    with database.session_scope() as session:
        audit_repo = CitationAuditRepository(session)
        sections_repo = DraftSectionRepository(session)
        for section in sections:
            existing = audit_repo.list_for_section(project_id, section.id)
            if not existing:
                value = audit_section(section, sources)
                audit_repo.create(project_id, run_id, value)
            audited_section_ids.add(section.id)
            latest = audit_repo.list_for_section(project_id, section.id)
            section_status = (
                DraftSectionStatus.FAILED
                if any(
                    audit.status is CitationAuditStatus.FAILED
                    for audit in latest
                )
                else DraftSectionStatus.VERIFIED
            )
            sections_repo.update_status(
                section.id,
                section_status,
                project_id=project_id,
            )

    claim_models, _ = verify_run_claims(
        database,
        storage,
        settings,
        [
            LocalDocumentParser(
                max_input_bytes=settings.parser_max_input_bytes,
                max_output_chars=settings.parser_max_output_chars,
                max_pdf_pages=settings.parser_max_pdf_pages,
            )
        ],
        project_id,
        run_id,
    )
    claim_state = {
        str(model.claim_id): model.model_dump(mode="json")
        for model in claim_models
        if model.claim_id is not None
    }

    with database.session_scope() as session:
        all_audits = CitationAuditRepository(session).list_for_run(
            project_id,
            run_id,
        )
        audits = [
            audit
            for audit in all_audits
            if audit.draft_section_id in audited_section_ids
        ]
        section_index = {section.id: section.section_index for section in sections}
        issues: list[CitationIssue] = []
        passed = warnings = failed = 0
        for audit in audits:
            if audit.status is CitationAuditStatus.PASSED:
                passed += 1
            elif audit.status is CitationAuditStatus.WARNING:
                warnings += 1
                issues.append(
                    CitationIssue(
                        section_index=section_index[audit.draft_section_id],
                        status=audit.status,
                        issue_type=audit.issue_type,
                        details=audit.details,
                    )
                )
            else:
                failed += 1
                issues.append(
                    CitationIssue(
                        section_index=section_index[audit.draft_section_id],
                        status=audit.status,
                        issue_type=audit.issue_type,
                        details=audit.details,
                    )
                )
        citation_audit = CitationAudit(
            issues=issues,
            passed=passed,
            warnings=warnings,
            failed=failed,
        )
        claims = ClaimRepository(session).list_for_run(project_id, run_id)
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
                "reasons": claim_state.get(str(claim.id), {}).get("reasons", []),
                "evidence": [
                    {
                        "evidence_id": str(link.evidence_id),
                        "relation": link.relation.value,
                        "rationale": link.rationale,
                    }
                    for link in links.list_for_claim(project_id, claim.id)
                ],
            }
            for claim in claims
            if str(claim.id) in claim_state
        ]
        evidence_source_ids = {item.source_id for item in evidence}
        reference_audit = audit_references(
            sections,
            sources,
            evidence_source_ids,
        )
        limitations = [
            issue
            for issue in reference_audit.issues
            if "not normalized" in issue or "incomplete" in issue
        ]
        references = reference_lines(brief, sections, sources)
        report = report_markdown(
            brief,
            matrix,
            sections,
            references,
            audits,
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
                "citation_audit": citation_audit.model_dump(mode="json"),
                "reference_audit": reference_audit.model_dump(mode="json"),
                "claims": claim_records,
                "claim_summary": claim_status_counts(claim_records),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        artifacts = persist_report_artifacts(
            storage=storage,
            session=session,
            project_id=project_id,
            run_id=run_id,
            report=report,
            reference_list=reference_list,
            matrix_json=matrix_json,
            verification_json=verification_json,
            force_new_version=True,
        )
        RunEventRepository(session).append(
            project_id,
            run_id,
            RunEventCreate(
                event_type=RunEventType.OTHER,
                stage=run.status,
                message="Report export regenerated",
                payload={
                    "artifacts": [
                        {
                            "id": str(item.id),
                            "type": item.artifact_type.value,
                            "version": item.version,
                        }
                        for item in artifacts
                    ],
                    "claims": len(claim_records),
                },
            ),
        )
    return ExportResult(
        artifacts=artifacts,
        claims=claim_records,
        citation_audit=citation_audit,
        reference_audit=reference_audit,
        section_count=len(sections),
    )


def _text(brief: ResearchBrief, zh: str, en: str) -> str:
    return zh if brief.language.value == "zh" else en
