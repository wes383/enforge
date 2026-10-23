from collections import namedtuple
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from dr_core.db.enums import (
    EvidenceType,
    ProjectLanguage,
    SectionCoverageStatus,
    SectionRole,
    SourceType,
    SourceVersionKind,
)
from dr_core.providers.models import (
    ParsedDocument,
    ParsedPage,
    ParsedSection,
    ParseStatus,
)
from dr_core.research.indexing import chunk_document, snippet_chunks
from dr_core.research.models import OutlineSection
from dr_core.research.nodes import _section_queries
from dr_core.research.quality import (
    assess_evidence_quality,
    assess_source_quality,
    build_evidence_matrix,
    source_identity,
)
from dr_core.research.text import (
    best_sentence_span,
    best_sentence_span_for_queries,
    context_window,
)

FakeSource = namedtuple(
    "FakeSource",
    [
        "id",
        "title",
        "doi",
        "canonical_url",
        "authors",
        "published_at",
        "publisher",
        "venue",
        "source_type",
        "source_metadata",
    ],
    defaults=[None, None, [], None, None, None, None, {}],
)


def _source(**overrides):
    values = {
        "id": uuid4(),
        "title": "A study of evidence quality",
        "doi": None,
        "canonical_url": None,
        "authors": [],
        "published_at": None,
        "publisher": None,
        "venue": None,
        "source_type": SourceType.WEB_PAGE,
        "source_metadata": {},
    }
    values.update(overrides)
    return FakeSource(**values)


def _evidence(section_id, source_id, *, evidence_type=EvidenceType.DIRECT_SUPPORT):
    return SimpleNamespace(
        id=uuid4(),
        outline_section_id=section_id,
        source_id=source_id,
        evidence_type=evidence_type,
        claim_summary="Regulatory enforcement improves compliance in audited firms.",
        quoted_text=(
            "Regulatory enforcement improves compliance among audited firms, "
            "although costs increase."
        ),
        context_before="The study reports the following result.",
        context_after="The authors discuss limitations next.",
    )


def _parsed() -> ParsedDocument:
    first = (
        "Regulatory enforcement improves compliance in audited firms. "
        "The effect is strongest when inspections are frequent. "
        "Small firms report higher administrative costs."
    )
    second = (
        "Independent audits confirm the compliance gains. "
        "Auditors recommend publishing enforcement statistics."
    )
    return ParsedDocument(
        status=ParseStatus.SUCCESS,
        text=f"{first}\n\n{second}",
        sections=[
            ParsedSection(
                heading="Results",
                level=2,
                paragraphs=[first, second[:40]],
            ),
            ParsedSection(
                heading="Discussion",
                level=2,
                paragraphs=[second],
            ),
        ],
        paragraph_count=3,
        character_count=len(f"{first}\n\n{second}"),
        parser_name="test",
        parser_version="test-1",
    )


def test_chunk_document_preserves_section_and_paragraph_locators():
    chunks = chunk_document(
        _parsed(),
        mime_type="text/markdown",
        max_chars=120,
    )

    assert chunks
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(len(chunk.text) <= 120 for chunk in chunks)
    assert {chunk.section_title for chunk in chunks} == {"Results", "Discussion"}
    assert all(chunk.paragraph_start is not None for chunk in chunks)
    assert all(chunk.paragraph_end >= chunk.paragraph_start for chunk in chunks)


def test_chunk_document_keeps_pdf_page_numbers():
    pages = [
        ParsedPage(page_number=1, text="Page one discusses methods and samples."),
        ParsedPage(page_number=2, text="Page two reports the findings in detail."),
    ]
    parsed = ParsedDocument(
        status=ParseStatus.SUCCESS,
        text="Page one discusses methods and samples.\n\nPage two reports the findings in detail.",
        pages=pages,
        page_count=2,
        character_count=len(
            "Page one discusses methods and samples.\n\n"
            "Page two reports the findings in detail."
        ),
        parser_name="test",
        parser_version="test-1",
    )

    chunks = chunk_document(parsed, mime_type="application/pdf", max_chars=500)

    assert [chunk.page_start for chunk in chunks] == [1, 2]
    assert [chunk.page_end for chunk in chunks] == [1, 2]
    assert all(chunk.section_title is None for chunk in chunks)


def test_snippet_chunks_split_long_snippets():
    text = "A sentence about evidence. " * 40
    chunks = snippet_chunks(text, max_chars=200)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 200 for chunk in chunks)


def test_sentence_span_and_context_are_traceable():
    text = (
        "Regulatory enforcement improves compliance in audited firms. "
        "Costs increase for small firms. "
        "Independent audits confirm the gains."
    )
    matches = best_sentence_span(text, "enforcement compliance", limit=1)
    assert matches
    sentence, _, start, end = matches[0]
    assert text[start:end].strip() == sentence
    before, after = context_window(text, start, end, window=30)
    assert before == ""
    assert "Costs increase" in after


def test_sentence_matching_uses_search_terms_for_english_sources():
    section = OutlineSection(
        index=0,
        role=SectionRole.BODY,
        title="青少年社交媒体使用与心理健康",
        purpose="解释这一关系及其边界。",
        search_terms=["adolescent social media mental health"],
    )
    text = (
        "Research shows that social media use is associated with poorer "
        "adolescent mental health, with stronger adverse consequences for girls."
    )

    assert best_sentence_span(text, section.title, limit=1) == []

    queries = _section_queries(section)
    assert queries == [
        "adolescent social media mental health",
        section.title,
    ]
    match = best_sentence_span_for_queries(text, queries)
    assert match is not None
    assert match[0].startswith("Research shows that social media use")


def test_source_identity_groups_doi_and_republished_titles():
    doi_source = _source(doi="10.1234/STUDY", title="Original title")
    republished = _source(title="Original title", published_at=date(2024, 1, 1))
    same_title = _source(
        title="Original   title!",
        published_at=date(2024, 1, 1),
        canonical_url="https://news.example.com/story",
    )
    other = _source(title="A different study", published_at=date(2024, 1, 1))

    assert source_identity(doi_source) == "doi:10.1234/study"
    assert source_identity(republished) == source_identity(same_title)
    assert source_identity(other) != source_identity(republished)


def test_source_quality_keeps_reasons_and_flags_retractions():
    strong = _source(
        title="Peer reviewed study",
        doi="10.1234/study",
        authors=["Ada Lovelace"],
        publisher="Example Press",
        venue="Journal of Tests",
        published_at=date(2024, 5, 1),
        source_type=SourceType.JOURNAL_ARTICLE,
        source_metadata={"methodology": "randomized audit experiment"},
    )
    weak = _source(title="Anonymous web summary")
    retracted = _source(
        title="Retracted paper",
        doi="10.1234/retracted",
        authors=["A. Author"],
        published_at=date(2018, 1, 1),
        source_type=SourceType.JOURNAL_ARTICLE,
        source_metadata={"retracted": True},
    )

    strong_quality = assess_source_quality(strong, now_year=2026)
    weak_quality = assess_source_quality(weak, now_year=2026)
    retracted_quality = assess_source_quality(retracted, now_year=2026)

    assert strong_quality.level == "high"
    assert {reason.dimension for reason in strong_quality.reasons} >= {
        "primary_source",
        "attribution",
        "recency",
        "method_transparency",
        "corrections",
    }
    assert weak_quality.level == "low"
    assert any(reason.status == "concern" for reason in weak_quality.reasons)
    assert retracted_quality.level == "low"
    assert any(
        reason.dimension == "corrections" and reason.status == "concern"
        for reason in retracted_quality.reasons
    )


def test_evidence_quality_explains_independence_and_context():
    evidence = _evidence(uuid4(), uuid4())
    quality = assess_evidence_quality(
        evidence,
        source=None,
        counter_evidence_present=True,
        identity_shared=True,
    )

    assert quality.level == "moderate"
    assert quality.directness == "direct"
    assert quality.context_consistency == "preserved"
    assert quality.independence == "shared"
    assert quality.counter_evidence == "present"
    assert any(reason.status == "concern" for reason in quality.reasons)


def test_evidence_matrix_keeps_counter_evidence_and_duplicate_sources():
    section_covered = OutlineSection(
        index=0,
        title="Does enforcement improve compliance?",
        purpose="Assess the compliance effect.",
    )
    section_mixed = OutlineSection(
        index=1,
        title="What are the costs of enforcement?",
        purpose="Assess the costs.",
    )
    section_empty = OutlineSection(
        index=2,
        title="How should enforcement be evaluated?",
        purpose="Discuss evaluation.",
    )
    source_a = _source(
        title="Primary study",
        doi="10.1234/study",
        authors=["Ada Lovelace"],
        published_at=date(2024, 1, 1),
        source_type=SourceType.JOURNAL_ARTICLE,
    )
    source_b = _source(
        title="News report of the same study",
        doi="10.1234/study",
        published_at=date(2024, 1, 1),
    )
    source_c = _source(
        title="Industry cost report",
        authors=["Industry Group"],
        published_at=date(2023, 1, 1),
        source_type=SourceType.REPORT,
    )
    support_a = _evidence(section_covered.section_id, source_a.id)
    support_b = _evidence(section_covered.section_id, source_b.id)
    mixed_support = _evidence(section_mixed.section_id, source_a.id)
    mixed_refute = _evidence(
        section_mixed.section_id,
        source_c.id,
        evidence_type=EvidenceType.REFUTATION,
    )

    matrix = build_evidence_matrix(
        [section_covered, section_mixed, section_empty],
        [support_a, support_b, mixed_support, mixed_refute],
        {source.id: source for source in (source_a, source_b, source_c)},
        language=ProjectLanguage.EN,
        now_year=2026,
    )

    covered_row = matrix.rows[0]
    mixed_row = matrix.rows[1]
    empty_row = matrix.rows[2]

    assert covered_row.support_count == 2
    assert covered_row.independent_source_count == 1
    assert covered_row.coverage_status is SectionCoverageStatus.PARTIAL
    assert covered_row.conclusion
    assert matrix.duplicate_source_groups
    assert any(
        quality.independence == "shared"
        for quality in covered_row.evidence_quality
    )
    assert any(
        limitation for limitation in covered_row.limitations if "independent" in limitation
    )

    assert mixed_row.refute_count == 1
    assert mixed_row.refute_evidence_ids == [mixed_refute.id]
    assert "mixed" in mixed_row.conclusion.lower()
    assert matrix.contradiction_count == 1
    assert any(
        quality.counter_evidence == "present"
        for quality in mixed_row.evidence_quality
    )

    assert empty_row.coverage_status is SectionCoverageStatus.INSUFFICIENT
    assert empty_row.gap
    assert matrix.coverage_ratio == 0.3333
    assert matrix.independent_source_count == 2
    assert matrix.source_count == 3


def test_snippet_chunk_kind_flows_from_content_kind():
    from dr_core.research.indexing import chunk_locator

    chunk = SimpleNamespace(
        id=uuid4(),
        source_version_id=uuid4(),
        chunk_index=0,
        section_title="Search snippet",
        page_start=None,
        page_end=None,
        paragraph_start=None,
        paragraph_end=None,
    )
    locator = chunk_locator(
        chunk,
        mime_type=None,
        content_kind=SourceVersionKind.SEARCH_SNIPPET,
    )
    assert locator["kind"] == "search_snippet"
    assert locator["chunk_id"] == str(chunk.id)
