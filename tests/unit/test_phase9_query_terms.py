from __future__ import annotations

from uuid import uuid4

from dr_core.db.enums import (
    ProjectLanguage,
    ResearchDepth,
    SectionRole,
    SearchProviderName,
)
from dr_core.research.generation import build_queries, query_pair
from dr_core.research.models import Outline, OutlineSection, ResearchBrief
from dr_core.research.nodes import _counter_queries, _gap_queries


def _brief(**overrides) -> ResearchBrief:
    values = {
        "project_id": uuid4(),
        "run_id": uuid4(),
        "title": "同行评审",
        "topic": "同行评审如何影响研究可靠性",
        "language": ProjectLanguage.ZH,
        "research_depth": ResearchDepth.STANDARD,
    }
    values.update(overrides)
    return ResearchBrief(**values)


def _outline(*sections: OutlineSection) -> Outline:
    return Outline(
        title="同行评审",
        summary="围绕同行评审组织全文。",
        sections=list(sections),
    )


def _queries(outline: Outline, *, limit: int = 10):
    return build_queries(
        outline,
        round_index=2,
        web_providers=[SearchProviderName.TAVILY],
        academic_providers=[SearchProviderName.CROSSREF],
        limit=limit,
    )


def test_web_queries_use_the_chinese_term_and_academic_queries_the_english_one():
    body = OutlineSection(
        index=1,
        role=SectionRole.BODY,
        title="错误率的影响",
        purpose="说明同行评审对错误率的影响。",
        search_terms=["同行评审 错误率", "peer review error rates"],
    )
    outline = _outline(
        OutlineSection(
            index=0,
            role=SectionRole.INTRODUCTION,
            title="引言",
            purpose="引出主题。",
            search_terms=["同行评审 背景"],
        ),
        body,
        OutlineSection(
            index=2,
            role=SectionRole.LIMITATIONS,
            title="研究局限",
            purpose="说明证据边界。",
        ),
        OutlineSection(
            index=3,
            role=SectionRole.CONCLUSION,
            title="结论",
            purpose="总结判断。",
            search_terms=["同行评审 结论"],
        ),
    )

    queries = _queries(outline)

    assert [(item.kind, item.query_text) for item in queries] == [
        ("web", "同行评审 背景"),
        ("academic", "同行评审 背景"),
        ("web", "同行评审 错误率"),
        ("academic", "peer review error rates"),
        ("web", "同行评审 结论"),
        ("academic", "同行评审 结论"),
    ]
    assert all(item.round_index == 2 for item in queries)
    assert all(item.outline_section_id in {section.section_id for section in outline.sections} for item in queries)
    assert [item.outline_section_id for item in queries] == [
        outline.sections[0].section_id,
        outline.sections[0].section_id,
        body.section_id,
        body.section_id,
        outline.sections[3].section_id,
        outline.sections[3].section_id,
    ]


def test_queries_fall_back_to_the_section_title_without_terms():
    body = OutlineSection(
        index=0,
        title="How does peer review affect reliability",
        purpose="Explain the effect.",
    )
    outline = _outline(body)

    queries = _queries(outline)

    assert {item.query_text for item in queries} == {body.title}


def test_query_limit_truncates_without_breaking_section_links():
    outline = _outline(
        OutlineSection(index=0, title="Background", purpose="Frame the issue.", search_terms=["review background"]),
        OutlineSection(index=1, title="Effects", purpose="Explain effects.", search_terms=["review effects"]),
    )

    queries = _queries(outline, limit=1)

    assert len(queries) == 1
    assert queries[0].outline_section_id == outline.sections[0].section_id


def test_query_pair_prefers_the_first_and_the_first_latin_term():
    assert query_pair("主题", []) == ("主题", "主题")
    assert query_pair("主题", ["  同行评审  错误率 "]) == (
        "同行评审 错误率",
        "同行评审 错误率",
    )
    assert query_pair("主题", ["KernelGen 芯片", "KernelGen chip design"]) == (
        "KernelGen 芯片",
        "KernelGen 芯片",
    )
    assert query_pair("主题", ["同行评审", "peer review"]) == (
        "同行评审",
        "peer review",
    )


def test_gap_queries_use_terms_and_keep_the_language_suffixes():
    zh = _brief()

    assert _gap_queries(zh, "错误率", ["同行评审 错误率", "peer review error rates"]) == [
        "同行评审 错误率 证据 数据 案例",
        "peer review error rates evidence data cases",
    ]
    assert _counter_queries(zh, "错误率", ["同行评审 错误率", "peer review error rates"]) == [
        "同行评审 错误率 反方证据 批评 争议",
        "peer review error rates counter-evidence critique controversy",
    ]
    assert _gap_queries(zh, "错误率", []) == [
        "错误率 证据 数据 案例",
        "错误率 反方观点 局限 批评",
    ]

    en = _brief(
        language=ProjectLanguage.EN,
        title="Peer review",
        topic="Peer review reliability",
    )
    assert _gap_queries(en, "Review bias", ["review bias"]) == [
        "review bias evidence data cases",
        "review bias counterarguments limitations critique",
    ]
    assert _counter_queries(en, "Review bias", []) == [
        "Review bias counter-evidence critique controversy",
        "Review bias risks boundary conditions",
    ]


def test_schema_normalizes_section_search_terms():
    section = OutlineSection(
        index=0,
        title="错误率",
        purpose="说明影响。",
        search_terms=[
            "  peer   review ",
            "PEER REVIEW",
            "",
            "review bias",
            "third term",
            "fourth term",
        ],
    )

    assert section.search_terms == [
        "peer review",
        "review bias",
        "third term",
        "fourth term",
    ]
