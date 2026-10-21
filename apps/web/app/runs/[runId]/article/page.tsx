"use client";

import {
  ArrowUpRight,
  ClipboardCheck,
  Download,
  Eye,
  FileText,
  Pencil,
  RefreshCw,
  Save,
  ScrollText,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Markdown } from "@/components/markdown";
import { useRunWorkspace } from "@/components/run/run-workspace";
import { EmptyState, ErrorState, LoadingState, Notice } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, apiFetch, artifactDownloadUrl } from "@/lib/api";
import {
  formatBytes,
  formatDateTime,
  formatLocator,
  shortId,
  truncate,
} from "@/lib/format";
import {
  ARTIFACT_TYPES,
  artifactTypeLabel,
  claimStatusLabel,
  claimStatusVariant,
  EVIDENCE_TYPE,
  evidenceTypeLabel,
  evidenceVerificationLabel,
  evidenceVerificationVariant,
  generationStatusLabel,
  generationStatusVariant,
  sectionAuditLabel,
  sectionAuditVariant,
  TERMINAL_RUN_STATUSES,
} from "@/lib/labels";
import { pick, useLocale } from "@/lib/locale";
import type {
  Artifact,
  ArtifactListResponse,
  Claim,
  ClaimListResponse,
  DraftSection,
  Evidence,
  EvidenceListResponse,
  ExportResponse,
  SectionListResponse,
  VerificationPayload,
} from "@/lib/types";
import { cn } from "@/lib/utils";

type PanelTab = "evidence" | "claims" | "audit";

function latestArtifact(artifacts: Artifact[], type: string): Artifact | null {
  const matches = artifacts.filter((item) => item.artifact_type === type);
  if (matches.length === 0) return null;
  return matches.reduce((latest, item) =>
    item.version > latest.version ? item : latest,
  );
}

function summarizeArtifacts(artifacts: Artifact[]): Artifact[] {
  return Object.keys(ARTIFACT_TYPES)
    .map((type) => latestArtifact(artifacts, type))
    .filter((item): item is Artifact => item !== null);
}

function wordCount(text: string): number {
  const cjk = (text.match(/[\u3400-\u4dbf\u4e00-\u9fff]/g) ?? []).length;
  const latin = (text.replace(/[\u3400-\u4dbf\u4e00-\u9fff]/g, " ").match(/[A-Za-z0-9][A-Za-z0-9'-]*/g) ?? [])
    .length;
  return cjk + latin;
}

function citationMarkers(content: string): string[] {
  const found = new Set<string>();
  for (const match of content.matchAll(/\[S(\d+)\]/g)) {
    found.add(`S${match[1]}`);
  }
  return [...found];
}

const CLAIM_SORT: Record<string, number> = {
  CONTRADICTED: 0,
  UNSUPPORTED: 1,
  PARTIAL: 2,
  UNVERIFIABLE: 3,
  PENDING: 4,
  SUPPORTED: 5,
};

export default function ArticlePage() {
  const locale = useLocale();
  const { runId, overview } = useRunWorkspace();
  const [sections, setSections] = useState<DraftSection[] | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [verification, setVerification] = useState<VerificationPayload | null>(
    null,
  );
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [mode, setMode] = useState<"edit" | "preview">("preview");
  const [panelTab, setPanelTab] = useState<PanelTab>("evidence");
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [showAllClaims, setShowAllClaims] = useState(false);

  const terminal = overview
    ? TERMINAL_RUN_STATUSES.has(overview.run.status)
    : false;

  const load = useCallback(async () => {
    try {
      const [sectionData, evidenceData, claimData, verificationData, artifactData] =
        await Promise.all([
          apiFetch<SectionListResponse>(`/runs/${runId}/sections`),
          apiFetch<EvidenceListResponse>(`/runs/${runId}/evidence?limit=500`),
          apiFetch<ClaimListResponse>(`/runs/${runId}/claims`),
          apiFetch<VerificationPayload>(`/runs/${runId}/verification`),
          apiFetch<ArtifactListResponse>(`/runs/${runId}/artifacts`),
        ]);
      setSections(sectionData.items);
      setEvidence(evidenceData.items);
      setClaims(claimData.items);
      setVerification(verificationData);
      setArtifacts(artifactData.items);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : String(caught));
    } finally {
      setLoading(false);
    }
  }, [runId]);

  const refresh = useCallback(async () => {
    setLoading(true);
    await load();
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!sections || sections.length === 0 || selectedId) return;
    const first = sections[0];
    setSelectedId(first.id);
    setTitle(first.title);
    setContent(first.content_markdown);
  }, [sections, selectedId]);

  const selectedSection = useMemo(
    () => sections?.find((section) => section.id === selectedId) ?? null,
    [sections, selectedId],
  );

  const evidenceBySource = useMemo(() => {
    const map = new Map<string, Evidence[]>();
    for (const item of evidence) {
      const list = map.get(item.source_id) ?? [];
      list.push(item);
      map.set(item.source_id, list);
    }
    return map;
  }, [evidence]);

  const evidenceById = useMemo(
    () => new Map(evidence.map((item) => [item.id, item])),
    [evidence],
  );

  const citedSources = useMemo(() => {
    if (!selectedSection) return [];
    const bySource = new Map<string, string[]>();
    for (const [marker, sourceId] of Object.entries(
      selectedSection.citation_map,
    )) {
      const list = bySource.get(sourceId) ?? [];
      list.push(marker);
      bySource.set(sourceId, list);
    }
    return [...bySource.entries()].map(([sourceId, markers]) => {
      const first = evidenceBySource.get(sourceId)?.[0];
      return {
        sourceId,
        markers,
        title: first?.source_title ?? `Source ${shortId(sourceId)}`,
        evidenceCount: evidenceBySource.get(sourceId)?.length ?? 0,
      };
    });
  }, [evidenceBySource, selectedSection]);

  const selectedSource = citedSources.find(
    (source) => source.sourceId === selectedSourceId,
  );

  const pendingClaims = useMemo(
    () =>
      [...claims]
        .filter((claim) => claim.verification_status !== "SUPPORTED")
        .sort(
          (a, b) =>
            (CLAIM_SORT[a.verification_status] ?? 9) -
            (CLAIM_SORT[b.verification_status] ?? 9),
        ),
    [claims],
  );

  const visibleClaims = showAllClaims ? claims : pendingClaims;
  const latestArtifacts = useMemo(() => summarizeArtifacts(artifacts), [artifacts]);
  const markdownArtifact =
    latestArtifact(artifacts, "markdown_export") ??
    latestArtifact(artifacts, "research_report");
  const referenceArtifact = latestArtifact(artifacts, "reference_list");

  const unknownMarkers = useMemo(() => {
    if (!selectedSection) return [];
    return citationMarkers(content).filter(
      (marker) => !(marker in selectedSection.citation_map),
    );
  }, [content, selectedSection]);

  function chooseSection(section: DraftSection) {
    if (section.id === selectedId) return;
    if (
      dirty &&
      !window.confirm(
        pick(
          locale,
          "当前章节有未保存的修改，切换后将丢失。继续？",
          "This section has unsaved changes. Discard them and switch?",
        ),
      )
    ) {
      return;
    }
    setSelectedId(section.id);
    setTitle(section.title);
    setContent(section.content_markdown);
    setDirty(false);
    setSaveError(null);
    setMode("preview");
    setSelectedSourceId(null);
    setNotice(null);
  }

  async function saveSection() {
    if (!selectedSection) return;
    setSaveError(null);
    setActionError(null);
    setNotice(null);
    if (!title.trim()) {
      setSaveError(pick(locale, "章节标题不能为空。", "Section title is required."));
      return;
    }
    if (!content.trim()) {
      setSaveError(pick(locale, "章节内容不能为空。", "Section content is required."));
      return;
    }
    if (unknownMarkers.length > 0) {
      setSaveError(
        pick(
          locale,
          `存在未映射的引用标记：${unknownMarkers.join(", ")}。请删除或改用已有标记。`,
          `Unmapped citation markers: ${unknownMarkers.join(", ")}. Remove them or use an existing marker.`,
        ),
      );
      return;
    }
    setSaving(true);
    try {
      const updated = await apiFetch<DraftSection>(
        `/runs/${runId}/sections/${selectedSection.id}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            title: title.trim(),
            content_markdown: content,
          }),
        },
      );
      setSections((current) =>
        current
          ? current.map((section) =>
              section.id === selectedSection.id ? updated : section,
            )
          : current,
      );
      setSelectedId(updated.id);
      setTitle(updated.title);
      setContent(updated.content_markdown);
      setDirty(false);
      setNotice(
        pick(
          locale,
          `已保存为第 ${updated.version} 版。导出生效前需重新核验。`,
          `Saved as version ${updated.version}. Export to re-run verification.`,
        ),
      );
    } catch (caught) {
      setSaveError(caught instanceof ApiError ? caught.detail : String(caught));
    } finally {
      setSaving(false);
    }
  }

  async function exportReport() {
    if (dirty) {
      setActionError(
        pick(
          locale,
          "请先保存当前章节，再生成最终报告。",
          "Save the current section before generating the final report.",
        ),
      );
      return;
    }
    setExporting(true);
    setActionError(null);
    setNotice(null);
    try {
      await apiFetch<ExportResponse>(`/runs/${runId}/export`, {
        method: "POST",
      });
      await load();
      setPanelTab("audit");
      setNotice(
        pick(
          locale,
          "已重新生成报告、参考文献与核验交付物。",
          "Report, references, and verification artifacts were regenerated.",
        ),
      );
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.detail : String(caught));
    } finally {
      setExporting(false);
    }
  }

  if (loading && sections === null) {
    return <LoadingState label={pick(locale, "加载长文…", "Loading article…")} />;
  }
  if (error && sections === null) {
    return <ErrorState message={error} onRetry={() => void refresh()} />;
  }

  return (
    <div className="flex flex-col gap-4">
      {notice ? (
        <Notice variant="success" title={pick(locale, "已更新", "Updated")}>
          {notice}
        </Notice>
      ) : null}
      {actionError ? (
        <Notice variant="danger" title={pick(locale, "操作失败", "Action failed")}>
          {actionError}
        </Notice>
      ) : null}
      {error ? (
        <Notice variant="warning" title={pick(locale, "刷新失败", "Refresh failed")}>
          {error}
        </Notice>
      ) : null}
      {!terminal ? (
        <Notice variant="info" title={pick(locale, "运行尚未结束", "The run is still active")}>
          {pick(
            locale,
            "当前可预览已生成章节；运行完成后才能编辑、保存新版本并导出报告。",
            "You can preview generated sections now. Editing, saving versions, and exporting are enabled after the run finishes.",
          )}
        </Notice>
      ) : null}

      <Card className="flex flex-col gap-3 p-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Badge variant="secondary" size="xs">
            {sections?.length ?? 0} {pick(locale, "个章节", "sections")}
          </Badge>
          {selectedSection ? (
            <>
              <Badge variant="outline" size="xs">
                {pick(locale, "第 ", "Section ")}
                {selectedSection.section_index + 1}
              </Badge>
              <Badge variant="secondary" size="xs">
                v{selectedSection.version}
              </Badge>
              <Badge
                variant={sectionAuditVariant(selectedSection.audit_status)}
                size="xs"
                dot
              >
                {sectionAuditLabel(selectedSection.audit_status, locale)}
              </Badge>
              {dirty ? (
                <Badge variant="warning" size="xs">
                  {pick(locale, "未保存", "Unsaved")}
                </Badge>
              ) : null}
            </>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
            <RefreshCw className={loading ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />
            {pick(locale, "刷新", "Refresh")}
          </Button>
          {markdownArtifact ? (
            <Button asChild variant="outline" size="sm">
              <a
                href={artifactDownloadUrl(runId, markdownArtifact.id)}
                target="_blank"
                rel="noreferrer"
              >
                <Download className="h-3.5 w-3.5" />
                {pick(locale, "下载 Markdown", "Download Markdown")}
              </a>
            </Button>
          ) : null}
          {referenceArtifact ? (
            <Button asChild variant="outline" size="sm">
              <a
                href={artifactDownloadUrl(runId, referenceArtifact.id)}
                target="_blank"
                rel="noreferrer"
              >
                <Download className="h-3.5 w-3.5" />
                {pick(locale, "下载参考文献", "Download references")}
              </a>
            </Button>
          ) : null}
          <Button
            size="sm"
            onClick={() => void exportReport()}
            loading={exporting}
            disabled={!terminal || !sections?.length || dirty}
          >
            <FileText className="h-4 w-4" />
            {pick(locale, "生成最终报告", "Generate final report")}
          </Button>
        </div>
      </Card>

      {!sections || sections.length === 0 ? (
        <EmptyState
          icon={<FileText className="h-5 w-5" />}
          title={pick(locale, "尚未生成章节", "No sections generated yet")}
          description={pick(
            locale,
            "提纲批准后，写作阶段会逐节生成长文。",
            "After the outline is approved, the writing stage generates sections.",
          )}
          action={
            <Button asChild variant="outline" size="sm">
              <Link href={`/runs/${runId}`}>
                {pick(locale, "返回运行概览", "Back to overview")}
              </Link>
            </Button>
          }
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,1fr)_370px]">
          <Card className="h-fit p-3 lg:sticky lg:top-24">
            <p className="px-2 pb-2 text-[11px] font-medium uppercase tracking-wide text-foreground-subtle">
              {pick(locale, "目录", "Contents")}
            </p>
            <ol className="space-y-0.5">
              {sections.map((section) => {
                const active = section.id === selectedId;
                return (
                  <li key={section.id}>
                    <button
                      type="button"
                      onClick={() => chooseSection(section)}
                      className={cn(
                        "flex w-full items-start gap-2 rounded-md px-2 py-2 text-left transition-colors",
                        active
                          ? "bg-hover-bg-strong text-foreground"
                          : "text-foreground-muted hover:bg-hover-bg hover:text-foreground",
                      )}
                    >
                      <span className="mt-0.5 font-mono text-[10px] text-foreground-faint">
                        {String(section.section_index + 1).padStart(2, "0")}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="line-clamp-2 text-xs leading-snug">
                          {section.title}
                        </span>
                        <span className="mt-1 flex items-center gap-1.5">
                          <span
                            className={cn(
                              "h-1.5 w-1.5 rounded-full",
                              section.audit_status === "passed"
                                ? "bg-green"
                                : section.audit_status === "failed"
                                  ? "bg-red"
                                  : section.audit_status === "warning"
                                    ? "bg-orange"
                                    : "bg-foreground-faint",
                            )}
                          />
                          <span className="text-[10px] text-foreground-faint">
                            {generationStatusLabel(
                              section.generation_status,
                              locale,
                            )}
                          </span>
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </Card>

          <Card className="flex min-w-0 flex-col">
            {selectedSection ? (
              <>
                <div className="flex flex-col gap-3 border-b border-border p-5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" size="xs">
                        v{selectedSection.version}
                      </Badge>
                      <Badge
                        variant={generationStatusVariant(
                          selectedSection.generation_status,
                        )}
                        size="xs"
                      >
                        {generationStatusLabel(
                          selectedSection.generation_status,
                          locale,
                        )}
                      </Badge>
                      <span className="text-[11px] text-foreground-subtle">
                        {pick(locale, "更新于 ", "Updated ")}
                        {formatDateTime(selectedSection.created_at, locale)}
                      </span>
                    </div>
                    <div className="flex items-center gap-1 rounded-full border border-border p-0.5">
                      <button
                        type="button"
                        onClick={() => setMode("edit")}
                        disabled={!terminal}
                        className={cn(
                          "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs transition-colors",
                          mode === "edit"
                            ? "bg-accent text-accent-fg"
                            : "text-foreground-muted hover:text-foreground",
                          !terminal && "cursor-not-allowed opacity-50",
                        )}
                      >
                        <Pencil className="h-3 w-3" />
                        {pick(locale, "编辑", "Edit")}
                      </button>
                      <button
                        type="button"
                        onClick={() => setMode("preview")}
                        className={cn(
                          "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs transition-colors",
                          mode === "preview"
                            ? "bg-accent text-accent-fg"
                            : "text-foreground-muted hover:text-foreground",
                        )}
                      >
                        <Eye className="h-3 w-3" />
                        {pick(locale, "预览", "Preview")}
                      </button>
                    </div>
                  </div>
                  {mode === "edit" ? (
                    <Input
                      value={title}
                      onChange={(event) => {
                        setTitle(event.target.value);
                        setDirty(true);
                      }}
                      maxLength={1000}
                      aria-label={pick(locale, "章节标题", "Section title")}
                      className="font-display text-lg font-semibold"
                    />
                  ) : (
                    <h2 className="font-display text-xl font-semibold tracking-tight">
                      {title || selectedSection.title}
                    </h2>
                  )}
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-foreground-subtle">
                    <span>
                      {wordCount(content)}{" "}
                      {pick(locale, "字词", "words")}
                    </span>
                    <span>
                      {citationMarkers(content).length}{" "}
                      {pick(locale, "个引用标记", "citation markers")}
                    </span>
                    {dirty ? (
                      <span className="text-orange-fg">
                        {pick(locale, "有未保存修改", "Unsaved changes")}
                      </span>
                    ) : null}
                  </div>
                </div>

                <div className="p-5">
                  {mode === "edit" ? (
                    <>
                      <Textarea
                        value={content}
                        onChange={(event) => {
                          setContent(event.target.value);
                          setDirty(true);
                        }}
                        rows={22}
                        className="min-h-[480px] font-sans text-sm leading-relaxed"
                        aria-label={pick(locale, "章节内容", "Section content")}
                      />
                      {unknownMarkers.length > 0 ? (
                        <p className="mt-2 text-xs text-red-fg">
                          {pick(
                            locale,
                            `未映射的引用标记：${unknownMarkers.join(", ")}`,
                            `Unmapped citation markers: ${unknownMarkers.join(", ")}`,
                          )}
                        </p>
                      ) : null}
                      {saveError ? (
                        <p className="mt-2 text-xs text-red-fg">{saveError}</p>
                      ) : null}
                      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                        <p className="text-[11px] text-foreground-subtle">
                          {pick(
                            locale,
                            "保存会创建新的章节版本，不会覆盖历史版本。",
                            "Saving creates a new section version without overwriting history.",
                          )}
                        </p>
                        <Button
                          onClick={() => void saveSection()}
                          loading={saving}
                          disabled={!dirty || !terminal}
                        >
                          <Save className="h-4 w-4" />
                          {pick(locale, "保存新版本", "Save new version")}
                        </Button>
                      </div>
                    </>
                  ) : (
                    <Markdown
                      source={content || selectedSection.content_markdown}
                      citationMap={selectedSection.citation_map}
                      onCitation={(marker, sourceId) => {
                        if (sourceId) {
                          setSelectedSourceId(sourceId);
                          setPanelTab("evidence");
                        } else {
                          setActionError(
                            pick(
                              locale,
                              `引用 ${marker} 没有对应的来源映射。`,
                              `Citation ${marker} has no mapped source.`,
                            ),
                          );
                        }
                      }}
                    />
                  )}
                </div>
              </>
            ) : (
              <p className="p-6 text-sm text-foreground-subtle">
                {pick(locale, "选择一个章节。", "Select a section.")}
              </p>
            )}
          </Card>

          <Card className="flex h-fit min-w-0 flex-col lg:col-span-2 xl:col-span-1 xl:sticky xl:top-24 xl:max-h-[calc(100vh-7rem)]">
            <div className="flex items-center gap-1 border-b border-border p-2">
              {(
                [
                  ["evidence", pick(locale, "证据", "Evidence"), ScrollText],
                  [
                    "claims",
                    pick(locale, `主张 ${pendingClaims.length}`, `Claims ${pendingClaims.length}`),
                    TriangleAlert,
                  ],
                  ["audit", pick(locale, "核验", "Audit"), ClipboardCheck],
                ] as const
              ).map(([key, label, Icon]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setPanelTab(key)}
                  className={cn(
                    "inline-flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors",
                    panelTab === key
                      ? "bg-hover-bg-strong text-foreground"
                      : "text-foreground-muted hover:bg-hover-bg",
                  )}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {label}
                </button>
              ))}
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              {panelTab === "evidence" ? (
                <div className="flex flex-col gap-3">
                  {selectedSource ? (
                    <div className="rounded-md border border-border p-3">
                      <div className="flex flex-wrap items-center gap-1.5">
                        {selectedSource.markers.map((marker) => (
                          <Badge key={marker} variant="info" size="xs">
                            {marker}
                          </Badge>
                        ))}
                        <span className="text-xs font-medium">
                          {selectedSource.title}
                        </span>
                      </div>
                      <p className="mt-1 text-[10px] text-foreground-subtle">
                        {selectedSource.evidenceCount}{" "}
                        {pick(locale, "条可用证据", "evidence items")}
                      </p>
                    </div>
                  ) : (
                    <div>
                      <p className="text-xs text-foreground-muted">
                        {pick(
                          locale,
                          "点击正文中的引用标记，查看对应证据。",
                          "Click a citation marker in the preview to inspect its evidence.",
                        )}
                      </p>
                      {citedSources.length > 0 ? (
                        <div className="mt-3 flex flex-wrap gap-1.5">
                          {citedSources.map((source) => (
                            <button
                              key={source.sourceId}
                              type="button"
                              onClick={() => setSelectedSourceId(source.sourceId)}
                              className="inline-flex max-w-full items-center gap-1 rounded-sm border border-border px-2 py-1 text-[11px] text-foreground-muted transition-colors hover:bg-hover-bg hover:text-foreground"
                            >
                              <span className="font-mono text-blue-fg">
                                {source.markers[0]}
                              </span>
                              <span className="truncate">{source.title}</span>
                            </button>
                          ))}
                        </div>
                      ) : (
                        <p className="mt-3 text-xs text-foreground-subtle">
                          {pick(
                            locale,
                            "本节没有引用来源。",
                            "This section has no cited sources.",
                          )}
                        </p>
                      )}
                    </div>
                  )}

                  {selectedSource
                    ? (evidenceBySource.get(selectedSource.sourceId) ?? []).map(
                        (item) => (
                          <div
                            key={item.id}
                            className="rounded-md border border-border p-3"
                          >
                            <div className="flex flex-wrap items-center gap-1.5">
                              <Badge
                                variant={
                                  EVIDENCE_TYPE[item.evidence_type]?.variant ??
                                  "secondary"
                                }
                                size="xs"
                              >
                                {evidenceTypeLabel(item.evidence_type, locale)}
                              </Badge>
                              <Badge
                                variant={evidenceVerificationVariant(
                                  item.verification_status,
                                )}
                                size="xs"
                              >
                                {evidenceVerificationLabel(
                                  item.verification_status,
                                  locale,
                                )}
                              </Badge>
                            </div>
                            <p className="mt-2 text-xs leading-relaxed text-foreground">
                              “{item.quoted_text}”
                            </p>
                            <div className="mt-2 flex flex-wrap items-center gap-1.5">
                              {formatLocator(item.locator).map((locator) => (
                                <span
                                  key={locator}
                                  className="font-mono text-[10px] text-foreground-subtle"
                                >
                                  {locator}
                                </span>
                              ))}
                              {item.source_version_number ? (
                                <span className="text-[10px] text-foreground-faint">
                                  v{item.source_version_number}
                                </span>
                              ) : null}
                            </div>
                            {item.source_url ? (
                              <a
                                href={item.source_url}
                                target="_blank"
                                rel="noreferrer"
                                className="mt-2 inline-flex items-center gap-1 text-[10px] text-foreground-subtle hover:text-foreground"
                              >
                                {pick(locale, "打开来源", "Open source")}
                                <ArrowUpRight className="h-2.5 w-2.5" />
                              </a>
                            ) : null}
                          </div>
                        ),
                      )
                    : null}
                </div>
              ) : panelTab === "claims" ? (
                <div className="flex flex-col gap-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs text-foreground-muted">
                      {pick(
                        locale,
                        `${pendingClaims.length} 条未完全通过`,
                        `${pendingClaims.length} not fully supported`,
                      )}
                    </p>
                    <button
                      type="button"
                      onClick={() => setShowAllClaims((value) => !value)}
                      className="text-[11px] text-foreground-subtle underline-offset-2 hover:text-foreground hover:underline"
                    >
                      {showAllClaims
                        ? pick(locale, "只看未通过", "Only flagged")
                        : pick(locale, "显示全部", "Show all")}
                    </button>
                  </div>
                  {visibleClaims.length === 0 ? (
                    <EmptyState
                      className="min-h-[140px] border-0 bg-transparent p-4"
                      title={
                        claims.length === 0
                          ? pick(locale, "尚无主张", "No claims yet")
                          : pick(locale, "全部主张均有支持", "All claims are supported")
                      }
                      description={
                        claims.length === 0
                          ? pick(locale, "写作完成后会抽取事实主张。", "Claims are extracted after writing.")
                          : undefined
                      }
                    />
                  ) : (
                    visibleClaims.map((claim) => (
                      <div
                        key={claim.id}
                        className="rounded-md border border-border p-3"
                      >
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Badge
                            variant={claimStatusVariant(claim.verification_status)}
                            size="xs"
                            dot
                          >
                            {claimStatusLabel(
                              claim.verification_status,
                              locale,
                            )}
                          </Badge>
                          <span className="text-[10px] text-foreground-faint">
                            {claim.claim_type}
                          </span>
                        </div>
                        <p className="mt-2 text-xs leading-relaxed">
                          {claim.claim_text}
                        </p>
                        {claim.evidence.length > 0 ? (
                          <ul className="mt-2 space-y-1.5 border-t border-border pt-2">
                            {claim.evidence.slice(0, 3).map((link) => {
                              const item = evidenceById.get(link.evidence_id);
                              return (
                                <li
                                  key={`${claim.id}-${link.evidence_id}`}
                                  className="text-[10px] leading-relaxed text-foreground-muted"
                                >
                                  <span className="font-mono text-foreground-faint">
                                    {link.relation}
                                  </span>{" "}
                                  {item
                                    ? truncate(item.quoted_text, 180)
                                    : (link.rationale ??
                                      truncate(link.evidence_id, 12))}
                                </li>
                              );
                            })}
                          </ul>
                        ) : null}
                      </div>
                    ))
                  )}
                </div>
              ) : (
                <div className="flex flex-col gap-4">
                  <section>
                    <h3 className="text-[11px] font-medium uppercase tracking-wide text-foreground-subtle">
                      {pick(locale, "引用核验", "Citation audit")}
                    </h3>
                    {verification?.available && verification.citation_audit ? (
                      <>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          <Badge variant="success" size="xs">
                            {verification.citation_audit.passed}{" "}
                            {pick(locale, "通过", "passed")}
                          </Badge>
                          <Badge variant="warning" size="xs">
                            {verification.citation_audit.warnings}{" "}
                            {pick(locale, "警告", "warnings")}
                          </Badge>
                          <Badge variant="danger" size="xs">
                            {verification.citation_audit.failed}{" "}
                            {pick(locale, "失败", "failed")}
                          </Badge>
                        </div>
                        {verification.citation_audit.issues.length > 0 ? (
                          <ul className="mt-2 space-y-1.5">
                            {verification.citation_audit.issues.map(
                              (issue, index) => (
                                <li
                                  key={`${issue.section_index}-${index}`}
                                  className="text-[10px] leading-relaxed text-foreground-muted"
                                >
                                  <span className="font-mono">
                                    §{issue.section_index + 1}
                                  </span>{" "}
                                  {issue.details}
                                </li>
                              ),
                            )}
                          </ul>
                        ) : null}
                        {verification.generated_at ? (
                          <p className="mt-2 text-[10px] text-foreground-faint">
                            {formatDateTime(verification.generated_at, locale)}
                          </p>
                        ) : null}
                      </>
                    ) : (
                      <p className="mt-2 text-xs text-foreground-subtle">
                        {pick(
                          locale,
                          "生成最终报告后显示整体核验结果。",
                          "Generate the final report to see the overall audit.",
                        )}
                      </p>
                    )}
                  </section>

                  <section className="border-t border-border pt-3">
                    <h3 className="text-[11px] font-medium uppercase tracking-wide text-foreground-subtle">
                      {pick(locale, "本节状态", "This section")}
                    </h3>
                    <div className="mt-2 flex items-center gap-2">
                      <Badge
                        variant={sectionAuditVariant(
                          selectedSection?.audit_status ?? null,
                        )}
                        size="xs"
                      >
                        {sectionAuditLabel(
                          selectedSection?.audit_status ?? null,
                          locale,
                        )}
                      </Badge>
                      {selectedSection?.audit_details.length ? (
                        <span className="text-[10px] text-foreground-subtle">
                          {selectedSection.audit_details[0]}
                        </span>
                      ) : null}
                    </div>
                  </section>

                  {verification?.reference_audit ? (
                    <section className="border-t border-border pt-3">
                      <h3 className="text-[11px] font-medium uppercase tracking-wide text-foreground-subtle">
                        {pick(locale, "参考文献", "References")}
                      </h3>
                      <ul className="mt-2 space-y-1 text-[10px] text-foreground-muted">
                        <li>
                          {pick(locale, "正文引用来源", "Cited sources")}:{" "}
                          {verification.reference_audit.cited_source_ids.length}
                        </li>
                        <li>
                          {pick(locale, "未引用证据来源", "Unused evidence sources")}:{" "}
                          {verification.reference_audit.unused_source_ids.length}
                        </li>
                        <li>
                          {pick(locale, "元数据不完整", "Incomplete metadata")}:{" "}
                          {
                            verification.reference_audit
                              .invalid_metadata_source_ids.length
                          }
                        </li>
                        <li>
                          {pick(locale, "悬空标记", "Dangling markers")}:{" "}
                          {verification.reference_audit.dangling_markers.length}
                        </li>
                      </ul>
                      {verification.reference_audit.issues.length > 0 ? (
                        <ul className="mt-2 space-y-1">
                          {verification.reference_audit.issues
                            .slice(0, 5)
                            .map((issue, index) => (
                              <li
                                key={index}
                                className="text-[10px] leading-relaxed text-foreground-subtle"
                              >
                                {issue}
                              </li>
                            ))}
                        </ul>
                      ) : null}
                    </section>
                  ) : null}

                  {verification?.claim_summary ? (
                    <section className="border-t border-border pt-3">
                      <h3 className="text-[11px] font-medium uppercase tracking-wide text-foreground-subtle">
                        {pick(locale, "主张汇总", "Claim summary")}
                      </h3>
                      <div className="mt-2 grid grid-cols-2 gap-1.5 text-[10px]">
                        {Object.entries(verification.claim_summary).map(
                          ([key, value]) => (
                            <div
                              key={key}
                              className="flex items-center justify-between gap-2 rounded-sm bg-hover-bg/60 px-2 py-1"
                            >
                              <span className="text-foreground-subtle">
                                {claimStatusLabel(key.toUpperCase(), locale) ||
                                  key}
                              </span>
                              <span className="font-mono">{value}</span>
                            </div>
                          ),
                        )}
                      </div>
                    </section>
                  ) : null}

                  <section className="border-t border-border pt-3">
                    <h3 className="text-[11px] font-medium uppercase tracking-wide text-foreground-subtle">
                      {pick(locale, "交付物", "Artifacts")}
                    </h3>
                    {latestArtifacts.length === 0 ? (
                      <p className="mt-2 text-xs text-foreground-subtle">
                        {pick(
                          locale,
                          "尚未生成报告文件。",
                          "No report artifacts yet.",
                        )}
                      </p>
                    ) : (
                      <ul className="mt-2 space-y-1.5">
                        {latestArtifacts.map((artifact) => (
                          <li
                            key={artifact.id}
                            className="flex items-center justify-between gap-2 rounded-md border border-border px-2.5 py-2"
                          >
                            <div className="min-w-0">
                              <p className="truncate text-[11px] font-medium">
                                {artifactTypeLabel(
                                  artifact.artifact_type,
                                  locale,
                                )}
                              </p>
                              <p className="text-[10px] text-foreground-faint">
                                v{artifact.version} ·{" "}
                                {formatBytes(artifact.size_bytes)}
                              </p>
                            </div>
                            <a
                              href={artifactDownloadUrl(runId, artifact.id)}
                              target="_blank"
                              rel="noreferrer"
                              className="shrink-0 text-foreground-subtle hover:text-foreground"
                              aria-label={pick(locale, "下载", "Download")}
                            >
                              <Download className="h-3.5 w-3.5" />
                            </a>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                </div>
              )}
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
