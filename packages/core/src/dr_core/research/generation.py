from __future__ import annotations

from typing import Iterable
from uuid import UUID

from dr_core.db.enums import (
    EvidenceType,
    ProjectLanguage,
    QuestionCoverageStatus,
    ResearchDepth,
    SearchProviderName,
)
from dr_core.research.models import (
    EvidenceMatrix,
    EvidenceRow,
    Outline,
    OutlineSection,
    ResearchBrief,
    ResearchGap,
    ResearchPlan,
    ResearchQuestion,
    SearchQuery,
)


def build_plan(
    brief: ResearchBrief,
    *,
    version: int = 1,
    feedback: str | None = None,
) -> ResearchPlan:
    topic = brief.topic.strip().rstrip("。.!?！？")
    scope = (brief.research_scope or "").strip()
    limit = {
        ResearchDepth.QUICK: 3,
        ResearchDepth.STANDARD: 4,
        ResearchDepth.DEEP: 5,
    }[brief.research_depth]
    if brief.language is ProjectLanguage.ZH:
        candidates = [
            f"{topic}的核心概念、现状与主要结论是什么？",
            f"有哪些关键证据、数据或案例支持关于{topic}的判断？",
            f"{topic}存在哪些争议、反方观点、风险或适用边界？",
            (
                f"在{scope}范围内，{topic}的实践路径和影响如何？"
                if scope
                else f"{topic}的最新进展、趋势与实践影响如何？"
            ),
            f"如何评价{topic}相关结论的可靠性、证据质量与可推广性？",
        ]
        summary = f"围绕“{topic}”建立多角度研究计划，优先覆盖事实、证据、争议、实践与可靠性。"
    else:
        candidates = [
            f"What are the core concepts, current state, and main conclusions of {topic}?",
            f"What key evidence, data, or cases support or challenge claims about {topic}?",
            f"What controversies, counterarguments, risks, or boundary conditions surround {topic}?",
            (
                f"How does {topic} play out in practice within {scope}?"
                if scope
                else f"What recent developments and practical implications shape {topic}?"
            ),
            f"How reliable, well-evidenced, and generalizable are conclusions about {topic}?",
        ]
        summary = (
            f"Build a multi-angle research plan for “{topic}” covering facts, "
            "evidence, debate, practice, and reliability."
        )

    if feedback:
        if brief.language is ProjectLanguage.ZH:
            candidates.insert(0, f"根据审批反馈需要补充研究：{feedback[:400]}")
        else:
            candidates.insert(0, f"Address the approval feedback: {feedback[:400]}")

    deduped: list[str] = []
    seen: set[str] = set()
    for question in candidates:
        key = question.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(question)
        if len(deduped) >= limit:
            break

    questions = [
        ResearchQuestion(
            question=question,
            priority=max(10, 90 - index * 10),
        )
        for index, question in enumerate(deduped)
    ]
    return ResearchPlan(
        version=version,
        summary=summary,
        questions=questions,
        scope_notes=scope or None,
    )


def build_queries(
    plan: ResearchPlan,
    *,
    round_index: int,
    web_providers: list[SearchProviderName],
    academic_providers: list[SearchProviderName],
    limit: int,
) -> list[SearchQuery]:
    queries: list[SearchQuery] = []
    for index, question in enumerate(plan.questions):
        if len(queries) >= limit:
            break
        if web_providers:
            queries.append(
                SearchQuery(
                    query_text=question.question,
                    kind="web",
                    provider=web_providers[index % len(web_providers)],
                    round_index=round_index,
                    research_question_id=question.question_id,
                )
            )
        if len(queries) < limit and academic_providers:
            queries.append(
                SearchQuery(
                    query_text=question.question,
                    kind="academic",
                    provider=academic_providers[
                        index % len(academic_providers)
                    ],
                    round_index=round_index,
                    research_question_id=question.question_id,
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
            query_text = text if gap.suggested_queries else f"{gap.question} {suffix}"
            queries.append(
                SearchQuery(
                    query_text=query_text,
                    kind=kind,
                    provider=provider_pool[
                        (gap_index + query_index) % len(provider_pool)
                    ],
                    round_index=round_index,
                    research_question_id=gap.question_id,
                )
            )
    return queries


def build_outline(
    brief: ResearchBrief,
    plan: ResearchPlan,
    matrix: EvidenceMatrix,
    gaps: list[ResearchGap],
    *,
    version: int,
    evidence_by_question: dict[UUID | None, list[UUID]],
    feedback: str | None = None,
) -> Outline:
    sections: list[OutlineSection] = []
    if brief.language is ProjectLanguage.ZH:
        sections.append(
            OutlineSection(
                index=0,
                title="引言",
                purpose="说明研究问题、范围与方法，并概述主要发现。",
            )
        )
        for question in plan.questions:
            sections.append(
                OutlineSection(
                    index=len(sections),
                    title=_short_title(question.question),
                    purpose="围绕子问题整理支持证据、反方证据与证据强度。",
                    question_id=question.question_id,
                    evidence_ids=evidence_by_question.get(question.question_id, []),
                )
            )
        if matrix.contradiction_count:
            sections.append(
                OutlineSection(
                    index=len(sections),
                    title="矛盾与反方证据",
                    purpose="对比相互冲突的证据并解释分歧来源。",
                )
            )
        if gaps:
            sections.append(
                OutlineSection(
                    index=len(sections),
                    title="研究局限与未解决问题",
                    purpose="说明检索和证据覆盖的不足，不将空白包装成结论。",
                )
            )
        sections.append(
            OutlineSection(
                index=len(sections),
                title="结论",
                purpose="综合证据回答主问题，并说明适用边界。",
            )
        )
    else:
        sections.append(
            OutlineSection(
                index=0,
                title="Introduction",
                purpose="Frame the research question, scope, method, and key findings.",
            )
        )
        for question in plan.questions:
            sections.append(
                OutlineSection(
                    index=len(sections),
                    title=_short_title(question.question),
                    purpose=(
                        "Synthesize supporting and opposing evidence and assess its strength."
                    ),
                    question_id=question.question_id,
                    evidence_ids=evidence_by_question.get(question.question_id, []),
                )
            )
        if matrix.contradiction_count:
            sections.append(
                OutlineSection(
                    index=len(sections),
                    title="Contradictions and Counter-Evidence",
                    purpose="Compare conflicting evidence and explain the disagreement.",
                )
            )
        if gaps:
            sections.append(
                OutlineSection(
                    index=len(sections),
                    title="Limitations and Open Questions",
                    purpose="State evidence gaps without presenting them as conclusions.",
                )
            )
        sections.append(
            OutlineSection(
                index=len(sections),
                title="Conclusion",
                purpose="Answer the main question and state boundary conditions.",
            )
        )

    summary = (
        f"围绕 {len(plan.questions)} 个研究问题组织报告，覆盖 {matrix.source_count} 个来源。"
        if brief.language is ProjectLanguage.ZH
        else (
            f"Organize the report around {len(plan.questions)} research questions "
            f"using {matrix.source_count} sources."
        )
    )
    if feedback:
        if brief.language is ProjectLanguage.ZH:
            sections.insert(
                len(sections) - 1,
                OutlineSection(
                    index=len(sections) - 1,
                    title="审批反馈",
                    purpose=f"回应提纲审批意见：{feedback[:500]}",
                ),
            )
            for index, section in enumerate(sections):
                section.index = index
            summary += f" 本版本回应审批反馈：{feedback[:300]}"
        else:
            sections.insert(
                len(sections) - 1,
                OutlineSection(
                    index=len(sections) - 1,
                    title="Approval Feedback",
                    purpose=f"Address the outline feedback: {feedback[:500]}",
                ),
            )
            for index, section in enumerate(sections):
                section.index = index
            summary += f" This version addresses feedback: {feedback[:300]}"
    return Outline(
        version=version,
        title=brief.title,
        sections=sections,
        summary=summary,
    )


def source_numbering(
    evidence: list,
) -> dict[UUID, int]:
    source_ids = sorted(
        {item.source_id for item in evidence},
        key=lambda value: str(value),
    )
    return {source_id: index + 1 for index, source_id in enumerate(source_ids)}


def write_section(
    *,
    section: OutlineSection,
    brief: ResearchBrief,
    matrix: EvidenceMatrix,
    gaps: list[ResearchGap],
    limitations: list[str],
    evidence: list,
    numbering: dict[UUID, int],
) -> tuple[str, dict[str, str], list[UUID]]:
    language = brief.language
    title = section.title
    lines = [f"## {title}", ""]
    section_id = section.question_id

    if section.index == 0:
        if language is ProjectLanguage.ZH:
            lines.append(
                f"本文围绕“{brief.topic}”展开，覆盖 {matrix.source_count} 个来源、"
                f"{matrix.total_evidence} 条证据，整体证据覆盖率为 "
                f"{matrix.coverage_ratio:.0%}。"
            )
        else:
            lines.append(
                f"This report examines “{brief.topic}” using {matrix.source_count} "
                f"sources and {matrix.total_evidence} evidence items; overall "
                f"question coverage is {matrix.coverage_ratio:.0%}."
            )
        if evidence:
            marker = numbering.get(evidence[0].source_id)
            if marker:
                lines.append(
                    f"主要发现来自可追溯来源 [{_marker(marker)}]。"
                    if language is ProjectLanguage.ZH
                    else f"The main findings are drawn from traceable sources [{_marker(marker)}]."
                )
        lines.append("")
        return "\n".join(lines), _map_for_used(evidence[:1], numbering), [
            evidence[0].id
        ] if evidence else []

    if section.title in {
        "研究局限与未解决问题",
        "Limitations and Open Questions",
    }:
        entries = list(dict.fromkeys([*limitations, *(gap.reason for gap in gaps)]))
        if not entries:
            entries = (
                ["未发现需要单独列出的重大局限。"]
                if language is ProjectLanguage.ZH
                else ["No material limitations were identified."]
            )
        lines.extend(f"- {entry}" for entry in entries[:12])
        lines.append("")
        return "\n".join(lines), {}, []

    if section.title in {"矛盾与反方证据", "Contradictions and Counter-Evidence"}:
        rows = [row for row in matrix.rows if row.refute_count and row.support_count]
        if not rows:
            lines.append(
                "当前证据中未发现明确的正反冲突。"
                if language is ProjectLanguage.ZH
                else "No explicit supporting-versus-refuting conflict was found."
            )
        for row in rows:
            lines.append(
                f"- “{row.question}”同时包含 {row.support_count} 条支持证据和 "
                f"{row.refute_count} 条反向证据，需要结合来源质量判断。"
                if language is ProjectLanguage.ZH
                else (
                    f"- “{row.question}” has {row.support_count} supporting and "
                    f"{row.refute_count} refuting items; source quality should be weighed."
                )
            )
        lines.append("")
        return "\n".join(lines), {}, []

    items = [
        item
        for item in evidence
        if section_id is not None and item.research_question_id == section_id
    ]
    if not items:
        items = evidence[:2]
    if not items:
        lines.append(
            "该部分尚缺少可引用的原始证据，因此不生成结论性陈述。"
            if language is ProjectLanguage.ZH
            else "No source-backed evidence is available for this section, so no conclusion is asserted."
        )
        lines.append("")
        return "\n".join(lines), {}, []

    citation_map: dict[str, str] = {}
    used_ids: list[UUID] = []
    for item in items[:5]:
        marker_number = numbering.get(item.source_id)
        if marker_number is None:
            continue
        marker = _marker(marker_number)
        citation_map[marker] = str(item.source_id)
        used_ids.append(item.id)
        stance = ""
        if item.evidence_type is EvidenceType.REFUTATION:
            stance = "（反向证据）" if language is ProjectLanguage.ZH else " (counter-evidence)"
        elif item.evidence_type is EvidenceType.STATISTIC:
            stance = "（统计证据）" if language is ProjectLanguage.ZH else " (statistical evidence)"
        lines.append(f"- {item.claim_summary}{stance} [{marker}]")
    if section.title in {"结论", "Conclusion"} and len(items) > 5:
        lines.append(
            f"- 另有 {len(items) - 5} 条证据支持上述综合判断。"
            if language is ProjectLanguage.ZH
            else f"- {len(items) - 5} additional evidence items support this synthesis."
        )
    lines.append("")
    return "\n".join(lines), citation_map, used_ids


def _map_for_used(evidence: list, numbering: dict[UUID, int]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in evidence:
        number = numbering.get(item.source_id)
        if number is not None:
            mapping[_marker(number)] = str(item.source_id)
    return mapping


def _marker(number: int) -> str:
    return f"S{number}"


def _short_title(question: str) -> str:
    cleaned = question.strip().rstrip("？?。.")
    if len(cleaned) <= 60:
        return cleaned
    return cleaned[:57] + "..."
