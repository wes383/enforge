from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from dr_core.db.enums import ArtifactType
from dr_core.db.repositories import (
    ArtifactRepository,
    SourceRepository,
)
from dr_core.normalization import text_sha256
from dr_core.research.models import ResearchBrief
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
    sections,
    references: list[str],
) -> str:
    """Render the reader-facing article.

    Coverage tables, claim checks and citation audits stay in the verification
    artifacts and the workbench; mixing them into the article makes the export
    read like an internal report instead of a finished piece.
    """
    lines = [f"# {brief.title}", ""]
    for section in sections:
        content = section.content_markdown.rstrip()
        if content:
            lines.extend([content, ""])
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
