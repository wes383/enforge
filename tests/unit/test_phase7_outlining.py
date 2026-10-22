import json
from types import SimpleNamespace
from uuid import uuid4

from dr_core.db.enums import EvidenceType, ProjectLanguage, ResearchDepth
from dr_core.research.generation import build_outline
from dr_core.research.models import (
    EvidenceMatrix,
    EvidenceRow,
    ResearchBrief,
    ResearchGap,
    ResearchPlan,
    ResearchQuestion,
)
from dr_core.research.outlining import OUTLINE_TASK, build_outline_with_llm

QUESTION_A = "Does enforcement improve compliance in audited firms?"
QUESTION_B = "What do small firms report about audit costs?"
LINE = "The article weighs measured compliance gains against the cost they impose."

TITLE_A = "Enforcement outcomes"
PURPOSE_A = "What audits change where enforcement is actually measured."
TITLE_B = "Audit overhead"
PURPOSE_B = "Why the same audits cost smaller firms more."


def _brief(**overrides) -> ResearchBrief:
    values = {
        "project_id": uuid4(),
        "run_id": uuid4(),
        "title": "Enforcement research",
        "topic": "Does enforcement improve compliance?",
        "language": ProjectLanguage.EN,
        "research_depth": ResearchDepth.STANDARD,
    }
    values.update(overrides)
    return ResearchBrief(**values)


def _evidence(
    question_id,
    source_id,
    *,
    evidence_type=EvidenceType.DIRECT_SUPPORT,
    claim_summary="Enforcement improves compliance in audited firms.",
    quoted_text="Enforcement improves compliance among audited firms.",
):
    return SimpleNamespace(
        id=uuid4(),
        research_question_id=question_id,
        source_id=source_id,
        source_version_id=uuid4(),
        evidence_type=evidence_type,
        claim_summary=claim_summary,
        quoted_text=quoted_text,
        context_before="The study reports the following result.",
        context_after="The authors discuss limitations next.",
        locator={"kind": "source_text"},
    )


def _fixture():
    question_a = uuid4()
    question_b = uuid4()
    support_a = _evidence(question_a, uuid4())
    refute_a = _evidence(
        question_a,
        uuid4(),
        evidence_type=EvidenceType.REFUTATION,
        claim_summary="Audits do not change compliance in small firms.",
        quoted_text="Audits do not change compliance in small firms.",
    )
    support_b = _evidence(
        question_b,
        uuid4(),
        claim_summary="Small firms report higher audit overhead.",
        quoted_text="Small firms report higher audit overhead after inspections.",
    )
    plan = ResearchPlan(
        summary="Plan",
        questions=[
            ResearchQuestion(question_id=question_a, question=QUESTION_A),
            ResearchQuestion(question_id=question_b, question=QUESTION_B),
        ],
    )
    matrix = EvidenceMatrix(
        rows=[
            EvidenceRow(
                question_id=question_a,
                question=QUESTION_A,
                support_count=1,
                refute_count=1,
                independent_source_count=2,
                conclusion="Evidence is mixed; the conclusion is provisional.",
                support_evidence_ids=[support_a.id],
                refute_evidence_ids=[refute_a.id],
            ),
            EvidenceRow(
                question_id=question_b,
                question=QUESTION_B,
                support_count=1,
                refute_count=0,
                independent_source_count=1,
                conclusion="Costs rise for smaller audited firms.",
                support_evidence_ids=[support_b.id],
                refute_evidence_ids=[],
            ),
        ],
        total_evidence=3,
        source_count=3,
        independent_source_count=3,
        contradiction_count=1,
        coverage_ratio=1.0,
    )
    gaps = [ResearchGap(question="Open question", reason="Missing data")]
    return {
        "plan": plan,
        "matrix": matrix,
        "gaps": gaps,
        "evidence": {question_a: [support_a, refute_a], question_b: [support_b]},
        "a": str(question_a),
        "b": str(question_b),
        "support_a": support_a,
        "refute_a": refute_a,
        "support_b": support_b,
    }


class _FakeLLM:
    name = "fake-llm"

    def __init__(self, payload, *, raises: bool = False):
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.raises = raises
        self.prompts: list[str] = []

    def generate(self, prompt, *, system=None, max_tokens=None, temperature=0.0):
        self.prompts.append(prompt)
        if self.raises:
            raise RuntimeError("model timed out")
        return SimpleNamespace(text=self.text)


def _entry(fixture, key: str, title: str, purpose: str, **overrides) -> dict:
    entry = {
        "question_id": fixture[key],
        "title": title,
        "purpose": purpose,
    }
    entry.update(overrides)
    return entry


def _payload(fixture, *entries: dict) -> dict:
    return {"line": LINE, "sections": list(entries)}


def _outline(*entries: dict, feedback=None, llm=None, fixture=None, brief=None):
    fixture = fixture or _fixture()
    model = llm or _FakeLLM(_payload(fixture, *entries))
    outline = build_outline_with_llm(
        brief or _brief(),
        fixture["plan"],
        fixture["matrix"],
        fixture["gaps"],
        version=1,
        evidence_by_question=fixture["evidence"],
        llm=model,
        feedback=feedback,
    )
    return outline, fixture, model


def _body_entries(fixture) -> tuple[dict, dict]:
    return (
        _entry(fixture, "a", TITLE_A, PURPOSE_A, key_claims=[
            "Enforcement improves compliance in audited firms."
        ], target_words=500),
        _entry(fixture, "b", TITLE_B, PURPOSE_B, key_claims=[
            "Small firms report higher audit overhead."
        ], target_words=400),
    )


def test_llm_outline_reorders_bodies_and_keeps_the_frame():
    fixture = _fixture()
    first, second = _body_entries(fixture)
    outline, _, _ = _outline(second, first, fixture=fixture)
    assert outline is not None
    assert [section.title for section in outline.sections] == [
        "Introduction",
        TITLE_B,
        TITLE_A,
        "Contradictions and Counter-Evidence",
        "Limitations and Open Questions",
        "Conclusion",
    ]
    assert [section.index for section in outline.sections] == list(
        range(len(outline.sections))
    )
    by_title = {section.title: section for section in outline.sections}
    assert str(by_title[TITLE_B].question_id) == fixture["b"]
    assert by_title[TITLE_B].support_evidence_ids == [fixture["support_b"].id]
    assert by_title[TITLE_A].evidence_ids == [
        fixture["support_a"].id,
        fixture["refute_a"].id,
    ]
    assert by_title[TITLE_A].target_words == 500
    assert by_title[TITLE_A].key_claims == [
        "Enforcement improves compliance in audited firms."
    ]
    assert outline.summary.startswith("Organize the report around 2 research questions")
    assert LINE in outline.summary


def test_llm_outline_keeps_the_feedback_section_before_the_conclusion():
    fixture = _fixture()
    first, second = _body_entries(fixture)
    outline, _, _ = _outline(
        second, first, feedback="Cover the cost side earlier.", fixture=fixture
    )
    assert outline is not None
    assert [section.title for section in outline.sections][-3:] == [
        "Limitations and Open Questions",
        "Approval Feedback",
        "Conclusion",
    ]


def test_llm_outline_rejects_incomplete_or_invented_coverage():
    fixture = _fixture()
    first, second = _body_entries(fixture)
    outline, _, _ = _outline(first, fixture=fixture)
    assert outline is None

    outline, _, _ = _outline(
        first,
        {**second, "question_id": str(uuid4())},
        fixture=fixture,
    )
    assert outline is None


def test_llm_outline_rejects_question_shaped_and_duplicate_titles():
    fixture = _fixture()
    first, second = _body_entries(fixture)
    outline, _, _ = _outline(
        {**first, "title": "Does enforcement work?"},
        second,
        fixture=fixture,
    )
    assert outline is None

    outline, _, _ = _outline(first, {**second, "title": TITLE_A}, fixture=fixture)
    assert outline is None


def test_llm_outline_rejects_titles_in_the_wrong_language():
    fixture = _fixture()
    first, second = _body_entries(fixture)
    outline, _, _ = _outline({**first, "title": "执行与合规"}, second, fixture=fixture)
    assert outline is None


def test_llm_outline_accepts_chinese_titles_for_a_chinese_project():
    fixture = _fixture()
    entry_a = _entry(
        fixture,
        "a",
        "执法与合规效果",
        "汇总有测量支持的合规变化，并说明它在什么范围内成立。",
        key_claims=["审计提升合规率。"],
        target_words=500,
    )
    entry_b = _entry(
        fixture,
        "b",
        "中小机构的审计成本",
        "说明同样的审计为何在小型机构带来更高负担。",
        key_claims=["小型机构反映审计开销更高。"],
        target_words=400,
    )
    outline, _, _ = _outline(
        entry_a,
        entry_b,
        brief=_brief(language=ProjectLanguage.ZH),
        fixture=fixture,
    )
    assert outline is not None
    assert [section.title for section in outline.sections] == [
        "引言",
        "执法与合规效果",
        "中小机构的审计成本",
        "矛盾与反方证据",
        "研究局限与未解决问题",
        "结论",
    ]


def test_llm_outline_drops_claims_with_unsupported_numbers():
    fixture = _fixture()
    first, second = _body_entries(fixture)
    outline, _, _ = _outline(
        {
            **first,
            "key_claims": [
                "Audits raise compliance by 42%.",
                "Enforcement raises compliance where it is measured.",
            ],
        },
        second,
        fixture=fixture,
    )
    assert outline is not None
    assert outline.sections[1].key_claims == [
        "Enforcement raises compliance where it is measured."
    ]


def test_llm_outline_ignores_out_of_range_word_budgets():
    fixture = _fixture()
    template = build_outline(
        _brief(),
        fixture["plan"],
        fixture["matrix"],
        fixture["gaps"],
        version=1,
        evidence_by_question=fixture["evidence"],
    )
    expected = {
        str(section.question_id): section.target_words
        for section in template.sections
        if section.question_id is not None
    }
    first, second = _body_entries(fixture)
    outline, _, _ = _outline(
        {**first, "target_words": 99999},
        {**second, "target_words": "many"},
        fixture=fixture,
    )
    assert outline is not None
    for section in outline.sections:
        if section.question_id is not None:
            assert section.target_words == expected[str(section.question_id)]


def test_llm_outline_falls_back_when_the_model_fails_or_answers_junk():
    fixture = _fixture()
    outline, _, model = _outline(
        *_body_entries(fixture), llm=_FakeLLM({}, raises=True), fixture=fixture
    )
    assert outline is None
    assert model.prompts

    outline, _, _ = _outline(*_body_entries(fixture), llm=_FakeLLM({"sections": "no"}), fixture=fixture)
    assert outline is None


def test_llm_outline_prompt_never_asks_for_evidence_or_sources():
    fixture = _fixture()
    _, _, model = _outline(*_body_entries(fixture), fixture=fixture)
    prompt = json.loads(model.prompts[0])
    rules = " ".join(prompt["rules"])
    assert prompt["task"] == OUTLINE_TASK
    assert "topic-phrase title" in rules
    assert "Every supplied question id must appear exactly once" in rules
    assert "introduction, the contradictions section" in rules
    assert "UNTRUSTED_SOURCE_DATA" in rules
    assert set(prompt["sections"][0]) == {
        "question_id",
        "question",
        "evidence",
        "support_count",
        "refute_count",
        "target_words",
    }
    assert '<UNTRUSTED_SOURCE_DATA label="claim_summary">' in (
        prompt["sections"][0]["evidence"][0]
    )
    dumped = json.dumps(prompt)
    assert "evidence_ids" not in dumped
    for item in (fixture["support_a"], fixture["refute_a"], fixture["support_b"]):
        assert str(item.id) not in dumped
