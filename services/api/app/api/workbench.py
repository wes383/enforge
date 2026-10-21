from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.schemas import (
    ApprovalResponse,
    ArtifactListResponse,
    ArtifactResponse,
    ClaimListResponse,
    ClaimResponse,
    EvidenceListResponse,
    EvidenceResponse,
    EventResponse,
    ExportResponse,
    OutlineWorkspaceResponse,
    ProjectResponse,
    QuestionListResponse,
    ResearchQuestionResponse,
    RunListResponse,
    RunOverviewResponse,
    RunResponse,
    SectionListResponse,
    SectionResponse,
    SectionUpdateRequest,
    SourceListResponse,
    SourceResponse,
    SourceVersionResponse,
)
from app.dependencies import SessionDep
from app.services.uploads import content_disposition
from dr_core.config import get_settings
from dr_core.db import get_database
from dr_core.db.enums import (
    ApprovalStatus,
    ArtifactType,
    DraftSectionStatus,
    RunStatus,
)
from dr_core.db.models import ResearchProject, ResearchRun, Source
from dr_core.db.repositories import (
    ArtifactRepository,
    CitationAuditRepository,
    ClaimEvidenceRepository,
    ClaimRepository,
    DraftSectionRepository,
    EvidenceRepository,
    OutlineRepository,
    ProjectRepository,
    ResearchQuestionRepository,
    RunApprovalRepository,
    RunEventRepository,
    RunRepository,
    SearchQueryRepository,
    SourceRepository,
    SourceVersionRepository,
)
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.research.text import citation_markers
from dr_core.schemas import DraftSectionCreate
from dr_core.services.errors import PermanentRunError
from dr_core.services.exporting import export_run
from dr_core.storage import StorageError, get_file_storage

router = APIRouter(tags=["workbench"])

_TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED}


def _run_and_project(
    session,
    run_id: UUID,
) -> tuple[ResearchRun, ResearchProject]:
    run = RunRepository(session).get(run_id)
    project = ProjectRepository(session).get(run.project_id)
    return run, project


def _run_sources(session, run: ResearchRun, evidence) -> list[Source]:
    sources = SourceRepository(session).list_for_project(
        run.project_id,
        limit=500,
    )
    evidence_source_ids = {item.source_id for item in evidence}
    if run.started_at is None:
        return sources
    return [
        source
        for source in sources
        if source.id in evidence_source_ids or source.created_at >= run.started_at
    ]


def _version_response(version) -> SourceVersionResponse:
    return SourceVersionResponse(
        id=version.id,
        version_number=version.version_number,
        content_kind=version.content_kind.value,
        mime_type=version.mime_type,
        size_bytes=version.size_bytes,
        parser_status=version.parser_status.value,
        parser_version=version.parser_version,
        parse_error=version.parse_error,
        fetched_url=version.fetched_url,
        fetched_at=version.fetched_at,
        has_stored_file=bool(version.storage_key),
        metadata=dict(version.version_metadata),
    )


def _artifact_response(artifact) -> ArtifactResponse:
    return ArtifactResponse(
        id=artifact.id,
        artifact_type=artifact.artifact_type.value,
        title=artifact.title,
        version=artifact.version,
        mime_type=artifact.mime_type,
        size_bytes=artifact.size_bytes,
        content_hash=artifact.content_hash,
        status=artifact.status.value,
        metadata=dict(artifact.artifact_metadata),
        created_at=artifact.created_at,
    )


def _latest_artifact(session, run: ResearchRun, artifact_type: ArtifactType):
    artifacts = [
        item
        for item in ArtifactRepository(session).list_for_run(
            run.project_id,
            run.id,
        )
        if item.artifact_type is artifact_type
    ]
    if not artifacts:
        return None
    return max(artifacts, key=lambda item: item.version)


def _read_verification(session, run: ResearchRun) -> dict[str, Any]:
    artifact = _latest_artifact(session, run, ArtifactType.CITATION_AUDIT)
    if artifact is None:
        return {"available": False}
    try:
        payload = json.loads(
            get_file_storage().read(artifact.storage_key).decode("utf-8")
        )
    except (StorageError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {"available": False}
    if not isinstance(payload, dict):
        return {"available": False}
    return {
        "available": True,
        "artifact": _artifact_response(artifact).model_dump(mode="json"),
        **payload,
    }


@router.get(
    "/projects/{project_id}/runs",
    response_model=RunListResponse,
)
def list_project_runs(
    project_id: UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunListResponse:
    ProjectRepository(session).get(project_id)
    repository = RunRepository(session)
    return RunListResponse(
        items=[
            RunResponse.model_validate(item)
            for item in repository.list_for_project(
                project_id,
                limit=limit,
                offset=offset,
            )
        ],
        total=repository.count_for_project(project_id),
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}/overview", response_model=RunOverviewResponse)
def get_run_overview(run_id: UUID, session: SessionDep) -> RunOverviewResponse:
    run, project = _run_and_project(session, run_id)
    questions = ResearchQuestionRepository(session).list_for_run(
        run.project_id,
        run.id,
    )
    evidence = EvidenceRepository(session).list_for_run(run.project_id, run.id)
    sources = _run_sources(session, run, evidence)
    versions = SourceVersionRepository(session).list_for_project(
        run.project_id,
        source_ids=[source.id for source in sources],
    )
    versions_by_source: dict[UUID, list] = {}
    for version in versions:
        versions_by_source.setdefault(version.source_id, []).append(version)
    source_status = {
        "total": len(sources),
        "full_text": 0,
        "snippet_only": 0,
        "parsed": 0,
        "parse_failed": 0,
        "pending_parse": 0,
    }
    for source in sources:
        source_versions = versions_by_source.get(source.id, [])
        if any(
            item.content_kind.value in {"full_text", "uploaded_document"}
            for item in source_versions
        ):
            source_status["full_text"] += 1
        elif source_versions:
            source_status["snippet_only"] += 1
        for version in source_versions:
            if version.parser_status.value == "parsed":
                source_status["parsed"] += 1
            elif version.parser_status.value == "failed":
                source_status["parse_failed"] += 1
            elif version.parser_status.value in {"pending", "parsing"}:
                source_status["pending_parse"] += 1
    pending = RunApprovalRepository(session).list_for_run(
        run.project_id,
        run.id,
        status=ApprovalStatus.PENDING,
    )
    pending_approval = (
        ApprovalResponse.model_validate(pending[-1]) if pending else None
    )
    queries = SearchQueryRepository(session).list_for_run(
        run.project_id,
        run.id,
    )
    search_status = {
        "queries": len(queries),
        "completed": 0,
        "running": 0,
        "failed": 0,
        "results": 0,
        "rounds": 0,
    }
    for query in queries:
        search_status["results"] += query.result_count
        search_status["rounds"] = max(
            search_status["rounds"],
            query.round_index,
        )
        if query.status.value == "completed":
            search_status["completed"] += 1
        elif query.status.value == "running":
            search_status["running"] += 1
        elif query.status.value == "failed":
            search_status["failed"] += 1
    events = RunEventRepository(session).list_after(
        run.project_id,
        run.id,
        after_id=0,
        limit=1000,
    )
    gaps = []
    if evidence:
        for question in questions:
            if question.coverage_status.value == "covered":
                continue
            gaps.append(
                {
                    "question_id": str(question.id),
                    "question": question.question,
                    "severity": (
                        "high"
                        if question.coverage_status.value == "insufficient"
                        else "medium"
                    ),
                    "coverage_status": question.coverage_status.value,
                }
            )
    claims = ClaimRepository(session).list_for_run(run.project_id, run.id)
    sections = DraftSectionRepository(session).list_latest_for_run(
        run.project_id,
        run.id,
    )
    artifacts = ArtifactRepository(session).list_for_run(run.project_id, run.id)
    return RunOverviewResponse(
        run=RunResponse.model_validate(run),
        project=ProjectResponse.model_validate(project),
        questions=[
            ResearchQuestionResponse(
                id=item.id,
                question=item.question,
                priority=item.priority,
                parent_id=item.parent_id,
                coverage_status=item.coverage_status.value,
                created_at=item.created_at,
            )
            for item in questions
        ],
        counts={
            "sources": len(sources),
            "evidence": len(evidence),
            "claims": len(claims),
            "sections": len(sections),
            "artifacts": len(artifacts),
        },
        source_status=source_status,
        search_status=search_status,
        gaps=gaps,
        pending_approval=pending_approval,
        latest_events=[
            EventResponse.model_validate(item) for item in events[-20:]
        ],
    )


@router.get(
    "/runs/{run_id}/questions",
    response_model=QuestionListResponse,
)
def list_run_questions(
    run_id: UUID,
    session: SessionDep,
) -> QuestionListResponse:
    run, _ = _run_and_project(session, run_id)
    questions = ResearchQuestionRepository(session).list_for_run(
        run.project_id,
        run.id,
    )
    return QuestionListResponse(
        items=[
            ResearchQuestionResponse(
                id=item.id,
                question=item.question,
                priority=item.priority,
                parent_id=item.parent_id,
                coverage_status=item.coverage_status.value,
                created_at=item.created_at,
            )
            for item in questions
        ]
    )


@router.get("/runs/{run_id}/sources", response_model=SourceListResponse)
def list_run_sources(run_id: UUID, session: SessionDep) -> SourceListResponse:
    run, _ = _run_and_project(session, run_id)
    evidence = EvidenceRepository(session).list_for_run(run.project_id, run.id)
    sources = _run_sources(session, run, evidence)
    versions = SourceVersionRepository(session).list_for_project(
        run.project_id,
        source_ids=[source.id for source in sources],
    )
    versions_by_source: dict[UUID, list] = {}
    for version in versions:
        versions_by_source.setdefault(version.source_id, []).append(version)
    evidence_by_source: dict[UUID, list] = {}
    for item in evidence:
        evidence_by_source.setdefault(item.source_id, []).append(item)
    items = []
    for source in sources:
        metadata = dict(source.source_metadata or {})
        last_fetch = metadata.get("last_fetch")
        items.append(
            SourceResponse(
                id=source.id,
                title=source.title,
                authors=list(source.authors or []),
                published_at=source.published_at,
                doi=source.doi,
                url=source.canonical_url,
                source_type=source.source_type.value,
                source_origin=source.source_origin.value,
                venue=source.venue,
                publisher=source.publisher,
                metadata=metadata,
                versions=[
                    _version_response(item)
                    for item in versions_by_source.get(source.id, [])
                ],
                question_ids=list(
                    dict.fromkeys(
                        str(item.research_question_id)
                        for item in evidence_by_source.get(source.id, [])
                        if item.research_question_id
                    )
                ),
                evidence_count=len(evidence_by_source.get(source.id, [])),
                fetch_status=(
                    str(last_fetch.get("status"))
                    if isinstance(last_fetch, dict) and last_fetch.get("status")
                    else None
                ),
            )
        )
    return SourceListResponse(items=items)


@router.get("/runs/{run_id}/evidence", response_model=EvidenceListResponse)
def list_run_evidence(
    run_id: UUID,
    session: SessionDep,
    question_id: UUID | None = None,
    evidence_type: str | None = None,
    source_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EvidenceListResponse:
    run, _ = _run_and_project(session, run_id)
    evidence = EvidenceRepository(session).list_for_run(run.project_id, run.id)
    if question_id is not None:
        evidence = [
            item for item in evidence if item.research_question_id == question_id
        ]
    if evidence_type is not None:
        evidence = [
            item for item in evidence if item.evidence_type.value == evidence_type
        ]
    if source_id is not None:
        evidence = [item for item in evidence if item.source_id == source_id]
    total = len(evidence)
    page = evidence[offset : offset + limit]
    source_ids = {item.source_id for item in page}
    version_ids = {item.source_version_id for item in page}
    source_map = {}
    if source_ids:
        source_map = {
            item.id: item
            for item in SourceRepository(session).list_for_project(
                run.project_id,
                limit=500,
            )
            if item.id in source_ids
        }
    version_map = {}
    if version_ids:
        version_map = {
            item.id: item
            for item in SourceVersionRepository(session).list_for_project(
                run.project_id,
                source_ids=list(source_ids),
            )
            if item.id in version_ids
        }
    items = []
    for item in page:
        source = source_map.get(item.source_id)
        version = version_map.get(item.source_version_id)
        items.append(
            EvidenceResponse(
                id=item.id,
                source_id=item.source_id,
                source_version_id=item.source_version_id,
                research_question_id=item.research_question_id,
                evidence_type=item.evidence_type.value,
                claim_summary=item.claim_summary,
                quoted_text=item.quoted_text,
                context_before=item.context_before,
                context_after=item.context_after,
                locator=dict(item.locator or {}),
                verification_status=item.verification_status.value,
                source_title=source.title if source else "",
                source_url=source.canonical_url if source else None,
                source_type=source.source_type.value if source else None,
                source_authors=list(source.authors or []) if source else [],
                source_published_at=source.published_at if source else None,
                source_version_number=version.version_number if version else None,
                source_version_kind=version.content_kind.value if version else None,
                created_at=item.created_at,
            )
        )
    return EvidenceListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}/outline", response_model=OutlineWorkspaceResponse)
def get_run_outline(
    run_id: UUID,
    session: SessionDep,
) -> OutlineWorkspaceResponse:
    run, _ = _run_and_project(session, run_id)
    outline = OutlineRepository(session).latest(run.project_id, run.id)
    if outline is None:
        return OutlineWorkspaceResponse(
            outline=None,
            version=None,
            status=None,
            approved_at=None,
        )
    return OutlineWorkspaceResponse(
        outline=dict(outline.content),
        version=outline.version,
        status=outline.status.value,
        approved_at=outline.approved_at,
    )


def _section_response(
    section,
    audits,
) -> SectionResponse:
    return SectionResponse(
        id=section.id,
        run_id=section.run_id,
        outline_id=section.outline_id,
        section_index=section.section_index,
        title=section.title,
        content_markdown=section.content_markdown,
        citation_map={
            str(key): str(value)
            for key, value in dict(section.citation_map or {}).items()
        },
        evidence_ids=[UUID(str(value)) for value in section.evidence_ids or []],
        version=section.version,
        generation_status=section.generation_status.value,
        content_hash=section.content_hash,
        created_at=section.created_at,
        audit_status=audits[-1].status.value if audits else None,
        audit_details=[item.details for item in audits],
    )


@router.get("/runs/{run_id}/sections", response_model=SectionListResponse)
def list_run_sections(
    run_id: UUID,
    session: SessionDep,
) -> SectionListResponse:
    run, _ = _run_and_project(session, run_id)
    sections = DraftSectionRepository(session).list_latest_for_run(
        run.project_id,
        run.id,
    )
    audit_repo = CitationAuditRepository(session)
    return SectionListResponse(
        items=[
            _section_response(
                section,
                audit_repo.list_for_section(run.project_id, section.id),
            )
            for section in sections
        ]
    )


@router.patch(
    "/runs/{run_id}/sections/{section_id}",
    response_model=SectionResponse,
)
def update_run_section(
    run_id: UUID,
    section_id: UUID,
    data: SectionUpdateRequest,
    session: SessionDep,
) -> SectionResponse:
    run, _ = _run_and_project(session, run_id)
    if run.status not in _TERMINAL:
        raise HTTPException(
            status_code=409,
            detail="Sections can only be edited after the run has finished",
        )
    repo = DraftSectionRepository(session)
    section = repo.get(section_id, project_id=run.project_id)
    if section.run_id != run.id:
        raise EntityNotFound("Draft section not found")
    citation_map = dict(section.citation_map or {})
    used_numbers = citation_markers(data.content_markdown)
    used_markers = [f"S{number}" for number in used_numbers]
    unknown = [marker for marker in used_markers if marker not in citation_map]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="Unknown citation markers: " + ", ".join(unknown),
        )
    filtered_map = {
        marker: citation_map[marker]
        for marker in used_markers
        if marker in citation_map
    }
    cited_source_ids = {str(value) for value in filtered_map.values()}
    evidence = EvidenceRepository(session).list_for_run(run.project_id, run.id)
    existing_ids = {str(value) for value in section.evidence_ids or []}
    evidence_ids = [
        item.id
        for item in evidence
        if str(item.source_id) in cited_source_ids
        and str(item.id) in existing_ids
    ]
    if not evidence_ids:
        evidence_ids = [
            item.id
            for item in evidence
            if str(item.source_id) in cited_source_ids
        ]
    updated = repo.create_version(
        run.project_id,
        run.id,
        DraftSectionCreate(
            outline_id=section.outline_id,
            section_index=section.section_index,
            title=data.title or section.title,
            content_markdown=data.content_markdown,
            citation_map=filtered_map,
            evidence_ids=evidence_ids,
            generation_status=DraftSectionStatus.DRAFTED,
        ),
    )
    if section.outline_id is not None:
        OutlineRepository(session).update_section_statuses(
            section.outline_id,
            {section.section_index: "drafted"},
            project_id=run.project_id,
        )
    session.commit()
    return _section_response(updated, [])


@router.get("/runs/{run_id}/claims", response_model=ClaimListResponse)
def list_run_claims(
    run_id: UUID,
    session: SessionDep,
) -> ClaimListResponse:
    run, _ = _run_and_project(session, run_id)
    claims = ClaimRepository(session).list_for_run(run.project_id, run.id)
    links = ClaimEvidenceRepository(session)
    return ClaimListResponse(
        items=[
            ClaimResponse(
                id=claim.id,
                claim_text=claim.claim_text,
                claim_type=claim.claim_type.value,
                verification_status=claim.verification_status.value,
                evidence=[
                    {
                        "evidence_id": link.evidence_id,
                        "relation": link.relation.value,
                        "rationale": link.rationale,
                    }
                    for link in links.list_for_claim(run.project_id, claim.id)
                ],
            )
            for claim in claims
        ]
    )


@router.get("/runs/{run_id}/artifacts", response_model=ArtifactListResponse)
def list_run_artifacts(
    run_id: UUID,
    session: SessionDep,
) -> ArtifactListResponse:
    run, _ = _run_and_project(session, run_id)
    artifacts = ArtifactRepository(session).list_for_run(run.project_id, run.id)
    return ArtifactListResponse(
        items=[
            _artifact_response(item)
            for item in sorted(
                artifacts,
                key=lambda item: (item.artifact_type.value, -item.version),
            )
        ]
    )


@router.get("/runs/{run_id}/artifacts/{artifact_id}/download")
def download_run_artifact(
    run_id: UUID,
    artifact_id: UUID,
    session: SessionDep,
) -> Response:
    run, _ = _run_and_project(session, run_id)
    artifact = ArtifactRepository(session).get(
        artifact_id,
        project_id=run.project_id,
    )
    if artifact.run_id != run.id:
        raise EntityNotFound("Artifact not found")
    try:
        payload = get_file_storage().read(artifact.storage_key)
    except (StorageError, OSError) as exc:
        raise HTTPException(
            status_code=404,
            detail="Artifact file is missing from storage",
        ) from exc
    extension = str(dict(artifact.artifact_metadata or {}).get("format") or "md")
    filename = f"{artifact.artifact_type.value}-v{artifact.version}.{extension}"
    return Response(
        content=payload,
        media_type=artifact.mime_type,
        headers={
            "Content-Disposition": content_disposition(filename, artifact.mime_type),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/runs/{run_id}/verification")
def get_run_verification(
    run_id: UUID,
    session: SessionDep,
) -> dict[str, Any]:
    run, _ = _run_and_project(session, run_id)
    return _read_verification(session, run)


@router.post(
    "/runs/{run_id}/export",
    response_model=ExportResponse,
    status_code=status.HTTP_201_CREATED,
)
def export_run_artifacts(run_id: UUID, session: SessionDep) -> ExportResponse:
    run, _ = _run_and_project(session, run_id)
    session.commit()
    try:
        result = export_run(
            get_database(),
            get_file_storage(),
            get_settings(),
            run.project_id,
            run.id,
        )
    except PermanentRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ExportResponse(
        artifacts=[
            _artifact_response(item) for item in result.artifacts
        ],
        claims=result.claims,
        citation_audit=result.citation_audit.model_dump(mode="json"),
        reference_audit=result.reference_audit.model_dump(mode="json"),
        section_count=result.section_count,
    )
