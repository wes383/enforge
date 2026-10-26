import json
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from dr_core.db.enums import (
    ClaimType,
    ClaimVerificationStatus,
    EvidenceRelation,
    EvidenceType,
    ProjectLanguage,
    ResearchDepth,
    SectionRole,
    SourceType,
)
from dr_core.research.claims import (
    ClaimCandidate,
    audit_references,
    extract_claims,
    merged_status,
    review_claim_with_llm,
    verify_claim,
)
from dr_core.research.models import (
    EvidenceMatrix,
    EvidenceRow,
    OutlineSection,
    ResearchBrief,
)
from dr_core.research.writing import (
    WriteSearchOutcome,
    compose_section,
    numeric_tokens,
    summarize_section_content,
)
from dr_core.research.text import is_low_value_sentence, sentence_spans


def _brief(**overrides) -> ResearchBrief:
    values = {
        "project_id": uuid4(),
        "run_id": uuid4(),
        "title": "Research",
        "topic": "AI regulation",
        "language": ProjectLanguage.EN,
        "research_depth": ResearchDepth.STANDARD,
    }
    values.update(overrides)
    return ResearchBrief(**values)


def _source(*, title="Regulatory study", **overrides):
    values = {
        "id": uuid4(),
        "title": title,
        "authors": ["Ada Lovelace"],
        "published_at": date(2024, 5, 1),
        "venue": "Journal of Tests",
        "publisher": "Example Press",
        "doi": "10.1234/study",
        "canonical_url": "https://example.org/study",
        "source_type": SourceType.JOURNAL_ARTICLE,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _evidence(
    section_id,
    source_id,
    *,
    evidence_type=EvidenceType.DIRECT_SUPPORT,
    claim_summary="Enforcement improves compliance in audited firms.",
    quoted_text=(
        "Enforcement improves compliance among audited firms, although costs increase."
    ),
    locator=None,
):
    return SimpleNamespace(
        id=uuid4(),
        outline_section_id=section_id,
        source_id=source_id,
        source_version_id=uuid4(),
        evidence_type=evidence_type,
        claim_summary=claim_summary,
        quoted_text=quoted_text,
        context_before="The study reports the following result.",
        context_after="The authors discuss limitations next.",
        locator=locator if locator is not None else {"kind": "source_text"},
    )


def _section(
    section_id,
    *,
    index=1,
    title="Enforcement outcomes",
    role=SectionRole.BODY,
    key_claims=None,
    evidence=(),
    matrix=None,
    llm=None,
    brief=None,
    numbering=None,
    supplement=None,
    max_search_requests=0,
    prior_sections=None,
    limitations=(),
    gaps=(),
):
    support, refute = [], []
    for item in evidence:
        (refute if item.evidence_type is EvidenceType.REFUTATION else support).append(
            item
        )
    if matrix is None:
        matrix = EvidenceMatrix(
            rows=[
                EvidenceRow(
                    section_id=section_id,
                    section_title=title,
                    support_count=len(support),
                    refute_count=len(refute),
                    independent_source_count=2,
                    conclusion="Evidence is mixed; the conclusion is provisional.",
                    support_evidence_ids=[item.id for item in support],
                    refute_evidence_ids=[item.id for item in refute],
                )
            ],
            total_evidence=len(evidence),
            source_count=2,
            independent_source_count=2,
            coverage_ratio=0.5,
        )
    sources = {}
    for item in evidence:
        sources.setdefault(
            item.source_id,
            _source(title=f"Source {len(sources) + 1}"),
        )
    if numbering is None:
        numbering = {
            source_id: position + 1
            for position, source_id in enumerate(sources)
        }
    draft_section = OutlineSection(
        index=index,
        title=title,
        role=role,
        purpose="Synthesize the evidence.",
        section_id=section_id,
        key_claims=key_claims or [],
        target_words=400,
        evidence_ids=[item.id for item in evidence],
    )
    draft = compose_section(
        section=draft_section,
        brief=brief or _brief(),
        matrix=matrix,
        gaps=list(gaps),
        limitations=list(limitations),
        evidence=list(evidence),
        sources=sources,
        numbering=numbering,
        llm=llm,
        supplement=supplement,
        max_search_requests=max_search_requests,
        prior_sections=prior_sections,
    )
    return draft, sources, numbering, matrix


class _FakeLLM:
    name = "fake-llm"

    def __init__(self, payload):
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def generate(self, prompt, *, system=None, max_tokens=None, temperature=0.0):
        return SimpleNamespace(text=self.text)


class _SequenceLLM:
    name = "fake-llm"

    def __init__(self, payloads):
        self.payloads = [
            payload if isinstance(payload, str) else json.dumps(payload)
            for payload in payloads
        ]
        self.prompts: list[str] = []

    def generate(self, prompt, *, system=None, max_tokens=None, temperature=0.0):
        self.prompts.append(prompt)
        return SimpleNamespace(text=self.payloads.pop(0))


def test_deterministic_section_writes_narrative_without_report_scaffolding():
    section_id = uuid4()
    source_a = _source(title="Support study")
    source_b = _source(title="Refuting study")
    support = _evidence(section_id, source_a.id)
    refute = _evidence(
        section_id,
        source_b.id,
        evidence_type=EvidenceType.REFUTATION,
        claim_summary="Audits do not improve compliance in small firms.",
        quoted_text="Audits do not improve compliance in small firms.",
    )
    draft, sources, numbering, _ = _section(
        section_id,
        title="Enforcement outcomes",
        key_claims=[support.claim_summary],
        evidence=[support, refute],
    )

    assert draft.content.startswith("## Enforcement outcomes")
    assert "This section addresses:" not in draft.content
    assert "Purpose:" not in draft.content
    assert "Key findings include:" not in draft.content
    assert "The next section discusses" not in draft.content
    assert support.claim_summary[:40] in draft.content
    assert "Counter-evidence" not in draft.content
    assert "The judgment is not uncontested, however." in draft.content
    assert not draft.used_llm
    assert set(draft.citation_map) == {
        f"S{numbering[source_a.id]}",
        f"S{numbering[source_b.id]}",
    }
    assert set(draft.citation_map.values()) == {str(source_a.id), str(source_b.id)}
    assert set(draft.evidence_ids) == {support.id, refute.id}
    for marker in draft.citation_map:
        assert f"[{marker}]" in draft.content


def test_deterministic_intro_frames_the_article_without_report_statistics():
    section_id = uuid4()
    source_a = _source(title="First study")
    source_b = _source(title="Second study")
    first = _evidence(section_id, source_a.id)
    second = _evidence(
        section_id,
        source_b.id,
        claim_summary="Small firms face higher compliance costs.",
        quoted_text="Small firms face higher compliance costs after audits.",
    )
    draft, _, numbering, _ = _section(
        section_id,
        index=0,
        title="Introduction",
        role=SectionRole.INTRODUCTION,
        evidence=[first, second],
    )

    assert draft.content.startswith("## Introduction")
    assert "This report examines" not in draft.content
    assert "Key findings include:" not in draft.content
    assert "The next section discusses" not in draft.content
    assert "- " not in draft.content
    assert first.claim_summary[:40] in draft.content
    assert f"[S{numbering[source_a.id]}]" in draft.content
    assert f"[S{numbering[source_b.id]}]" in draft.content


def test_deterministic_conclusion_deduplicates_repeated_row_conclusions():
    section_id = uuid4()
    items = [
        _evidence(
            section_id,
            uuid4(),
            claim_summary=f"Finding {index} about enforcement effects.",
            quoted_text=f"Finding {index} about enforcement effects across firms.",
        )
        for index in range(3)
    ]
    repeated = "3 independent sources support a provisional conclusion."
    matrix = EvidenceMatrix(
        rows=[
            EvidenceRow(
                section_id=section_id,
                section_title=f"Section {index}",
                support_count=1,
                independent_source_count=1,
                conclusion=repeated,
                support_evidence_ids=[item.id],
            )
            for index, item in enumerate(items)
        ],
        total_evidence=3,
        source_count=3,
        independent_source_count=3,
        coverage_ratio=1.0,
    )
    draft, _, _, _ = _section(
        section_id,
        title="Conclusion",
        role=SectionRole.CONCLUSION,
        evidence=items,
        matrix=matrix,
    )

    assert draft.content.count("Taken together") == 1
    assert repeated not in draft.content
    for item in items:
        assert draft.content.count(item.claim_summary.rstrip(".")) == 1


def test_limitations_section_drops_process_notes_and_deduplicates():
    section_id = uuid4()
    evidence_row = EvidenceRow(
        section_id=section_id,
        section_title="Enforcement outcomes",
        support_count=1,
        independent_source_count=1,
        conclusion="Limited support.",
        limitations=[
            "Only one independent source supports this section; treat the conclusion as provisional.",
            "Only one independent source supports this section; treat the conclusion as provisional.",
            "Some sources lack a publication date, so temporal relevance cannot be judged.",
        ],
    )
    matrix = EvidenceMatrix(
        rows=[evidence_row],
        total_evidence=1,
        source_count=1,
        independent_source_count=1,
        coverage_ratio=0.5,
    )
    draft, _, _, _ = _section(
        section_id,
        title="Limitations and Applicability",
        role=SectionRole.LIMITATIONS,
        evidence=[],
        matrix=matrix,
        limitations=[
            "Reached the maximum source count (20).",
            "The model token budget was exhausted; further research stopped.",
            "6 references have incomplete metadata; warnings were recorded instead of inventing fields.",
            "Semantic retrieval is unavailable; keyword search is active.",
        ],
    )

    assert draft.content.count("Only one independent source supports") == 1
    assert "publication date" in draft.content
    for process_note in (
        "maximum source count",
        "token budget",
        "incomplete metadata",
        "Semantic retrieval",
    ):
        assert process_note not in draft.content
    assert "- " not in draft.content


def test_limitations_section_drops_pipeline_and_model_fallback_notes():
    section_id = uuid4()
    matrix = EvidenceMatrix(
        rows=[
            EvidenceRow(
                section_id=section_id,
                section_title="Enforcement outcomes",
                support_count=0,
                independent_source_count=0,
                conclusion="No direct evidence.",
                limitations=[],
            )
        ],
        total_evidence=0,
        source_count=0,
        independent_source_count=0,
        coverage_ratio=0.0,
    )
    draft, _, _, _ = _section(
        section_id,
        title="Limitations and Applicability",
        role=SectionRole.LIMITATIONS,
        evidence=[],
        matrix=matrix,
        limitations=[
            "Background reconnaissance did not complete; the outline relies on the brief only.",
            "The model outline failed validation, so the report outline came from the deterministic template.",
            "No independent source supports this section.",
            "No web content provider is configured; only search snippets are available.",
            "Full text was unavailable for “Example” (failed).",
            "前置侦察未完成，提纲仅依据项目简介生成。",
            "报告提纲模型输出未通过校验，提纲改用确定性模板生成。",
            "无独立来源支持该章节。",
            "未配置网页正文提供商，来源仅使用检索摘要。",
            "Some sources lack a publication date, so temporal relevance cannot be judged.",
        ],
    )

    assert "publication date" in draft.content
    for process_note in (
        "reconnaissance",
        "failed validation",
        "No independent source supports",
        "web content provider",
        "Full text was unavailable",
        "前置侦察",
        "未通过校验",
        "无独立来源支持",
        "网页正文提供商",
    ):
        assert process_note not in draft.content


def test_metadata_sentences_are_filtered_from_evidence():
    text = (
        "关键词：生成式人工智能；学习效果；元分析；实验研究 "
        "【中图分类号】G40-057 【文献标识码】A "
        "【论文编号】1009-8097（2025）08-0036-10。"
        "This study reports a substantive finding about student learning outcomes."
    )
    assert is_low_value_sentence(
        "关键词：生成式人工智能；学习效果；元分析；实验研究"
    )
    spans = sentence_spans(text)
    assert len(spans) == 1
    assert spans[0][0].startswith("This study reports")

    section_id = uuid4()
    metadata = _evidence(
        section_id,
        uuid4(),
        claim_summary="关键词：生成式人工智能；学习效果；元分析；实验研究",
        quoted_text="关键词：生成式人工智能；学习效果；元分析；实验研究",
    )
    substantive = _evidence(section_id, uuid4())
    draft, _, numbering, _ = _section(
        section_id,
        title="Evidence",
        evidence=[metadata, substantive],
    )

    assert "关键词" not in draft.content
    assert metadata.id not in draft.evidence_ids
    assert set(draft.citation_map) == {f"S{numbering[substantive.source_id]}"}


def test_section_without_evidence_does_not_assert_conclusions():
    draft, _, _, _ = _section(uuid4(), title="Unanswered dimension", evidence=[])

    assert "does not yet support a grounded judgment" in draft.content
    assert not draft.citation_map
    assert not draft.evidence_ids


def test_llm_section_takes_the_outline_title_over_the_model_heading():
    section_id = uuid4()
    support = _evidence(section_id, uuid4())
    llm = _FakeLLM(
        {"markdown": "## Question\n\nEnforcement improves compliance [S1]."}
    )
    draft, sources, numbering, matrix = _section(
        section_id,
        title="Enforcement outcomes",
        evidence=[support],
        llm=llm,
    )

    assert draft.used_llm
    assert draft.content.startswith("## Enforcement outcomes")
    assert "Enforcement improves compliance [S1]." in draft.content
    assert "Question" not in draft.content
    assert draft.citation_map == {"S1": str(support.source_id)}
    assert draft.evidence_ids == [support.id]


def test_llm_section_supplements_evidence_then_rewrites():
    section_id = uuid4()
    original = _evidence(section_id, uuid4())
    added = _evidence(
        section_id,
        uuid4(),
        claim_summary="A second study confirms the finding.",
        quoted_text="A second study confirms the same compliance finding.",
    )
    numbering = {original.source_id: 1}
    llm = _SequenceLLM(
        [
            {
                "needs_sources": [
                    {
                        "query": "enforcement compliance second study",
                        "kind": "academic",
                        "reason": "only one source is available",
                    }
                ]
            },
            {
                "markdown": (
                    "## Question\n\n"
                    "A second study confirms the finding [S2]."
                )
            },
        ]
    )
    captured = []

    def supplement(requests):
        captured.extend(requests)
        numbering[added.source_id] = 2
        return WriteSearchOutcome(
            evidence=[added],
            sources_added=1,
            evidence_added=1,
            searches=1,
            queries=(requests[0].query,),
        )

    draft, _, _, _ = _section(
        section_id,
        title="Enforcement outcomes",
        evidence=[original],
        llm=llm,
        numbering=numbering,
        supplement=supplement,
        max_search_requests=2,
    )

    assert captured[0].query == "enforcement compliance second study"
    assert captured[0].kind == "academic"
    assert draft.used_llm
    assert draft.citation_map == {"S2": str(added.source_id)}
    assert draft.evidence_ids == [added.id]
    first = json.loads(llm.prompts[0])
    second = json.loads(llm.prompts[1])
    assert first["search"]["available"] is True
    assert second["search"]["available"] is False
    assert second["supplemental_search"]["evidence_added"] == 1


def test_llm_section_requests_sources_when_the_section_has_no_evidence():
    section_id = uuid4()
    added = _evidence(section_id, uuid4())
    numbering = {}
    llm = _SequenceLLM(
        [
            {
                "needs_sources": [
                    {
                        "query": "enforcement audits",
                        "kind": "web",
                        "reason": "no evidence supplied",
                    }
                ]
            },
            {
                "markdown": (
                    "## Unanswered dimension\n\n"
                    "New evidence addresses the question [S1]."
                )
            },
        ]
    )

    def supplement(requests):
        numbering[added.source_id] = 1
        return WriteSearchOutcome(
            evidence=[added],
            sources_added=1,
            evidence_added=1,
            searches=1,
            queries=(requests[0].query,),
        )

    draft, _, _, _ = _section(
        section_id,
        title="Unanswered dimension",
        evidence=[],
        llm=llm,
        numbering=numbering,
        supplement=supplement,
        max_search_requests=2,
    )

    assert draft.used_llm
    assert draft.citation_map == {"S1": str(added.source_id)}
    first = json.loads(llm.prompts[0])
    assert first["evidence"] == []
    assert first["search"]["available"] is True
    assert json.loads(llm.prompts[1])["supplemental_search"]["evidence_added"] == 1


def test_llm_section_writes_with_existing_evidence_when_search_finds_nothing():
    section_id = uuid4()
    support = _evidence(section_id, uuid4())
    llm = _SequenceLLM(
        [
            {
                "needs_sources": [
                    {"query": "missing source", "kind": "web", "reason": "gap"}
                ]
            },
            {
                "markdown": (
                    "## Question\n\n"
                    "The available evidence is limited [S1]."
                )
            },
        ]
    )

    def supplement(requests):
        return WriteSearchOutcome(
            searches=1,
            queries=(requests[0].query,),
            errors=("no results",),
        )

    draft, _, _, _ = _section(
        section_id,
        title="Enforcement outcomes",
        evidence=[support],
        llm=llm,
        supplement=supplement,
        max_search_requests=2,
    )

    assert draft.used_llm
    assert draft.citation_map == {"S1": str(support.source_id)}
    second = json.loads(llm.prompts[1])
    assert second["supplemental_search"]["evidence_added"] == 0
    assert any(
        "No new evidence was found" in rule for rule in second["rules"]
    )


def test_llm_section_returns_model_summary_for_later_context():
    section_id = uuid4()
    support = _evidence(section_id, uuid4())
    llm = _FakeLLM(
        {
            "markdown": "## Question\n\nEnforcement improves compliance [S1].",
            "summary": "Enforcement improves compliance in audited firms.",
        }
    )

    draft, _, _, _ = _section(
        section_id,
        title="Enforcement outcomes",
        evidence=[support],
        llm=llm,
    )

    assert draft.used_llm
    assert draft.summary == "Enforcement improves compliance in audited firms."


def test_llm_section_summary_falls_back_to_the_opening_sentences():
    section_id = uuid4()
    support = _evidence(section_id, uuid4())
    llm = _FakeLLM(
        {"markdown": "## Question\n\nEnforcement improves compliance [S1]."}
    )

    draft, _, _, _ = _section(
        section_id,
        title="Enforcement outcomes",
        evidence=[support],
        llm=llm,
    )

    assert draft.used_llm
    assert draft.summary.startswith("Enforcement improves compliance")
    assert "[S1]" not in draft.summary


def test_llm_section_receives_prior_section_context():
    section_id = uuid4()
    support = _evidence(section_id, uuid4())
    llm = _SequenceLLM(
        [
            {
                "markdown": (
                    "## Question\n\n"
                    "Enforcement improves compliance [S1]."
                )
            }
        ]
    )

    draft, _, _, _ = _section(
        section_id,
        title="Enforcement outcomes",
        evidence=[support],
        llm=llm,
        prior_sections=[
            {
                "index": 0,
                "title": "Introduction",
                "summary": "The introduction frames the enforcement debate.",
            }
        ],
    )

    assert draft.used_llm
    prompt = json.loads(llm.prompts[0])
    assert prompt["prior_sections"][0]["index"] == 0
    assert "Introduction" in prompt["prior_sections"][0]["title"]
    assert prompt["prior_sections"][0]["summary"].startswith(
        '<UNTRUSTED_SOURCE_DATA label="prior_section_summary"'
    )
    assert any("not evidence" in rule for rule in prompt["rules"])
    assert any("Paraphrase the evidence" in rule for rule in prompt["rules"])


def test_summarize_section_content_keeps_leading_sentences_without_markers():
    summary = summarize_section_content(
        "## Overview\n\n"
        "Enforcement improves compliance among audited firms [S1]. "
        "Costs increase for small firms [S2]. "
        "A third sentence that should not appear in the summary. "
    )

    assert "Enforcement improves" in summary
    assert "Costs increase" in summary
    assert "third sentence" not in summary
    assert "[S1]" not in summary


def test_llm_section_falls_back_on_unsafe_output():
    section_id = uuid4()
    support = _evidence(section_id, uuid4())

    unknown = _FakeLLM({"markdown": "## Q\n\nClaim [S7]."})
    draft, _, _, _ = _section(
        section_id, evidence=[support], llm=unknown
    )
    assert not draft.used_llm
    assert "Claim [S7]" not in draft.content

    invented_number = _FakeLLM(
        {"markdown": "## Q\n\nEfficiency rose 87% [S1]."}
    )
    draft, _, _, _ = _section(
        section_id, evidence=[support], llm=invented_number
    )
    assert not draft.used_llm
    assert "87%" not in draft.content


def test_llm_section_drops_a_fabricated_reference_list():
    section_id = uuid4()
    support = _evidence(section_id, uuid4())
    source_id = support.source_id

    reference_list = _FakeLLM(
        {
            "markdown": (
                "## Q\n\nEnforcement works [S1].\n\n"
                "## References\n\n[S1] Fabricated."
            )
        }
    )
    draft, _, _, _ = _section(
        section_id, evidence=[support], llm=reference_list
    )

    assert draft.used_llm
    assert "Enforcement works [S1]." in draft.content
    assert "Fabricated" not in draft.content
    assert draft.citation_map == {"S1": str(source_id)}


def test_llm_section_drops_uncited_paragraphs():
    section_id = uuid4()
    support = _evidence(section_id, uuid4())
    llm = _FakeLLM(
        {"markdown": "## Q\n\nFirst claim [S1].\n\nSecond unsupported sentence."}
    )
    draft, _, _, _ = _section(section_id, evidence=[support], llm=llm)

    assert draft.used_llm
    assert "First claim [S1]." in draft.content
    assert "Second unsupported sentence." not in draft.content


def test_claim_extraction_flags_uncited_numeric_claims():
    section = SimpleNamespace(
        section_index=1,
        title="Enforcement outcomes",
        content_markdown=(
            "## Question\n\n"
            "Enforcement improves compliance in audited firms [S1]. "
            "Efficiency improved by 87 percent across all firms. "
            "This section describes the evidence base. "
            "Policymakers should publish enforcement statistics."
        ),
        citation_map={"S1": str(uuid4())},
    )
    claims = extract_claims(section)
    texts = [claim.text for claim in claims]

    assert any("Enforcement improves" in text for text in texts)
    assert any("87 percent" in text for text in texts)
    assert not any("describes the evidence base" in text for text in texts)
    numeric = next(claim for claim in claims if "87 percent" in claim.text)
    assert numeric.claim_type is ClaimType.QUANTITATIVE
    assert not numeric.markers


def test_claim_extraction_keeps_markers_after_quoted_questions():
    section = SimpleNamespace(
        section_index=0,
        title="Introduction",
        content_markdown=(
            "## Introduction\n\n"
            "Key findings include:\n"
            "- Deterministic mock snippet 3 about What key evidence is available? [S1]\n"
            "- Evidence from “Paper: What is covered?” states the result [S2].\n"
        ),
        citation_map={"S1": str(uuid4()), "S2": str(uuid4())},
    )
    claims = extract_claims(section)

    assert all(claim.text != "Key findings include:" for claim in claims)
    assert any(
        "What key evidence is available?" in claim.text and claim.markers == ("S1",)
        for claim in claims
    )
    assert any(
        "Paper: What is covered?" in claim.text and claim.markers == ("S2",)
        for claim in claims
    )


def _assessment(claim_text, *, markers, evidence=None, section_title="Enforcement outcomes"):
    section_id = uuid4()
    source = _source()
    item = _evidence(section_id, source.id)
    if evidence is not None:
        item = evidence
        source = _source(id=item.source_id)
    candidate = ClaimCandidate(
        section_index=1,
        text=claim_text,
        claim_type=ClaimType.FACTUAL,
        markers=markers,
    )
    section = SimpleNamespace(
        title=section_title,
        citation_map={"S1": str(source.id)},
        evidence_ids=[item.id],
    )
    assessment = verify_claim(
        candidate,
        section=section,
        sources_by_id={source.id: source},
        evidence_by_id={item.id: item},
        evidence_by_source={source.id: [item]},
        traceable=lambda _: True,
    )
    return assessment, item


def test_claim_verification_accepts_traceable_evidence():
    assessment, item = _assessment(
        "Enforcement improves compliance in audited firms.",
        markers=("S1",),
    )

    assert assessment.status is ClaimVerificationStatus.SUPPORTED
    assert assessment.evidence_ids == [item.id]
    assert assessment.relations[item.id] is EvidenceRelation.SUPPORTS


def test_claim_verification_downgrades_for_numeric_and_locator_mismatch():
    section_id = uuid4()
    source = _source()
    item = _evidence(
        section_id,
        source.id,
        quoted_text="Enforcement improves compliance in 40 percent of audited firms.",
    )
    candidate = ClaimCandidate(
        section_index=1,
        text="Enforcement improves compliance in 87 percent of audited firms.",
        claim_type=ClaimType.QUANTITATIVE,
        markers=("S1",),
    )
    section = SimpleNamespace(
        title="Q",
        citation_map={"S1": str(source.id)},
        evidence_ids=[item.id],
    )
    assessment = verify_claim(
        candidate,
        section=section,
        sources_by_id={source.id: source},
        evidence_by_id={item.id: item},
        evidence_by_source={source.id: [item]},
        traceable=lambda _: False,
    )

    assert assessment.status is ClaimVerificationStatus.PARTIAL
    assert any("87" in reason for reason in assessment.reasons)
    assert any("relocated" in reason for reason in assessment.reasons)


def test_claim_verification_treats_refutation_as_contradiction():
    section_id = uuid4()
    source = _source()
    refute = _evidence(
        section_id,
        source.id,
        evidence_type=EvidenceType.REFUTATION,
        claim_summary="Audits fail to improve compliance in small firms.",
        quoted_text="Audits fail to improve compliance in small firms.",
    )
    assessment, item = _assessment(
        "Enforcement improves compliance in audited firms.",
        markers=("S1",),
        evidence=refute,
        section_title="Enforcement outcomes",
    )

    assert assessment.status is ClaimVerificationStatus.CONTRADICTED
    assert assessment.relations[item.id] is EvidenceRelation.REFUTES


def test_a_negative_claim_citing_a_refutation_is_supported():
    section_id = uuid4()
    source = _source()
    refute = _evidence(
        section_id,
        source.id,
        evidence_type=EvidenceType.REFUTATION,
        claim_summary=(
            "Enforcement does not improve compliance in audited firms."
        ),
        quoted_text=(
            "Enforcement does not improve compliance in audited firms."
        ),
    )
    assessment, item = _assessment(
        "Enforcement does not improve compliance in audited firms.",
        markers=("S1",),
        evidence=refute,
        section_title="Enforcement outcomes",
    )

    assert assessment.status is ClaimVerificationStatus.SUPPORTED
    assert assessment.relations[item.id] is EvidenceRelation.SUPPORTS


def test_uncited_claims_are_unsupported_or_unverifiable():
    factual = ClaimCandidate(
        section_index=1,
        text="Enforcement improves compliance in audited firms.",
        claim_type=ClaimType.FACTUAL,
        markers=(),
    )
    normative = ClaimCandidate(
        section_index=1,
        text="Policymakers should publish enforcement statistics.",
        claim_type=ClaimType.NORMATIVE,
        markers=(),
        normative=True,
    )
    section = SimpleNamespace(title="Q", citation_map={}, evidence_ids=[])
    for candidate, expected in (
        (factual, ClaimVerificationStatus.UNSUPPORTED),
        (normative, ClaimVerificationStatus.UNVERIFIABLE),
    ):
        assessment = verify_claim(
            candidate,
            section=section,
            sources_by_id={},
            evidence_by_id={},
            evidence_by_source={},
            traceable=lambda _: True,
        )
        assert assessment.status is expected


def test_merged_status_never_upgrades_an_existing_failure():
    assert (
        merged_status(
            ClaimVerificationStatus.UNSUPPORTED,
            ClaimVerificationStatus.SUPPORTED,
        )
        is ClaimVerificationStatus.UNSUPPORTED
    )
    assert (
        merged_status(
            ClaimVerificationStatus.PARTIAL,
            ClaimVerificationStatus.SUPPORTED,
        )
        is ClaimVerificationStatus.PARTIAL
    )
    assert (
        merged_status(
            ClaimVerificationStatus.SUPPORTED,
            ClaimVerificationStatus.PARTIAL,
        )
        is ClaimVerificationStatus.PARTIAL
    )
    assert (
        merged_status(None, ClaimVerificationStatus.SUPPORTED)
        is ClaimVerificationStatus.SUPPORTED
    )
    assert (
        merged_status(
            ClaimVerificationStatus.PENDING,
            ClaimVerificationStatus.SUPPORTED,
        )
        is ClaimVerificationStatus.SUPPORTED
    )


def test_llm_claim_review_only_downgrades():
    assessment, _ = _assessment(
        "Enforcement improves compliance in audited firms.",
        markers=("S1",),
    )
    assessment.status = ClaimVerificationStatus.PARTIAL
    upgraded = review_claim_with_llm(
        _FakeLLM({"status": "SUPPORTED", "reason": "fine"}),
        assessment,
        [],
    )
    assert upgraded.status is ClaimVerificationStatus.PARTIAL

    downgraded = review_claim_with_llm(
        _FakeLLM({"status": "CONTRADICTED", "reason": "quote is conditional"}),
        assessment,
        [],
    )
    assert downgraded.status is ClaimVerificationStatus.PARTIAL


def test_reference_audit_reports_dangling_unused_and_incomplete():
    cited_source = _source(title="Cited study")
    incomplete = _source(
        title="Blog summary",
        authors=[],
        published_at=None,
        venue=None,
        publisher=None,
        doi=None,
        canonical_url=None,
        source_type=SourceType.WEB_PAGE,
    )
    unused = _source(title="Unused study")
    mapped_unused = _source(title="Mapped but unused")
    sections = [
        SimpleNamespace(
            section_index=0,
            content_markdown="Claim [S1] and incomplete [S2] and missing [S9].",
            citation_map={
                "S1": str(cited_source.id),
                "S2": str(incomplete.id),
                "S3": str(mapped_unused.id),
            },
        )
    ]
    audit = audit_references(
        sections,
        {
            cited_source.id: cited_source,
            incomplete.id: incomplete,
            unused.id: unused,
            mapped_unused.id: mapped_unused,
        },
        {
            cited_source.id,
            incomplete.id,
            unused.id,
            mapped_unused.id,
        },
    )

    assert audit.cited_source_ids == sorted(
        [cited_source.id, incomplete.id], key=str
    )
    assert audit.unused_source_ids == sorted([unused.id, mapped_unused.id], key=str)
    assert audit.invalid_metadata_source_ids == [incomplete.id]
    assert audit.dangling_markers == ["S9"]
    assert any("S3" in issue and "not used in the body" in issue for issue in audit.issues)
    assert any("incomplete" in issue for issue in audit.issues)


def test_reference_audit_flags_unnormalized_identifiers():
    source = _source(
        doi="https://doi.org/10.1234/STUDY",
        canonical_url="HTTPS://Example.org/Paper/?utm_source=newsletter",
    )
    sections = [
        SimpleNamespace(
            section_index=0,
            content_markdown="Claim [S1].",
            citation_map={"S1": str(source.id)},
        )
    ]
    audit = audit_references(sections, {source.id: source}, {source.id})

    assert audit.invalid_metadata_source_ids == [source.id]
    assert any("DOI" in issue and "not normalized" in issue for issue in audit.issues)
    assert any("URL" in issue and "not normalized" in issue for issue in audit.issues)
