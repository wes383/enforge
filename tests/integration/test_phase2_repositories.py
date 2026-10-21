import hashlib
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from dr_core.db.enums import (
    ArtifactType,
    CitationAuditStatus,
    CitationIssueType,
    ClaimVerificationStatus,
    EvidenceRelation,
    EvidenceVerificationStatus,
    OutlineStatus,
    ParserStatus,
    QuestionCoverageStatus,
    RunEventType,
    RunStatus,
    SearchProviderName,
    SourceOrigin,
    SourceType,
    SourceVersionKind,
)
from dr_core.db.models import (
    Artifact,
    CitationAudit,
    Claim,
    ClaimEvidence,
    DocumentChunk,
    DraftSection,
    EvidenceItem,
    Outline,
    ResearchProject,
    ResearchQuestion,
    ResearchRun,
    RunEvent,
    SearchQuery,
    Source,
    SourceVersion,
)
from dr_core.db.repositories import (
    ArtifactRepository,
    CitationAuditRepository,
    ClaimEvidenceRepository,
    ClaimRepository,
    DocumentChunkRepository,
    DraftSectionRepository,
    DuplicateEntity,
    EntityNotFound,
    EvidenceRepository,
    OutlineRepository,
    ProjectRepository,
    ResearchQuestionRepository,
    RunEventRepository,
    RunRepository,
    RepositoryError,
    SearchQueryRepository,
    SourceRepository,
    SourceVersionRepository,
)
from dr_core.embeddings import EmbeddingDimensionMismatch
from dr_core.schemas import (
    ArtifactCreate,
    CitationAuditCreate,
    ClaimCreate,
    ClaimEvidenceCreate,
    DocumentChunkCreate,
    DraftSectionCreate,
    EvidenceCreate,
    OutlineCreate,
    ProjectCreate,
    ProjectUpdate,
    ResearchQuestionCreate,
    RunCreate,
    RunEventCreate,
    SearchQueryCreate,
    SourceCreate,
    SourceVersionCreate,
)

pytestmark = pytest.mark.integration

EMBEDDING_DIMENSION = 1536


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _vector(primary: float, secondary: float = 0.0) -> list[float]:
    values = [0.0] * EMBEDDING_DIMENSION
    values[0] = primary
    values[1] = secondary
    return values


def _create_source(session, project, suffix: str = "1") -> Source:
    return SourceRepository(session).create(
        project.id,
        SourceCreate(
            title=f"Evidence paper {suffix}",
            authors=["Ada Lovelace", "Alan Turing"],
            doi=f"https://doi.org/10.1234/PHASE2.{suffix}",
            url=f"https://example.com/papers/{suffix}?utm_source=test",
            source_type=SourceType.JOURNAL_ARTICLE,
            source_origin=SourceOrigin.ACADEMIC_SEARCH,
            publisher="Example Press",
            metadata={"peer_reviewed": True},
        ),
    )


def _create_version(
    session,
    project,
    source,
    *,
    content: str = "Full text content",
    content_kind: SourceVersionKind = SourceVersionKind.FULL_TEXT,
    storage_key: str | None = None,
) -> SourceVersion:
    if storage_key is None and content_kind is not SourceVersionKind.SEARCH_SNIPPET:
        storage_key = f"projects/{project.id}/sources/{source.id}/original.txt"
    return SourceVersionRepository(session).create(
        project.id,
        source.id,
        SourceVersionCreate(
            content_kind=content_kind,
            content_hash=_hash(content),
            storage_key=storage_key,
            mime_type="text/plain",
            size_bytes=len(content.encode("utf-8")),
            license="CC-BY-4.0",
        ),
    )


def _create_evidence(
    session,
    project,
    run,
    source,
    version,
    *,
    quote: str = "The quoted evidence.",
    question_id=None,
    evidence_type="direct_support",
) -> EvidenceItem:
    return EvidenceRepository(session).create(
        project.id,
        run.id,
        EvidenceCreate(
            source_id=source.id,
            source_version_id=version.id,
            research_question_id=question_id,
            evidence_type=evidence_type,
            claim_summary="A traceable finding",
            quoted_text=quote,
            context_before="Before the quote.",
            context_after="After the quote.",
            locator={"page": 3, "section": "Results", "paragraph": 2},
        ),
    )


def test_pgvector_extension_hnsw_and_enum_constraints(session):
    extension_version = session.scalar(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    )
    assert extension_version

    index_definition = session.scalar(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname = 'ix_document_chunks_embedding_hnsw'"
        )
    )
    assert index_definition is not None
    assert "USING hnsw" in index_definition
    assert "vector_cosine_ops" in index_definition

    run_checks = session.scalars(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'research_runs'::regclass AND contype = 'c'"
        )
    ).all()
    assert any("queued" in definition for definition in run_checks)

    artifact_checks = session.scalars(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'artifacts'::regclass AND contype = 'c'"
        )
    ).all()
    assert any("storage_key" in definition for definition in artifact_checks)


def test_database_is_at_alembic_head(session):
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from dr_core.config import get_settings

    config = Config(str(get_settings().alembic_config_path))
    head = ScriptDirectory.from_config(config).get_current_head()
    current = session.scalar(text("SELECT version_num FROM alembic_version"))
    assert current == head


def test_project_crud_soft_delete_and_restore(session, project):
    repository = ProjectRepository(session)
    loaded = repository.get(project.id)
    assert loaded.topic.startswith("Traceable evidence")
    assert loaded.language.value == "zh"
    assert loaded.status.value == "active"
    assert loaded.created_at is not None
    assert str(loaded.id) in {
        str(item.id) for item in repository.list(limit=200)
    }

    updated = repository.update(
        project.id,
        ProjectUpdate(
            title="Updated title",
            research_scope="Peer-reviewed sources only",
            target_audience="Researchers",
            budget_usd=Decimal("25.0000"),
        ),
    )
    assert updated.title == "Updated title"
    assert updated.research_scope == "Peer-reviewed sources only"
    assert updated.budget_usd == Decimal("25.0000")

    deleted = repository.soft_delete(project.id)
    assert deleted.deleted_at is not None
    assert deleted.status.value == "archived"
    with pytest.raises(EntityNotFound):
        repository.get(project.id)
    assert project.id not in {item.id for item in repository.list(limit=200)}
    assert project.id in {
        item.id for item in repository.list(limit=200, include_deleted=True)
    }

    restored = repository.restore(project.id)
    assert restored.deleted_at is None
    assert restored.status.value == "active"


def test_run_idempotency_status_and_project_scope(session, project, project_factory):
    other_project = project_factory("Second project")
    repository = RunRepository(session)

    first = repository.create(project.id, RunCreate(idempotency_key="run-key-1"))
    duplicate = repository.create(project.id, RunCreate(idempotency_key="run-key-1"))
    other = repository.create(
        other_project.id,
        RunCreate(idempotency_key="run-key-1"),
    )

    assert duplicate.id == first.id
    assert other.id != first.id
    assert first.workflow_thread_id
    assert first.status is RunStatus.QUEUED

    started = repository.update_status(first.id, RunStatus.PLANNING)
    assert started.started_at is not None
    assert started.finished_at is None

    failed = repository.update_status(
        first.id,
        RunStatus.FAILED,
        error_summary="Provider timeout",
    )
    assert failed.finished_at is not None
    assert failed.error_summary == "Provider timeout"

    with_checkpoint = repository.attach_checkpoint(first.id, "checkpoint-42")
    assert with_checkpoint.checkpoint_id == "checkpoint-42"

    with pytest.raises(EntityNotFound):
        repository.get(first.id, project_id=other_project.id)
    assert first.id in {
        item.id for item in repository.list_for_project(project.id)
    }
    assert first.id not in {
        item.id for item in repository.list_for_project(other_project.id)
    }


def test_questions_and_search_queries_are_idempotent(session, project, run):
    question_repository = ResearchQuestionRepository(session)
    question = question_repository.create(
        project.id,
        run.id,
        ResearchQuestionCreate(question="What is the evidence?", priority=5),
    )
    duplicate = question_repository.create(
        project.id,
        run.id,
        ResearchQuestionCreate(question="  what   is the EVIDENCE?  ", priority=1),
    )
    child = question_repository.create(
        project.id,
        run.id,
        ResearchQuestionCreate(
            question="Which studies are most reliable?",
            parent_id=question.id,
        ),
    )

    assert duplicate.id == question.id
    assert child.parent_id == question.id
    assert len(question_repository.list_for_run(project.id, run.id)) == 2

    covered = question_repository.update_coverage(
        question.id,
        QuestionCoverageStatus.COVERED,
        project_id=project.id,
    )
    assert covered.coverage_status is QuestionCoverageStatus.COVERED

    query_repository = SearchQueryRepository(session)
    query = query_repository.create(
        project.id,
        run.id,
        SearchQueryCreate(
            query_text="traceable evidence",
            provider=SearchProviderName.TAVILY,
            research_question_id=question.id,
        ),
    )
    duplicate_query = query_repository.create(
        project.id,
        run.id,
        SearchQueryCreate(
            query_text="Traceable   Evidence",
            provider=SearchProviderName.TAVILY,
            research_question_id=question.id,
        ),
    )
    other_provider = query_repository.create(
        project.id,
        run.id,
        SearchQueryCreate(
            query_text="traceable evidence",
            provider=SearchProviderName.CROSSREF,
        ),
    )
    next_round = query_repository.create(
        project.id,
        run.id,
        SearchQueryCreate(
            query_text="traceable evidence",
            provider=SearchProviderName.TAVILY,
            round_index=2,
        ),
    )

    assert duplicate_query.id == query.id
    assert other_provider.id != query.id
    assert next_round.id != query.id
    assert len(query_repository.list_for_run(project.id, run.id)) == 3

    completed = query_repository.mark_completed(
        query.id,
        project_id=project.id,
        result_count=12,
    )
    assert completed.status.value == "completed"
    assert completed.result_count == 12
    assert completed.executed_at is not None

    second_run = RunRepository(session).create(project.id, RunCreate())
    with pytest.raises(EntityNotFound):
        query_repository.create(
            project.id,
            second_run.id,
            SearchQueryCreate(
                query_text="foreign question",
                provider=SearchProviderName.TAVILY,
                research_question_id=question.id,
            ),
        )


def test_sources_are_deduplicated_by_doi_and_canonical_url(session, project, project_factory):
    other_project = project_factory("Source isolation project")
    repository = SourceRepository(session)

    first = repository.get_or_create(
        project.id,
        SourceCreate(
            title="Canonical paper",
            doi="https://doi.org/10.5555/ABC.001",
            url="https://example.org/paper?utm_source=newsletter",
            source_type=SourceType.JOURNAL_ARTICLE,
            authors=["Grace Hopper"],
            metadata={"peer_reviewed": True},
        ),
    )
    same_doi = repository.get_or_create(
        project.id,
        SourceCreate(
            title="Duplicate metadata",
            doi="doi:10.5555/abc.001",
        ),
    )
    same_url = repository.get_or_create(
        project.id,
        SourceCreate(
            title="Same URL",
            url="https://EXAMPLE.org/paper/#section",
        ),
    )
    foreign = repository.get_or_create(
        other_project.id,
        SourceCreate(
            title="Same DOI other project",
            doi="10.5555/abc.001",
        ),
    )

    assert first.doi == "10.5555/abc.001"
    assert first.canonical_url == "https://example.org/paper"
    assert same_doi.id == first.id
    assert same_url.id == first.id
    assert foreign.id != first.id

    session.refresh(first)
    assert first.authors == ["Grace Hopper"]
    assert first.source_metadata == {"peer_reviewed": True}

    duplicate = Source(
        project_id=project.id,
        title="Constraint duplicate",
        doi=first.doi,
    )
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(duplicate)
            session.flush()

    assert repository.find_by_doi(project.id, "https://doi.org/10.5555/ABC.001")
    assert repository.find_by_url(project.id, "https://example.org/paper")


def test_source_versions_hash_dedup_and_storage_key_constraint(session, project):
    source = _create_source(session, project)
    repository = SourceVersionRepository(session)

    first = _create_version(session, project, source, content="Version one")
    duplicate = _create_version(session, project, source, content="Version one")
    second = _create_version(session, project, source, content="Version two")
    snippet = _create_version(
        session,
        project,
        source,
        content="Search snippet only",
        content_kind=SourceVersionKind.SEARCH_SNIPPET,
        storage_key=None,
    )
    same_content_snippet = _create_version(
        session,
        project,
        source,
        content="Version one",
        content_kind=SourceVersionKind.SEARCH_SNIPPET,
        storage_key=None,
    )
    duplicate_snippet = _create_version(
        session,
        project,
        source,
        content="Version one",
        content_kind=SourceVersionKind.SEARCH_SNIPPET,
        storage_key=None,
    )

    assert duplicate.id == first.id
    assert first.version_number == 1
    assert second.version_number == 2
    assert snippet.version_number == 3
    assert same_content_snippet.version_number == 4
    assert same_content_snippet.id != first.id
    assert duplicate_snippet.id == same_content_snippet.id
    assert snippet.storage_key is None
    assert len(repository.list_for_source(source.id, project_id=project.id)) == 4

    parsed = repository.mark_parsed(
        first.id,
        project_id=project.id,
        parser_version="pypdf-6",
    )
    assert parsed.parser_status is ParserStatus.PARSED
    assert parsed.parser_version == "pypdf-6"

    failed = repository.mark_parse_failed(
        second.id,
        project_id=project.id,
        error="Encrypted PDF",
    )
    assert failed.parser_status is ParserStatus.FAILED
    assert failed.parse_error == "Encrypted PDF"

    unsafe = SourceVersion(
        project_id=project.id,
        source_id=source.id,
        version_number=99,
        content_kind=SourceVersionKind.FULL_TEXT,
        content_hash=_hash("unsafe"),
        storage_key="/absolute/original.pdf",
    )
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(unsafe)
            session.flush()


def test_document_chunks_and_pgvector_search(session, project):
    source = _create_source(session, project)
    version = _create_version(session, project, source)
    repository = DocumentChunkRepository(session)

    chunks = repository.create_many(
        project.id,
        version.id,
        [
            DocumentChunkCreate(
                chunk_index=0,
                section_title="Introduction",
                page_start=1,
                page_end=1,
                text="First chunk",
                embedding=_vector(1.0),
                embedding_model="test-embedding",
            ),
            DocumentChunkCreate(
                chunk_index=1,
                section_title="Methods",
                page_start=2,
                page_end=2,
                text="Second chunk",
                embedding=_vector(0.0, 1.0),
                embedding_model="test-embedding",
            ),
            DocumentChunkCreate(
                chunk_index=2,
                section_title="Results",
                page_start=3,
                page_end=3,
                text="Third chunk",
                embedding=_vector(0.6, 0.8),
                embedding_model="test-embedding",
            ),
        ],
    )
    assert len(chunks) == 3
    assert chunks[0].text_hash == _hash("First chunk")

    matches = repository.search_similar(project.id, _vector(1.0), limit=3)
    assert [match.chunk.id for match in matches] == [
        chunks[0].id,
        chunks[2].id,
        chunks[1].id,
    ]
    assert matches[0].distance == pytest.approx(0.0, abs=1e-6)

    filtered = repository.search_similar(
        project.id,
        _vector(1.0),
        source_version_id=version.id,
    )
    assert len(filtered) == 3

    with pytest.raises(EmbeddingDimensionMismatch):
        repository.search_similar(project.id, [1.0, 0.0])

    with pytest.raises(DBAPIError):
        with session.begin_nested():
            session.execute(
                text(
                    "UPDATE document_chunks SET embedding = '[1,2,3]'::vector "
                    "WHERE id = :chunk_id"
                ),
                {"chunk_id": str(chunks[0].id)},
            )

    updated = repository.set_embedding(
        chunks[0].id,
        _vector(0.0, 1.0),
        project_id=project.id,
        embedding_model="other-embedding",
    )
    assert updated.embedding_model == "other-embedding"


def test_evidence_traceability_claims_and_links(session, project, run):
    question = ResearchQuestionRepository(session).create(
        project.id,
        run.id,
        ResearchQuestionCreate(question="Is the effect real?"),
    )
    source = _create_source(session, project)
    version = _create_version(session, project, source)

    evidence_repository = EvidenceRepository(session)
    first = _create_evidence(
        session,
        project,
        run,
        source,
        version,
        quote="The effect was statistically significant.",
        question_id=question.id,
    )
    duplicate = _create_evidence(
        session,
        project,
        run,
        source,
        version,
        quote="  The effect was statistically   significant.  ",
        question_id=question.id,
    )
    counter = _create_evidence(
        session,
        project,
        run,
        source,
        version,
        quote="A later cohort did not replicate the effect.",
        question_id=question.id,
        evidence_type="refutation",
    )

    second_question = ResearchQuestionRepository(session).create(
        project.id,
        run.id,
        ResearchQuestionCreate(question="Does the effect hold across cohorts?"),
    )
    same_quote_for_other_question = _create_evidence(
        session,
        project,
        run,
        source,
        version,
        quote="The effect was statistically significant.",
        question_id=second_question.id,
    )

    assert duplicate.id == first.id
    assert same_quote_for_other_question.id != first.id
    assert first.source_id == source.id
    assert first.source_version_id == version.id
    assert first.locator["page"] == 3
    assert len(evidence_repository.list_for_question(project.id, question.id)) == 2
    assert (
        len(evidence_repository.list_for_question(project.id, second_question.id))
        == 1
    )
    assert len(evidence_repository.list_for_source_version(project.id, version.id)) == 3

    verified = evidence_repository.mark_verification(
        first.id,
        EvidenceVerificationStatus.VERIFIED,
        project_id=project.id,
    )
    assert verified.verification_status is EvidenceVerificationStatus.VERIFIED

    claim_repository = ClaimRepository(session)
    claim = claim_repository.create(
        project.id,
        run.id,
        ClaimCreate(claim_text="The intervention has a significant effect."),
    )
    duplicate_claim = claim_repository.create(
        project.id,
        run.id,
        ClaimCreate(claim_text="the intervention has a significant effect."),
    )
    assert duplicate_claim.id == claim.id

    link_repository = ClaimEvidenceRepository(session)
    support = link_repository.link(
        project.id,
        claim.id,
        ClaimEvidenceCreate(
            evidence_id=first.id,
            relation=EvidenceRelation.SUPPORTS,
            rationale="Direct statistical evidence",
        ),
    )
    refute = link_repository.link(
        project.id,
        claim.id,
        ClaimEvidenceCreate(
            evidence_id=counter.id,
            relation=EvidenceRelation.REFUTES,
        ),
    )
    reclassified = link_repository.link(
        project.id,
        claim.id,
        ClaimEvidenceCreate(
            evidence_id=first.id,
            relation=EvidenceRelation.PARTIALLY_SUPPORTS,
        ),
    )

    assert support.id == reclassified.id
    assert reclassified.relation is EvidenceRelation.PARTIALLY_SUPPORTS
    assert refute.relation is EvidenceRelation.REFUTES
    assert len(link_repository.list_for_claim(project.id, claim.id)) == 2
    assert len(link_repository.list_for_evidence(project.id, first.id)) == 1

    updated_claim = claim_repository.update_verification(
        claim.id,
        ClaimVerificationStatus.PARTIAL,
        project_id=project.id,
    )
    assert updated_claim.verification_status is ClaimVerificationStatus.PARTIAL

    other_run = RunRepository(session).create(project.id, RunCreate())
    other_evidence = _create_evidence(
        session,
        project,
        other_run,
        source,
        version,
        quote="Evidence from another run.",
    )
    with pytest.raises(RepositoryError):
        link_repository.link(
            project.id,
            claim.id,
            ClaimEvidenceCreate(
                evidence_id=other_evidence.id,
                relation=EvidenceRelation.RELATED,
            ),
        )

    other_source = _create_source(session, project, suffix="2")
    with pytest.raises(EntityNotFound):
        evidence_repository.create(
            project.id,
            run.id,
            EvidenceCreate(
                source_id=other_source.id,
                source_version_id=version.id,
                claim_summary="Mismatched source",
                quoted_text="Mismatched version.",
            ),
        )


def test_outline_versions_sections_and_citation_audits(session, project, run):
    outline_repository = OutlineRepository(session)
    first = outline_repository.create_version(
        project.id,
        run.id,
        OutlineCreate(content={"sections": [{"title": "Introduction"}]}),
    )
    first_approved = outline_repository.approve(first.id, project_id=project.id)
    assert first_approved.status is OutlineStatus.APPROVED

    second = outline_repository.create_version(
        project.id,
        run.id,
        OutlineCreate(
            content={"sections": [{"title": "Introduction"}, {"title": "Results"}]},
            version_note="Expanded after evidence review",
        ),
    )

    assert first.version == 1
    assert second.version == 2
    assert outline_repository.latest(project.id, run.id).id == second.id

    approved = outline_repository.approve(second.id, project_id=project.id)
    session.refresh(first)
    assert approved.status is OutlineStatus.APPROVED
    assert approved.approved_at is not None
    assert first.status is OutlineStatus.SUPERSEDED

    section_repository = DraftSectionRepository(session)
    section_v1 = section_repository.create_version(
        project.id,
        run.id,
        DraftSectionCreate(
            outline_id=second.id,
            section_index=0,
            title="Introduction",
            content_markdown="First draft [1].",
            citation_map={"1": str(uuid4())},
        ),
    )
    section_v2 = section_repository.create_version(
        project.id,
        run.id,
        DraftSectionCreate(
            outline_id=second.id,
            section_index=0,
            title="Introduction",
            content_markdown="Revised draft [1].",
            citation_map={"1": str(uuid4())},
        ),
    )

    assert section_v1.version == 1
    assert section_v2.version == 2
    latest = section_repository.list_latest_for_run(project.id, run.id)
    assert len(latest) == 1
    assert latest[0].id == section_v2.id
    assert section_v2.content_hash == _hash("Revised draft [1].")
    assert section_repository.get_latest(project.id, run.id, 0).id == section_v2.id

    claim = ClaimRepository(session).create(
        project.id,
        run.id,
        ClaimCreate(claim_text="A cited claim."),
    )
    audit_repository = CitationAuditRepository(session)
    passed = audit_repository.create(
        project.id,
        run.id,
        CitationAuditCreate(
            draft_section_id=section_v2.id,
            claim_id=claim.id,
            citation_marker="[1]",
            status=CitationAuditStatus.PASSED,
            details="Citation resolves to a source version.",
        ),
    )
    failed = audit_repository.create(
        project.id,
        run.id,
        CitationAuditCreate(
            draft_section_id=section_v2.id,
            claim_id=claim.id,
            citation_marker="[2]",
            status=CitationAuditStatus.FAILED,
            issue_type=CitationIssueType.LOCATOR_MISSING,
            details="The source version lacks a page locator.",
            correction_note="Add page locator in the next revision.",
            corrected_in_version=3,
        ),
    )

    assert passed.status is CitationAuditStatus.PASSED
    assert failed.issue_type is CitationIssueType.LOCATOR_MISSING
    assert len(audit_repository.list_for_section(project.id, section_v2.id)) == 2
    assert len(audit_repository.list_for_run(project.id, run.id)) == 2

    other_run = RunRepository(session).create(project.id, RunCreate())
    with pytest.raises(EntityNotFound):
        section_repository.create_version(
            project.id,
            other_run.id,
            DraftSectionCreate(
                outline_id=second.id,
                section_index=0,
                title="Foreign outline",
            ),
        )


def test_run_events_and_artifact_versions(session, project, run):
    event_repository = RunEventRepository(session)
    first = event_repository.append(
        project.id,
        run.id,
        RunEventCreate(
            event_type=RunEventType.PHASE_STARTED,
            stage=RunStatus.SEARCHING,
            message="Searching sources",
        ),
    )
    deduped = event_repository.append(
        project.id,
        run.id,
        RunEventCreate(
            event_type=RunEventType.PROGRESS,
            message="25 percent",
            dedupe_key="progress-25",
        ),
    )
    duplicate = event_repository.append(
        project.id,
        run.id,
        RunEventCreate(
            event_type=RunEventType.PROGRESS,
            message="25 percent again",
            dedupe_key="progress-25",
        ),
    )
    third = event_repository.append(
        project.id,
        run.id,
        RunEventCreate(
            event_type=RunEventType.PHASE_COMPLETED,
            stage=RunStatus.SEARCHING,
        ),
    )

    assert duplicate.id == deduped.id
    assert [event.id for event in event_repository.list_after(project.id, run.id)] == [
        first.id,
        deduped.id,
        third.id,
    ]
    assert [
        event.id
        for event in event_repository.list_after(
            project.id,
            run.id,
            after_id=first.id,
        )
    ] == [deduped.id, third.id]
    assert event_repository.latest_id(project.id, run.id) == third.id

    artifact_repository = ArtifactRepository(session)
    artifact = artifact_repository.register(
        project.id,
        run.id,
        ArtifactCreate(
            artifact_type=ArtifactType.RESEARCH_REPORT,
            title="Final report",
            storage_key=f"projects/{project.id}/runs/{run.id}/report.md",
            mime_type="text/markdown",
            size_bytes=42,
            content_hash=_hash("report"),
        ),
    )
    duplicate_artifact = artifact_repository.register(
        project.id,
        run.id,
        ArtifactCreate(
            artifact_type=ArtifactType.RESEARCH_REPORT,
            title="Final report",
            storage_key=artifact.storage_key,
            mime_type="text/markdown",
            size_bytes=42,
            content_hash=_hash("report"),
        ),
    )
    assert duplicate_artifact.id == artifact.id

    with pytest.raises(DuplicateEntity):
        artifact_repository.register(
            project.id,
            run.id,
            ArtifactCreate(
                artifact_type=ArtifactType.RESEARCH_REPORT,
                storage_key=f"projects/{project.id}/runs/{run.id}/report-v2.md",
                mime_type="text/markdown",
                size_bytes=42,
                content_hash=_hash("report v2"),
            ),
        )

    other_run = RunRepository(session).create(project.id, RunCreate())
    with pytest.raises(DuplicateEntity):
        artifact_repository.register(
            project.id,
            other_run.id,
            ArtifactCreate(
                artifact_type=ArtifactType.MARKDOWN_EXPORT,
                storage_key=artifact.storage_key,
                mime_type="text/markdown",
                size_bytes=42,
                content_hash=_hash("report"),
            ),
        )

    unsafe = Artifact(
        project_id=project.id,
        run_id=run.id,
        artifact_type=ArtifactType.OTHER,
        version=9,
        storage_key="/absolute/report.md",
        mime_type="text/markdown",
        size_bytes=1,
        content_hash=_hash("unsafe"),
    )
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(unsafe)
            session.flush()

    assert len(artifact_repository.list_for_run(project.id, run.id)) == 1


def test_project_delete_cascades_all_research_records(session, project):
    other_project = ProjectRepository(session).create(
        ProjectCreate(
            title="Cascade isolation",
            topic="Cascade isolation topic",
        )
    )
    try:
        run = RunRepository(session).create(project.id, RunCreate())
        question = ResearchQuestionRepository(session).create(
            project.id,
            run.id,
            ResearchQuestionCreate(question="Cascade question?"),
        )
        SearchQueryRepository(session).create(
            project.id,
            run.id,
            SearchQueryCreate(
                query_text="cascade query",
                provider=SearchProviderName.TAVILY,
                research_question_id=question.id,
            ),
        )
        source = _create_source(session, project)
        version = _create_version(session, project, source)
        DocumentChunkRepository(session).create_many(
            project.id,
            version.id,
            [
                DocumentChunkCreate(
                    chunk_index=0,
                    text="Cascade chunk",
                    embedding=_vector(1.0),
                    embedding_model="test-embedding",
                )
            ],
        )
        evidence = _create_evidence(session, project, run, source, version)
        claim = ClaimRepository(session).create(
            project.id,
            run.id,
            ClaimCreate(claim_text="Cascade claim."),
        )
        ClaimEvidenceRepository(session).link(
            project.id,
            claim.id,
            ClaimEvidenceCreate(
                evidence_id=evidence.id,
                relation=EvidenceRelation.SUPPORTS,
            ),
        )
        outline = OutlineRepository(session).create_version(
            project.id,
            run.id,
            OutlineCreate(content={"sections": ["One"]}),
        )
        section = DraftSectionRepository(session).create_version(
            project.id,
            run.id,
            DraftSectionCreate(
                outline_id=outline.id,
                section_index=0,
                title="Section",
                content_markdown="Text [1].",
            ),
        )
        CitationAuditRepository(session).create(
            project.id,
            run.id,
            CitationAuditCreate(
                draft_section_id=section.id,
                status=CitationAuditStatus.PASSED,
                details="Resolved.",
            ),
        )
        RunEventRepository(session).append(
            project.id,
            run.id,
            RunEventCreate(event_type=RunEventType.PHASE_STARTED),
        )
        ArtifactRepository(session).register(
            project.id,
            run.id,
            ArtifactCreate(
                artifact_type=ArtifactType.MARKDOWN_EXPORT,
                storage_key=f"projects/{project.id}/runs/{run.id}/report.md",
                mime_type="text/markdown",
                size_bytes=1,
                content_hash=_hash("cascade"),
            ),
        )

        session.execute(
            delete(ResearchProject).where(ResearchProject.id == project.id)
        )
        session.flush()

        models = [
            ResearchRun,
            ResearchQuestion,
            SearchQuery,
            Source,
            SourceVersion,
            DocumentChunk,
            EvidenceItem,
            Claim,
            ClaimEvidence,
            Outline,
            DraftSection,
            CitationAudit,
            RunEvent,
            Artifact,
        ]
        for model in models:
            count = session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.project_id == project.id)
            )
            assert count == 0, f"{model.__tablename__} still has rows"

        assert session.scalar(
            select(func.count())
            .select_from(ResearchProject)
            .where(ResearchProject.id == other_project.id)
        ) == 1
    finally:
        session.execute(
            delete(ResearchProject).where(ResearchProject.id == other_project.id)
        )
