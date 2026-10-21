from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from dr_core.db.enums import ArtifactType, CitationAuditStatus
from dr_core.db.repositories import (
    ArtifactRepository,
    SourceRepository,
)
from dr_core.normalization import text_sha256
from dr_core.research.models import (
    EvidenceMatrix,
    ReferenceAudit,
    ResearchBrief,
)
from dr_core.research.text import format_reference
from dr_core.schemas import ArtifactCreate


def source_views(session, project_id: UUID) -> dict[UUID, SimpleNamespace]:
    views: dict[UUID, SimpleNamespace] = {}
    for source in SourceRepository(session).list_for_project(project_id, limit=500):
        views[source.id] = SimpleNamespace(
            id=source.id,
            title=source.title,
            authors=list(source.authors or []),
            published_at=source.published_at,
            venue=source.venue,
            publisher=source.publisher,
            doi=source.doi,
            canonical_url=source.canonical_url,
            source_type=source.source_type,
        )
    return views


def reference_lines(brief, sections, sources) -> list[str]:
    numbering: dict[int, UUID] = {}
    for section in sections:
        for marker, source_id in dict(section.citation_map or {}).items():
            digits = "".join(character for character in marker if character.isdigit())
            if not digits:
                continue
            try:
                numbering[int(digits)] = UUID(str(source_id))
            except ValueError:
                continue
    lines = []
    for number in sorted(numbering):
        source = sources.get(numbering[number])
        if source is None:
            continue
        reference = format_reference(
            authors=source.authors or [],
            title=source.title,
            year=source.published_at.year if source.published_at else None,
            venue=source.venue,
            publisher=source.publisher,
            doi=source.doi,
            url=source.canonical_url,
            style=brief.citation_style.value,
        )
        lines.append(f"[S{number}] {reference}")
    return lines


def report_markdown(
    brief: ResearchBrief,
    matrix: EvidenceMatrix,
    sections,
    references: list[str],
    citations,
    limitations: list[str],
    *,
    claims: list[dict] | None = None,
    reference_audit: ReferenceAudit | None = None,
) -> str:
    lines = [
        f"# {brief.title}",
        "",
        _text(brief, "## 研究摘要", "## Research Summary"),
        "",
        _text(
            brief,
            f"本报告围绕“{brief.topic}”生成。共分析 {matrix.source_count} 个来源、"
            f"{matrix.total_evidence} 条证据，问题覆盖率为 {matrix.coverage_ratio:.0%}。",
            f"This report examines “{brief.topic}” using {matrix.source_count} sources "
            f"and {matrix.total_evidence} evidence items; question coverage is "
            f"{matrix.coverage_ratio:.0%}.",
        ),
        "",
        _text(
            brief,
            f"- 生成时间：{datetime.now(tz=UTC).isoformat()}",
            f"- Generated at: {datetime.now(tz=UTC).isoformat()}",
        ),
        _text(
            brief,
            f"- 引用格式：{brief.citation_style.value.upper()}",
            f"- Citation style: {brief.citation_style.value.upper()}",
        ),
        "",
    ]
    lines.extend(
        [
            _text(brief, "## 证据覆盖", "## Evidence Coverage"),
            "",
            _text(
                brief,
                "| 研究问题 | 覆盖状态 | 支持证据 | 相反证据 | 独立来源 |",
                "| Question | Coverage | Supporting | Counter | Independent sources |",
            ),
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in matrix.rows:
        question = row.question.replace("|", "\\|")[:500]
        lines.append(
            f"| {question} | {row.coverage_status.value} | "
            f"{row.support_count} | {row.refute_count} | "
            f"{row.independent_source_count} |"
        )
    if matrix.duplicate_source_groups:
        lines.extend(
            [
                "",
                _text(
                    brief,
                    "以下来源可能转述同一项原始工作，未按独立来源计数：",
                    "The following sources appear to report the same original work "
                    "and were not counted as independent:",
                ),
            ]
        )
        lines.extend(
            f"- {group}" for group in matrix.duplicate_source_groups[:10]
        )
    lines.append("")
    for section in sections:
        lines.append(section.content_markdown.rstrip())
        lines.append("")
    lines.append(_text(brief, "## 参考文献", "## References"))
    lines.append("")
    if references:
        lines.extend(references)
    else:
        lines.append(
            _text(
                brief,
                "没有可核验的参考文献。",
                "No verifiable references were available.",
            )
        )
    lines.extend(["", _text(brief, "## 引用核验", "## Citation Audit"), ""])
    failed = sum(1 for audit in citations if audit.status is CitationAuditStatus.FAILED)
    warnings = sum(
        1 for audit in citations if audit.status is CitationAuditStatus.WARNING
    )
    lines.append(
        _text(
            brief,
            f"共记录 {len(citations)} 条核验结果：失败 {failed}，警告 {warnings}。",
            f"{len(citations)} audit records: {failed} failed, {warnings} warnings.",
        )
    )
    if claims:
        counts = claim_status_counts(claims)
        lines.extend(["", _text(brief, "## 事实核验", "## Claim Verification"), ""])
        lines.append(
            _text(
                brief,
                f"共抽取 {counts.get('total', 0)} 条事实主张："
                f"有支持 {counts.get('supported', 0)}，部分支持 {counts.get('partial', 0)}，"
                f"矛盾 {counts.get('contradicted', 0)}，无支持 {counts.get('unsupported', 0)}，"
                f"无法核验 {counts.get('unverifiable', 0)}。",
                f"{counts.get('total', 0)} claims extracted: "
                f"{counts.get('supported', 0)} supported, "
                f"{counts.get('partial', 0)} partial, "
                f"{counts.get('contradicted', 0)} contradicted, "
                f"{counts.get('unsupported', 0)} unsupported, "
                f"{counts.get('unverifiable', 0)} unverifiable.",
            )
        )
        flagged = [
            claim
            for claim in claims
            if claim.get("status") not in {"SUPPORTED"}
        ]
        if flagged:
            lines.extend(
                [
                    "",
                    _text(
                        brief,
                        "未完全通过核验的主张：",
                        "Claims that did not fully pass verification:",
                    ),
                ]
            )
            for claim in flagged[:10]:
                lines.append(
                    f"- [{claim.get('status')}] {claim.get('claim_text', '')[:300]}"
                )
    if reference_audit is not None:
        lines.extend(["", _text(brief, "## 参考文献审计", "## Reference Audit"), ""])
        lines.append(
            _text(
                brief,
                f"正文引用来源 {len(reference_audit.cited_source_ids)} 个，"
                f"未引用证据来源 {len(reference_audit.unused_source_ids)} 个，"
                f"元数据不完整 {len(reference_audit.invalid_metadata_source_ids)} 个，"
                f"悬空标记 {len(reference_audit.dangling_markers)} 个。",
                f"{len(reference_audit.cited_source_ids)} sources cited, "
                f"{len(reference_audit.unused_source_ids)} evidence sources unused, "
                f"{len(reference_audit.invalid_metadata_source_ids)} with incomplete metadata, "
                f"{len(reference_audit.dangling_markers)} dangling markers.",
            )
        )
        if reference_audit.issues:
            lines.extend(f"- {issue}" for issue in reference_audit.issues[:10])
    lines.extend(["", _text(brief, "## 研究局限", "## Limitations"), ""])
    if limitations:
        lines.extend(f"- {item}" for item in list(dict.fromkeys(limitations)))
    else:
        lines.append(
            _text(brief, "未记录额外的研究局限。", "No additional limitations were recorded.")
        )
    lines.append("")
    return "\n".join(lines)


def claim_status_counts(claims: list[dict]) -> dict[str, int]:
    summary = {"total": len(claims)}
    for claim in claims:
        key = str(claim.get("status", "")).lower()
        summary[key] = summary.get(key, 0) + 1
    return summary


def persist_report_artifacts(
    *,
    storage,
    session,
    project_id: UUID,
    run_id: UUID,
    report: str,
    reference_list: str,
    matrix_json: str,
    verification_json: str,
    force_new_version: bool = False,
) -> list:
    repo = ArtifactRepository(session)
    existing = {
        item.artifact_type: item
        for item in repo.list_for_run(project_id, run_id)
    }
    artifacts = []
    for artifact_type, title, content, extension, mime_type in [
        (ArtifactType.RESEARCH_REPORT, "Research report", report, "md", "text/markdown"),
        (
            ArtifactType.REFERENCE_LIST,
            "Reference list",
            reference_list,
            "md",
            "text/markdown",
        ),
        (
            ArtifactType.MARKDOWN_EXPORT,
            "Markdown export",
            report,
            "md",
            "text/markdown",
        ),
        (
            ArtifactType.EVIDENCE_MATRIX,
            "Evidence matrix",
            matrix_json,
            "json",
            "application/json",
        ),
        (
            ArtifactType.CITATION_AUDIT,
            "Evidence and claim verification",
            verification_json,
            "json",
            "application/json",
        ),
    ]:
        artifacts.append(
            _persist_artifact(
                storage,
                session,
                repo,
                existing.get(artifact_type),
                artifact_type,
                title,
                content,
                project_id=project_id,
                run_id=run_id,
                extension=extension,
                mime_type=mime_type,
                force_new_version=force_new_version,
            )
        )
    return artifacts


def _persist_artifact(
    storage,
    session,
    repo: ArtifactRepository,
    latest,
    artifact_type: ArtifactType,
    title: str,
    content: str,
    *,
    project_id: UUID,
    run_id: UUID,
    extension: str = "md",
    mime_type: str = "text/markdown",
    force_new_version: bool = False,
):
    payload = content.encode("utf-8")
    content_hash = text_sha256(content)
    if (
        not force_new_version
        and latest is not None
        and latest.content_hash == content_hash
        and storage.exists(latest.storage_key)
    ):
        return latest
    if force_new_version:
        version = (latest.version + 1) if latest else 1
    else:
        version = latest.version if latest and latest.content_hash == content_hash else (
            (latest.version + 1) if latest else 1
        )
    key = (
        f"projects/{project_id}/runs/{run_id}/artifacts/"
        f"{artifact_type.value}/v{version}.{extension}"
    )
    created_file = False
    if latest is None or latest.storage_key != key or not storage.exists(key):
        storage.save(key, payload)
        created_file = True
    try:
        return repo.register(
            project_id,
            run_id,
            ArtifactCreate(
                artifact_type=artifact_type,
                title=title,
                version=version,
                storage_key=key,
                mime_type=mime_type,
                size_bytes=len(payload),
                content_hash=content_hash,
                metadata={
                    "sections": content.count("\n## "),
                    "format": extension,
                    "generated_by": "phase8-workflow" if not force_new_version else "phase8-export",
                },
            ),
        )
    except Exception:
        if created_file:
            try:
                storage.delete(key)
            except Exception:
                pass
        raise


def _text(brief: ResearchBrief, zh: str, en: str) -> str:
    return zh if brief.language.value == "zh" else en
