import json
from types import SimpleNamespace
from uuid import uuid4

from dr_core.db.enums import (
    ArticleType,
    ProjectLanguage,
    ResearchDepth,
    SectionRole,
)
from dr_core.research.article_types import article_type_guide, article_type_prompt
from dr_core.research.generation import (
    build_outline_template,
    target_section_range,
)
from dr_core.research.models import ResearchBrief
from dr_core.research.planning import (
    OUTLINE_PLANNING_TASK,
    build_outline_with_llm,
    outline_from_payload,
)

SUMMARY = (
    "Organize the article around enforcement evidence, its costs and the limits "
    "of what current studies can settle."
)


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


def _section(title: str, **overrides) -> dict:
    entry = {
        "title": title,
        "purpose": f"Develop the {title.lower()} dimension and hand it to the next section.",
        "search_terms": [f"enforcement compliance {title.lower()}"],
    }
    entry.update(overrides)
    return entry


def _payload(*sections: dict, summary: str = SUMMARY) -> dict:
    return {"summary": summary, "sections": list(sections)}


def _full_payload(**overrides) -> dict:
    sections = [
        _section("Introduction", role="introduction"),
        _section("Enforcement effects", role="body"),
        _section("Compliance costs", role="body"),
        {
            "title": "Limitations and Applicability",
            "purpose": "State what the evidence cannot settle.",
        },
        _section("Conclusion", role="conclusion"),
    ]
    return _payload(*sections, **overrides)


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


def test_outline_payload_becomes_contiguous_typed_sections():
    outline = outline_from_payload(_full_payload(), _brief(), version=2)

    assert outline is not None
    assert outline.version == 2
    assert outline.title == "Enforcement research"
    assert outline.summary == SUMMARY
    assert [section.index for section in outline.sections] == list(range(4))
    assert [section.role for section in outline.sections] == [
        SectionRole.INTRODUCTION,
        SectionRole.BODY,
        SectionRole.BODY,
        SectionRole.CONCLUSION,
    ]
    # A limitations section proposed by the model is dropped: it used to be
    # written from a template and read as a disclaimer.
    assert SectionRole.LIMITATIONS not in {
        section.role for section in outline.sections
    }
    assert all(50 <= section.target_words <= 5000 for section in outline.sections)
    assert all(section.section_id for section in outline.sections)


def test_outline_payload_drops_question_titles_and_duplicates():
    payload = _payload(
        _section("Introduction", role="introduction"),
        _section("Enforcement effects"),
        _section("Enforcement effects"),
        _section("Does enforcement improve compliance?"),
        _section("Compliance costs", role="body"),
    )

    outline = outline_from_payload(payload, _brief(), version=1)

    assert outline is not None
    assert [section.title for section in outline.sections] == [
        "Introduction",
        "Enforcement effects",
        "Compliance costs",
    ]

    only_question = _payload(_section("Does enforcement work?"))
    assert outline_from_payload(only_question, _brief(), version=1) is None


def test_outline_payload_keeps_one_introduction_and_conclusion():
    payload = _payload(
        _section("Introduction", role="introduction"),
        _section("Enforcement effects"),
        _section("Scope of enforcement", role="introduction"),
        _section("Conclusion", role="conclusion"),
        _section("Closing observations", role="conclusion"),
    )

    outline = outline_from_payload(payload, _brief(), version=1)

    assert outline is not None
    assert [section.role for section in outline.sections] == [
        SectionRole.INTRODUCTION,
        SectionRole.BODY,
        SectionRole.BODY,
        SectionRole.CONCLUSION,
        SectionRole.BODY,
    ]


def test_outline_payload_rejects_off_topic_and_wrong_language_outlines():
    off_topic = _payload(
        _section("Gardening basics", search_terms=["gardening basics"]),
        _section("Soil chemistry", search_terms=["soil chemistry"]),
    )
    assert outline_from_payload(off_topic, _brief(), version=1) is None

    chinese = _payload(
        _section("执法与合规效果", search_terms=["执法 合规 审计"]),
        _section("审计成本", search_terms=["审计 成本"]),
    )
    assert outline_from_payload(chinese, _brief(), version=1) is None

    chinese_brief = _brief(language=ProjectLanguage.ZH, topic="执法能否提升合规？")
    chinese_summary = "围绕执法与合规的关系组织全文，并说明证据能够支持的范围。"
    assert (
        outline_from_payload(
            {
                "summary": chinese_summary,
                "sections": [
                    {
                        "title": "执法与合规效果",
                        "purpose": "汇总有测量支持的合规变化。",
                        "search_terms": ["执法 合规 审计"],
                    }
                ],
            },
            chinese_brief,
            version=1,
        )
        is not None
    )


def test_outline_payload_rejects_missing_summary_or_sections():
    assert outline_from_payload({"sections": []}, _brief(), version=1) is None
    assert (
        outline_from_payload(
            {"summary": "Short", "sections": [_section("Enforcement effects")]},
            _brief(),
            version=1,
        )
        is None
    )
    assert outline_from_payload(_payload(), _brief(), version=1) is None


def test_outline_llm_prompt_carries_article_type_and_avoids_questions():
    model = _FakeLLM(_full_payload())

    outline = build_outline_with_llm(
        _brief(article_type=ArticleType.MECHANISM_ANALYSIS, target_words=6000),
        model,
        version=3,
        feedback="Spend more space on competing explanations.",
    )

    assert outline is not None
    assert outline.version == 3
    prompt = json.loads(model.prompts[0])
    assert prompt["task"] == OUTLINE_PLANNING_TASK
    assert prompt["language"] == "en"
    assert prompt["brief"]["article_type"]["id"] == ArticleType.MECHANISM_ANALYSIS.value
    assert prompt["brief"]["article_type"]["outline_focus"]
    assert prompt["brief"]["article_type"]["research_focus"]
    assert prompt["brief"]["article_type"]["review_focus"]
    assert "competing explanations" in prompt["approver_feedback"]
    rules = " ".join(prompt["rules"])
    assert "short topic phrase" in rules
    assert "number of sections follows the word budget" in rules
    assert "one distinct analytical job" in rules
    assert "No two sections may share the same job" in rules
    assert "Order the sections as a progression" in rules
    assert "Avoid generic purposes" in rules
    assert "must not restate the body sections" in rules
    assert "UNTRUSTED_SOURCE_DATA" in rules
    dumped = json.dumps(prompt)
    assert "research_questions" not in dumped
    assert "evidence_ids" not in dumped


def test_outline_payload_rejects_sections_that_name_questions():
    payload = _full_payload()
    payload["sections"].insert(
        3,
        _section("Decision and Open Questions", role="body"),
    )

    outline = outline_from_payload(payload, _brief(), version=1)

    assert outline is not None
    assert all(
        "question" not in section.title.casefold()
        for section in outline.sections
    )


def test_outline_llm_failure_returns_none():
    model = _FakeLLM(_full_payload(), raises=True)
    assert (
        build_outline_with_llm(_brief(), model, version=1) is None
    )
    assert model.prompts

    junk = _FakeLLM({"summary": "x", "sections": "no"})
    assert build_outline_with_llm(_brief(), junk, version=1) is None


def test_template_outline_follows_the_article_type():
    for article_type in ArticleType:
        outline = build_outline_template(
            _brief(article_type=article_type, target_words=3000)
        )
        roles = [section.role for section in outline.sections]
        assert roles[0] is SectionRole.INTRODUCTION
        assert roles[-1] is SectionRole.CONCLUSION
        assert SectionRole.LIMITATIONS not in roles
        assert [section.index for section in outline.sections] == list(
            range(len(outline.sections))
        )
        assert all(
            section.search_terms
            for section in outline.sections
            if section.role is not SectionRole.LIMITATIONS
        )

    critique = build_outline_template(
        _brief(article_type=ArticleType.ARGUMENT_CRITIQUE)
    )
    explainer = build_outline_template(
        _brief(article_type=ArticleType.EXPLAINER)
    )
    assert SectionRole.CONTRADICTIONS in {
        section.role for section in critique.sections
    }
    assert SectionRole.CONTRADICTIONS not in {
        section.role for section in explainer.sections
    }


def test_template_outline_section_count_scales_with_word_budget():
    short = build_outline_template(
        _brief(article_type=ArticleType.EXPLAINER, target_words=1200)
    )
    long = build_outline_template(
        _brief(article_type=ArticleType.EXPLAINER, target_words=12000)
    )

    short_body = [
        section for section in short.sections if section.role is SectionRole.BODY
    ]
    long_body = [
        section for section in long.sections if section.role is SectionRole.BODY
    ]
    assert 1 <= len(short_body) <= len(long_body)
    assert len(long_body) > 2
    assert sum(section.target_words for section in long.sections) > sum(
        section.target_words for section in short.sections
    )


def test_target_section_range_expands_without_a_fixed_cap():
    compact = target_section_range(_brief(target_words=1200))
    extended = target_section_range(_brief(target_words=12000))

    assert compact[0] >= 3
    assert extended[0] > compact[0]
    assert extended[1] > extended[0]
    assert extended[1] > 10


def test_article_type_prompt_is_bilingual_and_covers_every_type():
    for article_type in ArticleType:
        guide = article_type_guide(article_type)
        assert guide.key == article_type.value
        english = article_type_prompt(article_type, ProjectLanguage.EN)
        chinese = article_type_prompt(article_type, ProjectLanguage.ZH)
        assert english["label"] == guide.label_en
        assert chinese["label"] == guide.label_zh
        assert english["intent"] == guide.intent_en
        assert chinese["intent"] == guide.intent_zh
        for key in ("outline_focus", "research_focus", "review_focus"):
            assert english[key] and chinese[key]
