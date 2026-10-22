from __future__ import annotations

from uuid import uuid4

from dr_core.db.enums import ProjectLanguage, ResearchDepth, SearchProviderName
from dr_core.research.generation import build_queries, query_pair
from dr_core.research.models import ResearchBrief, ResearchPlan, ResearchQuestion
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


def _plan(*questions: ResearchQuestion) -> ResearchPlan:
    return ResearchPlan(summary="计划", questions=list(questions))


def _queries(plan: ResearchPlan):
    return build_queries(
        plan,
        round_index=1,
        web_providers=[SearchProviderName.TAVILY],
        academic_providers=[SearchProviderName.CROSSREF],
        limit=10,
    )


def test_web_queries_use_the_chinese_term_and_academic_queries_the_english_one():
    plan = _plan(
        ResearchQuestion(
            question="同行评审对论文错误率的实际影响有哪些证据？",
            search_terms=["同行评审 错误率", "peer review error rates"],
        )
    )

    queries = _queries(plan)

    assert [(item.kind, item.query_text) for item in queries] == [
        ("web", "同行评审 错误率"),
        ("academic", "peer review error rates"),
    ]


def test_queries_fall_back_to_the_full_question_without_terms():
    question = ResearchQuestion(
        question="How does peer review affect research reliability?"
    )

    queries = _queries(_plan(question))

    assert {item.query_text for item in queries} == {question.question}


def test_query_pair_prefers_the_first_and_the_first_latin_term():
    assert query_pair("问题", []) == ("问题", "问题")
    assert query_pair("问题", ["  同行评审  错误率 "]) == (
        "同行评审 错误率",
        "同行评审 错误率",
    )
    assert query_pair("问题", ["KernelGen 芯片", "KernelGen chip design"]) == (
        "KernelGen 芯片",
        "KernelGen 芯片",
    )
    assert query_pair("问题", ["同行评审", "peer review"]) == (
        "同行评审",
        "peer review",
    )


def test_gap_queries_use_terms_and_keep_the_language_suffixes():
    zh = _brief()
    terms = ["同行评审 错误率", "peer review error rates"]

    assert _gap_queries(zh, "问题", terms) == [
        "同行评审 错误率 证据 数据 案例",
        "peer review error rates evidence data cases",
    ]
    assert _counter_queries(zh, "问题", terms) == [
        "同行评审 错误率 反方证据 批评 争议",
        "peer review error rates counter-evidence critique controversy",
    ]
    assert _gap_queries(zh, "问题", []) == [
        "问题 证据 数据 案例",
        "问题 反方观点 局限 批评",
    ]

    en = _brief(
        language=ProjectLanguage.EN,
        title="Peer review",
        topic="Peer review reliability",
    )
    assert _gap_queries(en, "question", ["review bias"]) == [
        "review bias evidence data cases",
        "review bias counterarguments limitations critique",
    ]


def test_schema_normalizes_search_terms():
    question = ResearchQuestion(
        question="Q",
        search_terms=[
            "  peer   review ",
            "PEER REVIEW",
            "",
            "review bias",
            "third term",
            "fourth term",
        ],
    )

    assert question.search_terms == [
        "peer review",
        "review bias",
        "third term",
    ]
