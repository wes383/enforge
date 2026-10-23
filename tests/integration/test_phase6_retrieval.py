import hashlib
from datetime import date

import pytest
from sqlalchemy import select, text

from dr_core.config import Settings, get_settings
from dr_core.db.enums import (
    EvidenceType,
    EvidenceVerificationStatus,
    SourceOrigin,
    SourceType,
    SourceVersionKind,
)
from dr_core.db.models import DocumentChunk
from dr_core.db.repositories import (
    DocumentChunkRepository,
    EvidenceRepository,
    OutlineSectionRepository,
    SourceRepository,
    SourceVersionRepository,
)
from dr_core.research.retrieval import HybridRetriever, RetrievalFilters
from dr_core.schemas import (
    DocumentChunkCreate,
    EvidenceCreate,
    OutlineSectionCreate,
    SourceCreate,
    SourceVersionCreate,
)

pytestmark = pytest.mark.integration

DIMENSION = get_settings().embedding_dimension


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _vector(primary: float, secondary: float = 0.0) -> list[float]:
    values = [0.0] * DIMENSION
    values[0] = primary
    values[1] = secondary
    return values


class FixedEmbedding:
    model_name = "fixed-test-embedding"
    dimension = DIMENSION

    def embed_texts(self, texts):
        return [_vector(1.0) for _ in texts]


def _source(
    session,
    project,
    *,
    title: str,
    suffix: str,
    source_type: SourceType,
    published_at: date,
    authors: list[str] | None = None,
    doi: str | None = None,
):
    return SourceRepository(session).create(
        project.id,
        SourceCreate(
            title=title,
            authors=authors or [],
            doi=doi,
            url=f"https://example.org/phase6/{suffix}",
            published_at=published_at,
            source_type=source_type,
            source_origin=SourceOrigin.WEB_SEARCH,
        ),
    )


def _version(session, project, source, snippet: str):
    return SourceVersionRepository(session).create(
        project.id,
        source.id,
        SourceVersionCreate(
            content_kind=SourceVersionKind.SEARCH_SNIPPET,
            content_hash=_hash(snippet),
            metadata={"snippet": snippet},
        ),
    )


def _chunk(session, project, version, index: int, chunk_text: str, vector=None):
    return DocumentChunkRepository(session).create_many(
        project.id,
        version.id,
        [
            DocumentChunkCreate(
                chunk_index=index,
                section_title=f"Section {index}",
                text=chunk_text,
                embedding=vector,
                embedding_model="fixed-test-embedding" if vector else None,
            )
        ],
    )[0]


@pytest.fixture
def retrieval_corpus(session, project):
    source_a = _source(
        session,
        project,
        title="Peer reviewed enforcement study",
        suffix="study",
        source_type=SourceType.JOURNAL_ARTICLE,
        published_at=date(2024, 2, 1),
        authors=["Ada Lovelace"],
        doi="10.5555/phase6.study",
    )
    source_b = _source(
        session,
        project,
        title="Independent audit report",
        suffix="audit",
        source_type=SourceType.REPORT,
        published_at=date(2023, 5, 1),
    )
    source_c = _source(
        session,
        project,
        title="Small business cost survey",
        suffix="costs",
        source_type=SourceType.REPORT,
        published_at=date(2018, 3, 1),
    )
    source_d = _source(
        session,
        project,
        title="中文监管报道",
        suffix="zh",
        source_type=SourceType.WEB_PAGE,
        published_at=date(2022, 1, 1),
    )

    text_a = (
        "Regulatory enforcement improves compliance in audited firms "
        "through frequent inspections."
    )
    text_b = "Independent audit results show measurable gains after inspections."
    text_c = "Regulatory enforcement imposes administrative costs on small firms."
    text_zh = "监管执法提高了受审计企业的合规率，并增加了检查频率。"

    version_a = _version(session, project, source_a, text_a)
    version_b = _version(session, project, source_b, text_b)
    version_c = _version(session, project, source_c, text_c)
    version_d = _version(session, project, source_d, text_zh)

    chunk_a = _chunk(session, project, version_a, 0, text_a, _vector(1.0))
    chunk_b = _chunk(session, project, version_b, 0, text_b, _vector(0.98, 0.02))
    chunk_c = _chunk(session, project, version_c, 0, text_c, _vector(0.0, 1.0))
    chunk_d = _chunk(session, project, version_d, 0, text_zh)

    return {
        "sources": {
            "a": source_a,
            "b": source_b,
            "c": source_c,
            "d": source_d,
        },
        "versions": {
            "a": version_a,
            "b": version_b,
            "c": version_c,
            "d": version_d,
        },
        "chunks": {
            "a": chunk_a,
            "b": chunk_b,
            "c": chunk_c,
            "d": chunk_d,
        },
    }


def test_full_text_index_exists(session):
    assert session.scalar(
        text(
            "SELECT count(*) FROM pg_indexes "
            "WHERE indexname = 'ix_document_chunks_text_fts'"
        )
    ) == 1


def test_hybrid_retrieval_fuses_keyword_and_vector(
    session,
    project,
    retrieval_corpus,
):
    result = HybridRetriever(
        session,
        project.id,
        settings=get_settings(),
        embedding=FixedEmbedding(),
    ).search("regulatory enforcement compliance", limit=10)

    assert result.keyword_used
    assert result.vector_used
    assert result.degraded_reason is None
    hits = {hit.chunk_id: hit for hit in result.hits}
    chunk_a = retrieval_corpus["chunks"]["a"]
    chunk_b = retrieval_corpus["chunks"]["b"]
    chunk_c = retrieval_corpus["chunks"]["c"]

    assert chunk_a.id in hits
    assert chunk_b.id in hits
    assert chunk_c.id in hits
    assert hits[chunk_b.id].keyword_rank is None
    assert hits[chunk_b.id].vector_rank is not None
    assert hits[chunk_c.id].keyword_rank is not None
    for hit in result.hits:
        assert hit.text
        assert hit.source_title
        assert hit.source_version_id
        assert hit.locator["source_version_id"] == str(hit.source_version_id)
        assert "chunk_id" in hit.locator
        assert hit.score > 0


def test_retrieval_metadata_filters_apply_to_both_modes(
    session,
    project,
    retrieval_corpus,
):
    retriever = HybridRetriever(
        session,
        project.id,
        settings=get_settings(),
        embedding=FixedEmbedding(),
    )
    reports = retriever.search(
        "regulatory enforcement compliance",
        filters=RetrievalFilters(source_types=[SourceType.REPORT]),
        limit=10,
    )
    assert reports.hits
    assert {hit.source_type for hit in reports.hits} == {SourceType.REPORT}

    recent = retriever.search(
        "regulatory enforcement compliance",
        filters=RetrievalFilters(published_from=date(2023, 1, 1)),
        limit=10,
    )
    assert recent.hits
    assert all(
        hit.published_at is not None and hit.published_at >= date(2023, 1, 1)
        for hit in recent.hits
    )


def test_keyword_search_supports_cjk_terms(session, project, retrieval_corpus):
    result = HybridRetriever(
        session,
        project.id,
        settings=get_settings(),
        embedding=None,
    ).search("监管 合规", limit=10)

    assert result.hits
    assert any(
        hit.chunk_id == retrieval_corpus["chunks"]["d"].id
        for hit in result.hits
    )


def test_vector_unavailable_degrades_to_keyword(
    session,
    project,
    retrieval_corpus,
):
    result = HybridRetriever(
        session,
        project.id,
        settings=get_settings(),
        embedding=None,
    ).search("regulatory enforcement compliance", limit=10)

    assert result.keyword_used
    assert not result.vector_used
    assert result.degraded_reason
    assert result.hits
    assert all(hit.keyword_rank is not None for hit in result.hits)
    assert retrieval_corpus["chunks"]["b"].id not in {
        hit.chunk_id for hit in result.hits
    }


def test_vector_mode_without_embedding_falls_back_to_keyword(
    session,
    project,
    retrieval_corpus,
):
    result = HybridRetriever(
        session,
        project.id,
        settings=Settings(_env_file=None, research_retrieval_mode="vector"),
        embedding=None,
    ).search("regulatory enforcement compliance", limit=10)

    assert result.keyword_used
    assert not result.vector_used
    assert result.degraded_reason
    assert result.hits
    assert all(hit.keyword_rank is not None for hit in result.hits)


def test_retrieval_returns_linked_evidence_ids(
    session,
    project,
    run,
    retrieval_corpus,
):
    section = OutlineSectionRepository(session).create(
        project.id,
        run.id,
        OutlineSectionCreate(
            title="Enforcement and compliance",
            purpose="Assess whether enforcement improves compliance.",
            search_terms=["enforcement compliance"],
        ),
    )
    chunk_b = retrieval_corpus["chunks"]["b"]
    evidence = EvidenceRepository(session).create(
        project.id,
        run.id,
        EvidenceCreate(
            source_id=retrieval_corpus["sources"]["b"].id,
            source_version_id=retrieval_corpus["versions"]["b"].id,
            outline_section_id=section.id,
            evidence_type=EvidenceType.DIRECT_SUPPORT,
            claim_summary="Audits show measurable gains.",
            quoted_text="Independent audit results show measurable gains after inspections.",
            context_before="Audit context.",
            locator={
                "kind": "search_snippet",
                "source_version_id": str(retrieval_corpus["versions"]["b"].id),
                "chunk_id": str(chunk_b.id),
            },
            verification_status=EvidenceVerificationStatus.VERIFIED,
        ),
    )
    session.flush()

    result = HybridRetriever(
        session,
        project.id,
        settings=get_settings(),
        embedding=FixedEmbedding(),
        run_id=run.id,
    ).search("regulatory enforcement compliance", limit=10)
    hit = next(hit for hit in result.hits if hit.chunk_id == chunk_b.id)
    assert hit.evidence_ids == [evidence.id]


def test_document_chunk_fts_index_is_used_for_keyword_search(
    session,
    project,
    retrieval_corpus,
):
    chunks = list(
        session.scalars(
            select(DocumentChunk).where(DocumentChunk.project_id == project.id)
        )
    )
    assert len(chunks) >= 4
    repository = DocumentChunkRepository(session)
    matches = repository.search_keyword(
        project.id,
        "regulatory enforcement",
    )
    assert matches
    assert {match.chunk.source_version_id for match in matches} >= {
        retrieval_corpus["versions"]["a"].id,
        retrieval_corpus["versions"]["c"].id,
    }
