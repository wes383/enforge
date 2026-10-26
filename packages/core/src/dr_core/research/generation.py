from __future__ import annotations

import re
from typing import Iterable
from uuid import UUID

from dr_core.db.enums import (
    ArticleType,
    ProjectLanguage,
    ResearchDepth,
    SearchProviderName,
    SectionRole,
)
from dr_core.research.models import (
    Outline,
    OutlineSection,
    ResearchBrief,
    ResearchGap,
    SearchQuery,
)

_LATIN_RE = re.compile(r"[A-Za-z]")

_DEPTH_SCALE = {
    ResearchDepth.QUICK: 0.8,
    ResearchDepth.STANDARD: 1.0,
    ResearchDepth.DEEP: 1.3,
}
_DEFAULT_SECTIONS = {
    ResearchDepth.QUICK: 2,
    ResearchDepth.STANDARD: 3,
    ResearchDepth.DEEP: 4,
}

_TYPE_TITLES: dict[ArticleType, dict[ProjectLanguage, tuple[str, ...]]] = {
    ArticleType.EXPLAINER: {
        ProjectLanguage.ZH: ("核心概念与定义", "运作机制", "为什么重要", "常见误解与边界"),
        ProjectLanguage.EN: ("Core concepts and definitions", "How it works", "Why it matters", "Common misunderstandings and limits"),
    },
    ArticleType.MECHANISM_ANALYSIS: {
        ProjectLanguage.ZH: ("待解释的现象", "竞争性解释", "因果机制", "证据检验与适用条件"),
        ProjectLanguage.EN: ("The phenomenon to explain", "Competing explanations", "Causal mechanisms", "Testing explanations and boundary conditions"),
    },
    ArticleType.INVESTIGATIVE_RESEARCH: {
        ProjectLanguage.ZH: ("议题背景与边界", "已确认的事实", "关键证据与数据", "争议与不同立场", "研究缺口"),
        ProjectLanguage.EN: ("Background and boundaries", "Established facts", "Key evidence and data", "Disputes and positions", "Research gaps"),
    },
    ArticleType.CASE_STUDY: {
        ProjectLanguage.ZH: ("案例选择与背景", "事件时间线", "关键决策与参与者", "结果与成因", "可迁移条件"),
        ProjectLanguage.EN: ("Case selection and context", "Timeline", "Decisions and actors", "Outcomes and causes", "Transferable conditions"),
    },
    ArticleType.COMPARATIVE_ANALYSIS: {
        ProjectLanguage.ZH: ("比较对象与标准", "维度对比", "差异来源", "适用条件与结论"),
        ProjectLanguage.EN: ("Objects and criteria", "Comparison", "Sources of difference", "Applicability and conclusions"),
    },
    ArticleType.ARGUMENT_CRITIQUE: {
        ProjectLanguage.ZH: ("核心主张", "论证结构", "支持证据", "最强反方观点", "回应与结论"),
        ProjectLanguage.EN: ("The central claim", "Argument structure", "Supporting evidence", "The strongest objection", "Response and conclusion"),
    },
    ArticleType.TREND_SCENARIO: {
        ProjectLanguage.ZH: ("趋势与观察窗口", "历史轨迹与驱动因素", "确定性与不确定性", "情景分析", "需要观察的信号"),
        ProjectLanguage.EN: ("Trend and observation window", "Trajectory and drivers", "Certainties and uncertainties", "Scenarios", "Signals to watch"),
    },
    ArticleType.DECISION_STRATEGY: {
        ProjectLanguage.ZH: ("决策目标与约束", "备选方案", "评估标准与证据", "权衡与敏感性", "建议与实施路径"),
        ProjectLanguage.EN: ("Decision, goals and constraints", "Options", "Criteria and evidence", "Tradeoffs and sensitivity", "Recommendation and roadmap"),
    },
    ArticleType.HISTORICAL_DEVELOPMENT: {
        ProjectLanguage.ZH: ("研究对象与范围", "起源与前身", "发展阶段", "转折点与机制", "连续、断裂与争议"),
        ProjectLanguage.EN: ("Object and scope", "Origins and precursors", "Phases of development", "Turning points and mechanisms", "Continuity, rupture and disputes"),
    },
}


def query_pair(title: str, search_terms: list[str]) -> tuple[str, str]:
    """(primary, latin) query text: web uses primary, academic uses latin."""
    terms = [
        " ".join(term.split())
        for term in search_terms
        if term and term.strip()
    ]
    primary = terms[0] if terms else title
    latin = next((term for term in terms if _LATIN_RE.search(term)), primary)
    return primary, latin


def build_queries(
    outline: Outline,
    *,
    round_index: int,
    web_providers: list[SearchProviderName],
    academic_providers: list[SearchProviderName],
    limit: int,
) -> list[SearchQuery]:
    queries: list[SearchQuery] = []
    searchable = [
        section
        for section in outline.sections
        if section.role
        in {
            SectionRole.INTRODUCTION,
            SectionRole.BODY,
            SectionRole.CONTRADICTIONS,
            SectionRole.CONCLUSION,
        }
    ]
    for index, section in enumerate(searchable):
        if len(queries) >= limit:
            break
        primary, latin = query_pair(section.title, section.search_terms)
        if web_providers:
            queries.append(
                SearchQuery(
                    query_text=primary,
                    kind="web",
                    provider=web_providers[index % len(web_providers)],
                    round_index=round_index,
                    outline_section_id=section.section_id,
                )
            )
        if len(queries) < limit and academic_providers:
            queries.append(
                SearchQuery(
                    query_text=latin,
                    kind="academic",
                    provider=academic_providers[
                        index % len(academic_providers)
                    ],
                    round_index=round_index,
                    outline_section_id=section.section_id,
                )
            )
    return queries


def build_gap_queries(
    gaps: Iterable[ResearchGap],
    *,
    round_index: int,
    web_providers: list[SearchProviderName],
    academic_providers: list[SearchProviderName],
    limit: int,
    language: ProjectLanguage,
) -> list[SearchQuery]:
    queries: list[SearchQuery] = []
    suffix = "反方证据 局限 批评" if language is ProjectLanguage.ZH else (
        "counterargument limitations critique"
    )
    for gap_index, gap in enumerate(gaps):
        for query_index, text in enumerate(gap.suggested_queries):
            if len(queries) >= limit:
                return queries
            provider_pool = (
                web_providers
                if query_index % 2 == 0
                else academic_providers or web_providers
            )
            kind = "web" if provider_pool is web_providers else "academic"
            if not provider_pool:
                continue
            query_text = text if gap.suggested_queries else f"{gap.section_title} {suffix}"
            queries.append(
                SearchQuery(
                    query_text=query_text,
                    kind=kind,
                    provider=provider_pool[
                        (gap_index + query_index) % len(provider_pool)
                    ],
                    round_index=round_index,
                    outline_section_id=gap.section_id,
                )
            )
    return queries


def build_outline_template(
    brief: ResearchBrief,
    *,
    version: int = 1,
    feedback: str | None = None,
) -> Outline:
    """Deterministic article outline used when no planning model is available."""
    zh = brief.language is ProjectLanguage.ZH
    topic = brief.topic.strip().rstrip("。.!?！？")
    titles = _TYPE_TITLES[brief.article_type][brief.language]
    body_count = _body_count(brief, len(titles))
    body_titles = list(titles[:body_count])
    while len(body_titles) < body_count:
        body_titles.append(
            f"补充分析 {len(body_titles) + 1}"
            if zh
            else f"Further analysis {len(body_titles) + 1}"
        )

    sections: list[OutlineSection] = [
        OutlineSection(
            index=0,
            role=SectionRole.INTRODUCTION,
            title="引言" if zh else "Introduction",
            purpose=(
                f"引出“{topic}”的利害、范围与文章主线，不罗列证据清单。"
                if zh
                else (
                    f"Frame the stakes, scope and line of argument for “{topic}” "
                    "without listing evidence."
                )
            ),
            search_terms=_terms(brief, "背景" if zh else "background"),
            target_words=_section_words(
                brief, SectionRole.INTRODUCTION, body_count
            ),
        )
    ]
    for position, title in enumerate(body_titles):
        sections.append(
            OutlineSection(
                index=len(sections),
                role=SectionRole.BODY,
                title=title,
                purpose=(
                    f"围绕“{topic}”展开这一维度，说明证据支持什么、边界在哪里，"
                    "并把结论交给下一节。"
                    if zh
                    else (
                        f"Develop this dimension of “{topic}”: what the evidence "
                        "supports, where its limits lie, and what the next section "
                        "inherits."
                    )
                ),
                search_terms=_terms(brief, title),
                target_words=_section_words(brief, SectionRole.BODY, body_count),
                key_claims=(
                    [feedback[:300]]
                    if feedback and position == 0
                    else []
                ),
            )
        )
    if brief.article_type in {
        ArticleType.ARGUMENT_CRITIQUE,
        ArticleType.INVESTIGATIVE_RESEARCH,
        ArticleType.MECHANISM_ANALYSIS,
    }:
        sections.append(
            OutlineSection(
                index=len(sections),
                role=SectionRole.CONTRADICTIONS,
                title="争议与反方证据" if zh else "Disputes and Counter-Evidence",
                purpose=(
                    "对比相互冲突的材料，解释分歧来源，不把单方证据当成定论。"
                    if zh
                    else (
                        "Compare conflicting material, explain the disagreement and "
                        "avoid treating one side as settled."
                    )
                ),
                search_terms=_terms(
                    brief,
                    "反方证据 争议" if zh else "counter-evidence controversy",
                ),
                target_words=_section_words(
                    brief, SectionRole.CONTRADICTIONS, body_count
                ),
            )
        )
    sections.append(
        OutlineSection(
            index=len(sections),
            role=SectionRole.CONCLUSION,
            title="结论" if zh else "Conclusion",
            purpose=(
                "综合证据回答主问题，并说明结论成立的条件。"
                if zh
                else (
                    "Answer the main issue by synthesizing the evidence and state "
                    "the conditions under which the conclusion holds."
                )
            ),
            search_terms=_terms(brief, "结论 趋势" if zh else "conclusion outlook"),
            target_words=_section_words(brief, SectionRole.CONCLUSION, body_count),
        )
    )

    summary = (
        f"围绕“{topic}”组织一篇{_type_label(brief, zh)}，先界定问题，再讨论证据、"
        "分歧与适用条件。"
        if zh
        else (
            f"Organize a {_type_label(brief, zh)} on “{topic}” that defines the "
            "issue, then examines evidence, disagreement and conditions of "
            "applicability."
        )
    )
    if feedback:
        summary += (
            f" 本次提纲回应审批意见：{feedback[:300]}"
            if zh
            else f" This outline responds to the approval feedback: {feedback[:300]}"
        )
    return Outline(
        version=version,
        title=brief.title,
        sections=sections,
        summary=summary[:20000],
    )


def target_section_range(brief: ResearchBrief) -> tuple[int, int]:
    """Soft range shown to the planning model; not a validation limit."""
    if brief.target_words:
        low = max(3, round(brief.target_words / 1800))
        high = max(low + 2, round(brief.target_words / 700))
    else:
        base = _DEFAULT_SECTIONS[brief.research_depth]
        low, high = max(3, base), base + 2
    return low, high


def source_numbering(
    evidence: list,
) -> dict[UUID, int]:
    source_ids = sorted(
        {item.source_id for item in evidence},
        key=lambda value: str(value),
    )
    return {source_id: index + 1 for index, source_id in enumerate(source_ids)}


def _body_count(brief: ResearchBrief, available: int) -> int:
    if brief.target_words:
        count = max(2, round(brief.target_words * 0.72 / 1200))
    else:
        count = _DEFAULT_SECTIONS[brief.research_depth]
    return max(1, min(available, count)) if available else count


def _terms(brief: ResearchBrief, angle: str) -> list[str]:
    topic = " ".join(brief.topic.split())[:200]
    terms = [f"{topic} {angle}", topic]
    if angle.casefold() in topic.casefold():
        terms = [topic]
    seen: set[str] = set()
    cleaned: list[str] = []
    for term in terms:
        key = term.casefold()
        if not term or key in seen:
            continue
        seen.add(key)
        cleaned.append(term)
    return cleaned[:3]


def _base_words(brief: ResearchBrief) -> int:
    if brief.target_words:
        return max(100, brief.target_words)
    return {
        ResearchDepth.QUICK: 1200,
        ResearchDepth.STANDARD: 2200,
        ResearchDepth.DEEP: 3400,
    }[brief.research_depth]


def _section_words(
    brief: ResearchBrief,
    role: SectionRole,
    body_count: int,
) -> int:
    total = _base_words(brief)
    intro_share = 0.1
    conclusion_share = 0.1
    contradiction_share = 0.1
    if role is SectionRole.INTRODUCTION:
        words = total * intro_share
    elif role is SectionRole.CONCLUSION:
        words = total * conclusion_share
    elif role is SectionRole.CONTRADICTIONS:
        words = total * contradiction_share
    else:
        words = total * (
            1 - intro_share - conclusion_share
        ) / max(1, body_count)
    scale = _DEPTH_SCALE[brief.research_depth]
    return max(50, min(5000, round(words * scale)))


def _type_label(brief: ResearchBrief, zh: bool) -> str:
    from dr_core.research.article_types import article_type_guide

    guide = article_type_guide(brief.article_type)
    return guide.label_zh if zh else guide.label_en
