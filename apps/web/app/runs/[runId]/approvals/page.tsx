"use client";

import { Check, ClipboardCheck, Plus, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useRunWorkspace } from "@/components/run/run-workspace";
import { EmptyState, ErrorState, LoadingState, Notice } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, apiFetch } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { APPROVAL_STATUS, TERMINAL_RUN_STATUSES } from "@/lib/labels";
import { pick, useLocale } from "@/lib/locale";
import type {
  Approval,
  ApprovalListResponse,
  OutlineContent,
  OutlineSection,
  OutlineWorkspace,
} from "@/lib/types";

type OutlineDraft = {
  title: string;
  summary: string;
  sections: OutlineSection[];
};

const SECTION_ROLES = [
  "introduction",
  "body",
  "contradictions",
  "limitations",
  "conclusion",
] as const;

function roleLabel(role: string, locale: "zh" | "en"): string {
  const labels: Record<string, { zh: string; en: string }> = {
    introduction: { zh: "引言", en: "Introduction" },
    body: { zh: "主体", en: "Body" },
    contradictions: { zh: "争议与反方证据", en: "Disagreements" },
    limitations: { zh: "局限", en: "Limitations" },
    conclusion: { zh: "结论", en: "Conclusion" },
  };
  return labels[role] ? labels[role][locale] : role;
}

function outlineFromApproval(approval: Approval): OutlineContent | null {
  const source =
    approval.status === "modified" && approval.decision_payload
      ? approval.decision_payload
      : (approval.payload as { outline?: unknown }).outline;
  if (
    source &&
    typeof source === "object" &&
    Array.isArray((source as { sections?: unknown }).sections)
  ) {
    return source as unknown as OutlineContent;
  }
  return null;
}

function outlineDraftFrom(outline: OutlineContent): OutlineDraft {
  return {
    title: outline.title,
    summary: outline.summary,
    sections: outline.sections.map((section) => ({
      ...section,
      search_terms: [...section.search_terms],
      key_claims: [...section.key_claims],
      evidence_ids: [...section.evidence_ids],
      support_evidence_ids: [...section.support_evidence_ids],
      refute_evidence_ids: [...section.refute_evidence_ids],
    })),
  };
}

function newSection(index: number): OutlineSection {
  return {
    section_id: crypto.randomUUID(),
    index,
    role: "body",
    title: "",
    purpose: "",
    search_terms: [],
    key_claims: [],
    evidence_ids: [],
    support_evidence_ids: [],
    refute_evidence_ids: [],
    target_words: 400,
    coverage_status: "open",
    status: "planned",
  };
}

export default function ApprovalsPage() {
  const locale = useLocale();
  const {
    runId,
    overview,
    reload: reloadWorkspace,
    registerRefresh,
  } = useRunWorkspace();
  const [approvals, setApprovals] = useState<Approval[] | null>(null);
  const [outline, setOutline] = useState<OutlineWorkspace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [feedback, setFeedback] = useState("");
  const [draft, setDraft] = useState<OutlineDraft | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const initializedDraft = useRef<string | null>(null);
  const initialDraft = useRef<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [approvalData, outlineData] = await Promise.all([
        apiFetch<ApprovalListResponse>(`/runs/${runId}/approvals`),
        apiFetch<OutlineWorkspace>(`/runs/${runId}/outline`),
      ]);
      setApprovals(approvalData.items);
      setOutline(outlineData);
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

  useEffect(() => registerRefresh(refresh), [refresh, registerRefresh]);

  const active = Boolean(
    overview && !TERMINAL_RUN_STATUSES.has(overview.run.status),
  );
  const approveLabel = pick(locale, "批准", "Approve");
  const reviseLabel = pick(locale, "驳回并修订", "Request revision");

  useEffect(() => {
    if (!active) return;
    const interval = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(interval);
  }, [active, load]);

  const pending = useMemo(
    () => approvals?.find((approval) => approval.status === "pending") ?? null,
    [approvals],
  );

  const syncedPending = useRef<string | null>(null);
  useEffect(() => {
    if (!pending) return;
    if (pending.id === overview?.pending_approval?.id) return;
    if (syncedPending.current === pending.id) return;
    syncedPending.current = pending.id;
    void reloadWorkspace();
  }, [overview?.pending_approval?.id, pending, reloadWorkspace]);

  useEffect(() => {
    if (!pending || pending.kind !== "outline") return;
    if (initializedDraft.current === pending.id) return;
    const content = outlineFromApproval(pending);
    if (!content) return;
    initializedDraft.current = pending.id;
    const value = outlineDraftFrom(content);
    initialDraft.current = JSON.stringify(value);
    setDraft(value);
    setDraftError(null);
    setFeedback("");
    setFeedbackError(null);
  }, [pending]);

  const history = useMemo(
    () =>
      [...(approvals ?? [])].sort(
        (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
      ),
    [approvals],
  );

  function approveDecision(): "approved" | "modified" {
    if (draft === null || initialDraft.current === null) return "approved";
    return JSON.stringify(draft) === initialDraft.current
      ? "approved"
      : "modified";
  }

  async function submitDecision(decision: "approved" | "modified" | "rejected") {
    if (!pending) return;
    setActionError(null);
    setSuccess(null);
    setDraftError(null);
    setFeedbackError(null);

    let content: OutlineContent | undefined;
    if (decision === "modified") {
      if (!draft) return;
      const validation = validateOutline(draft);
      if (validation) {
        setDraftError(validation);
        return;
      }
      content = {
        version: pending.version,
        title: draft.title.trim(),
        summary: draft.summary.trim(),
        sections: draft.sections.map((section, index) => ({
          ...section,
          index,
          title: section.title.trim(),
          purpose: section.purpose.trim(),
          search_terms: section.search_terms
            .map((term) => term.trim())
            .filter(Boolean)
            .slice(0, 5),
          key_claims: section.key_claims.filter((claim) => claim.trim()),
        })),
      };
    }

    if (decision === "rejected" && !feedback.trim()) {
      setFeedbackError(
        pick(
          locale,
          "驳回时需要填写修改意见，工作流会据此生成下一版提纲。",
          "Feedback is required when requesting a revision.",
        ),
      );
      return;
    }

    const label = decision === "rejected" ? reviseLabel : approveLabel;
    setSubmitting(label);
    try {
      await apiFetch(`/runs/${runId}/approvals/${pending.id}`, {
        method: "POST",
        body: JSON.stringify({
          decision,
          feedback: feedback.trim() || null,
          content: decision === "modified" ? content : undefined,
        }),
      });
      setFeedback("");
      setSuccess(
        decision === "rejected"
          ? pick(
              locale,
              "已提交修改意见，工作流将生成下一版提纲。",
              "Revision requested. A new outline will be generated.",
            )
          : pick(
              locale,
              "审批已提交，工作流继续取证与写作。",
              "Decision submitted. Evidence collection resumes.",
            ),
      );
      await Promise.all([load(), reloadWorkspace()]);
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.detail : String(caught));
      await Promise.all([load(), reloadWorkspace()]);
    } finally {
      setSubmitting(null);
    }
  }

  function validateOutline(value: OutlineDraft): string | null {
    if (!value.title.trim()) {
      return pick(locale, "提纲标题不能为空。", "Outline title is required.");
    }
    if (!value.summary.trim()) {
      return pick(locale, "提纲摘要不能为空。", "Outline summary is required.");
    }
    if (value.sections.length === 0) {
      return pick(locale, "至少保留一个章节。", "Keep at least one section.");
    }
    const seen = new Set<string>();
    for (const section of value.sections) {
      if (!section.title.trim() || !section.purpose.trim()) {
        return pick(
          locale,
          "每个章节都需要标题与写作目的。",
          "Every section needs a title and purpose.",
        );
      }
      const key = section.title.trim().toLowerCase();
      if (seen.has(key)) {
        return pick(locale, "章节标题不能重复。", "Section titles must be unique.");
      }
      seen.add(key);
      if (section.target_words < 50 || section.target_words > 5000) {
        return pick(
          locale,
          "章节目标字数需在 50–5000 之间。",
          "Section target words must be between 50 and 5000.",
        );
      }
    }
    return null;
  }

  if (loading && approvals === null) {
    return <LoadingState label={pick(locale, "加载审批…", "Loading approvals…")} />;
  }
  if (error && approvals === null) {
    return <ErrorState message={error} onRetry={() => void refresh()} />;
  }

  return (
    <div className="flex flex-col gap-4">
      {success ? (
        <Notice variant="success" title={pick(locale, "已提交", "Submitted")}>
          {success}
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

      {pending ? (
        <Card className="p-5 lg:p-6">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="warning" size="xs" dot>
                  {pick(locale, "待审批", "Pending")}
                </Badge>
                <Badge variant="secondary" size="xs">
                  v{pending.version}
                </Badge>
                <span className="text-xs text-foreground-subtle">
                  {formatDateTime(pending.created_at, locale)}
                </span>
              </div>
              <h2 className="mt-2 font-display text-xl font-semibold tracking-tight">
                {pick(locale, "审阅文章提纲", "Review the article outline")}
              </h2>
              <p className="mt-1 text-sm text-foreground-muted">
                {pick(
                  locale,
                  "这是唯一的审批关口。批准后章节结构冻结，取证与写作按此提纲执行；需要重新生成时填写意见后驳回。",
                  "This is the only approval gate. Approving freezes the structure for evidence collection and writing; add feedback and reject to regenerate.",
                )}
              </p>
            </div>
          </div>

          {pending.kind !== "outline" || !draft ? (
            <Notice
              variant="danger"
              className="mt-5"
              title={pick(locale, "审批内容无法解析", "Approval payload is invalid")}
            >
              {pick(
                locale,
                "请刷新页面，或通过 API 查看原始审批内容。",
                "Refresh the page or inspect the raw approval through the API.",
              )}
            </Notice>
          ) : (
            <div className="mt-5 flex flex-col gap-6">
              <div className="grid gap-4 lg:grid-cols-2">
                <Field label={pick(locale, "文章标题", "Article title")}>
                  <Input
                    value={draft.title}
                    onChange={(event) =>
                      setDraft({ ...draft, title: event.target.value })
                    }
                    maxLength={1000}
                  />
                </Field>
                <Field label={pick(locale, "提纲摘要", "Outline summary")}>
                  <Textarea
                    value={draft.summary}
                    onChange={(event) =>
                      setDraft({ ...draft, summary: event.target.value })
                    }
                    rows={3}
                    maxLength={20000}
                  />
                </Field>
              </div>

              <div>
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-xs font-medium uppercase tracking-wide text-foreground-muted">
                    {pick(locale, "章节", "Sections")}
                  </h3>
                  <Button
                    variant="outline"
                    size="xs"
                    onClick={() =>
                      setDraft({
                        ...draft,
                        sections: [
                          ...draft.sections,
                          newSection(draft.sections.length),
                        ],
                      })
                    }
                  >
                    <Plus className="h-3 w-3" />
                    {pick(locale, "添加章节", "Add section")}
                  </Button>
                </div>
                <ol className="mt-3 space-y-3">
                  {draft.sections.map((section, index) => (
                    <li
                      key={section.section_id}
                      className="rounded-md border border-border p-3"
                    >
                      <div className="flex items-start gap-2">
                        <span className="mt-2.5 w-6 shrink-0 font-mono text-[11px] text-foreground-faint">
                          {String(index + 1).padStart(2, "0")}
                        </span>
                        <Input
                          value={section.title}
                          onChange={(event) => {
                            const sections = [...draft.sections];
                            sections[index] = {
                              ...section,
                              title: event.target.value,
                            };
                            setDraft({ ...draft, sections });
                          }}
                          placeholder={pick(locale, "章节标题", "Section title")}
                          maxLength={1000}
                        />
                        <Select
                          value={section.role}
                          onChange={(event) => {
                            const sections = [...draft.sections];
                            sections[index] = {
                              ...section,
                              role: event.target.value as OutlineSection["role"],
                            };
                            setDraft({ ...draft, sections });
                          }}
                          className="w-32 shrink-0"
                          aria-label={pick(locale, "章节角色", "Section role")}
                        >
                          {SECTION_ROLES.map((role) => (
                            <option key={role} value={role}>
                              {roleLabel(role, locale)}
                            </option>
                          ))}
                        </Select>
                        <Input
                          type="number"
                          min={50}
                          max={5000}
                          value={section.target_words}
                          onChange={(event) => {
                            const sections = [...draft.sections];
                            sections[index] = {
                              ...section,
                              target_words: Number(event.target.value),
                            };
                            setDraft({ ...draft, sections });
                          }}
                          className="w-24 shrink-0"
                          aria-label={pick(locale, "目标字数", "Target words")}
                        />
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          className="shrink-0 text-foreground-subtle"
                          onClick={() =>
                            setDraft({
                              ...draft,
                              sections: draft.sections.filter(
                                (_, sectionIndex) => sectionIndex !== index,
                              ),
                            })
                          }
                          aria-label={pick(locale, "删除章节", "Remove section")}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                      <div className="mt-2 grid gap-2 pl-8 lg:grid-cols-2">
                        <Textarea
                          value={section.purpose}
                          onChange={(event) => {
                            const sections = [...draft.sections];
                            sections[index] = {
                              ...section,
                              purpose: event.target.value,
                            };
                            setDraft({ ...draft, sections });
                          }}
                          placeholder={pick(locale, "写作目的", "Section purpose")}
                          rows={2}
                          maxLength={5000}
                        />
                        <Textarea
                          value={section.search_terms.join("\n")}
                          onChange={(event) => {
                            const sections = [...draft.sections];
                            sections[index] = {
                              ...section,
                              search_terms: event.target.value
                                .split("\n")
                                .map((term) => term.trim())
                                .filter(Boolean)
                                .slice(0, 5),
                            };
                            setDraft({ ...draft, sections });
                          }}
                          placeholder={pick(
                            locale,
                            "检索词，每行一个（最多 5 个）",
                            "Search terms, one per line (max 5)",
                          )}
                          rows={2}
                          maxLength={1000}
                        />
                      </div>
                    </li>
                  ))}
                </ol>
                {draftError ? (
                  <p className="mt-3 text-xs text-red-fg">{draftError}</p>
                ) : null}
              </div>
            </div>
          )}

          <div className="mt-6 rounded-md border border-border bg-background/60 p-4">
            <Field
              label={pick(locale, "审批意见", "Feedback")}
              hint={pick(
                locale,
                "驳回时必填；批准时可留空。",
                "Required when requesting a revision; optional otherwise.",
              )}
              error={feedbackError ?? undefined}
            >
              <Textarea
                value={feedback}
                onChange={(event) => {
                  setFeedback(event.target.value);
                  if (feedbackError) setFeedbackError(null);
                }}
                rows={3}
                maxLength={20000}
                placeholder={pick(
                  locale,
                  "说明需要调整的范围、证据或章节结构…",
                  "Describe changes to scope, evidence, or structure…",
                )}
              />
            </Field>
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Button
                onClick={() => void submitDecision(approveDecision())}
                loading={submitting === approveLabel}
                disabled={submitting !== null}
              >
                <Check className="h-4 w-4" />
                {approveLabel}
              </Button>
              <Button
                variant="danger"
                onClick={() => void submitDecision("rejected")}
                loading={submitting === reviseLabel}
                disabled={submitting !== null}
              >
                <X className="h-4 w-4" />
                {reviseLabel}
              </Button>
            </div>
          </div>
        </Card>
      ) : (
        <EmptyState
          icon={<ClipboardCheck className="h-5 w-5" />}
          title={pick(locale, "当前没有待审批项", "No approval pending")}
          description={pick(
            locale,
            "提纲准备好后，工作流会暂停在此处等待审阅。",
            "When the outline is ready, the workflow pauses here for review.",
          )}
          action={
            <Button asChild variant="outline" size="sm">
              <Link href={`/runs/${runId}`}>
                {pick(locale, "返回运行概览", "Back to overview")}
              </Link>
            </Button>
          }
        />
      )}

      {!pending && outline?.outline ? (
        <Card className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-display text-sm font-semibold tracking-tight">
              {pick(locale, "当前提纲", "Current outline")}
            </h2>
            <div className="flex items-center gap-2">
              <Badge variant="secondary" size="xs">
                v{outline.version}
              </Badge>
              <Badge
                variant={outline.status === "approved" ? "success" : "warning"}
                size="xs"
              >
                {outline.status === "approved"
                  ? pick(locale, "已批准", "Approved")
                  : outline.status}
              </Badge>
            </div>
          </div>
          <p className="mt-2 font-display text-base font-semibold">
            {outline.outline.title}
          </p>
          <p className="mt-1 text-sm text-foreground-muted">
            {outline.outline.summary}
          </p>
          <ol className="mt-4 divide-y divide-border">
            {outline.outline.sections.map((section) => (
              <li
                key={section.section_id}
                className="flex items-start gap-3 py-2.5"
              >
                <span className="font-mono text-[11px] text-foreground-faint">
                  {String(section.index + 1).padStart(2, "0")}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">
                    {section.title}
                    <span className="ml-2 text-[10px] font-normal uppercase tracking-wide text-foreground-faint">
                      {roleLabel(section.role, locale)}
                    </span>
                  </p>
                  <p className="mt-0.5 text-xs leading-relaxed text-foreground-muted">
                    {section.purpose}
                  </p>
                  {section.search_terms.length > 0 ? (
                    <p className="mt-1 font-mono text-[10px] text-foreground-faint">
                      {section.search_terms.join(" · ")}
                    </p>
                  ) : null}
                </div>
                <span className="shrink-0 font-mono text-[10px] text-foreground-faint">
                  {section.target_words}w
                </span>
              </li>
            ))}
          </ol>
        </Card>
      ) : null}

      <Card className="overflow-hidden">
        <div className="border-b border-border p-5">
          <h2 className="font-display text-sm font-semibold tracking-tight">
            {pick(locale, "审批历史", "Approval history")}
          </h2>
        </div>
        {history.length === 0 ? (
          <p className="p-5 text-sm text-foreground-subtle">
            {pick(locale, "暂无审批记录。", "No approvals yet.")}
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {history.map((approval) => (
              <li key={approval.id} className="flex flex-wrap items-center gap-3 p-4">
                <Badge
                  variant={APPROVAL_STATUS[approval.status]?.variant ?? "secondary"}
                  size="xs"
                >
                  {locale === "zh"
                    ? APPROVAL_STATUS[approval.status]?.label.zh ?? approval.status
                    : APPROVAL_STATUS[approval.status]?.label.en ?? approval.status}
                </Badge>
                <span className="text-sm font-medium">
                  {approval.kind === "outline"
                    ? pick(locale, "文章提纲", "Article outline")
                    : approval.kind}
                </span>
                <span className="font-mono text-[11px] text-foreground-subtle">
                  v{approval.version}
                </span>
                <span className="text-xs text-foreground-subtle">
                  {formatDateTime(approval.created_at, locale)}
                  {approval.decided_at
                    ? ` → ${formatDateTime(approval.decided_at, locale)}`
                    : ""}
                </span>
                {approval.feedback ? (
                  <p className="w-full text-xs leading-relaxed text-foreground-muted">
                    {approval.feedback}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
