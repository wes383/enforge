"use client";

import {
  Check,
  ClipboardCheck,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useRunWorkspace } from "@/components/run/run-workspace";
import { EmptyState, ErrorState, LoadingState, Notice } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
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
  ResearchPlan,
} from "@/lib/types";

type PlanQuestionDraft = {
  question_id: string | null;
  question: string;
  priority: number;
  coverage_status: string;
  search_terms: string[];
};

type PlanDraft = {
  summary: string;
  scope_notes: string;
  questions: PlanQuestionDraft[];
};

type OutlineDraft = {
  title: string;
  summary: string;
  sections: OutlineSection[];
};

function planFromApproval(approval: Approval): ResearchPlan | null {
  const source =
    approval.status === "modified" && approval.decision_payload
      ? approval.decision_payload
      : approval.payload;
  if (
    source &&
    typeof source === "object" &&
    Array.isArray((source as { questions?: unknown }).questions)
  ) {
    return source as unknown as ResearchPlan;
  }
  return null;
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

function planDraftFrom(plan: ResearchPlan): PlanDraft {
  return {
    summary: plan.summary,
    scope_notes: plan.scope_notes ?? "",
    questions: plan.questions.map((question) => ({
      question_id: question.question_id ?? null,
      question: question.question,
      priority: question.priority,
      coverage_status: question.coverage_status ?? "open",
      search_terms: question.search_terms ?? [],
    })),
  };
}

function outlineDraftFrom(outline: OutlineContent): OutlineDraft {
  return {
    title: outline.title,
    summary: outline.summary,
    sections: outline.sections.map((section) => ({
      ...section,
      key_claims: [...section.key_claims],
    })),
  };
}

const DEFAULT_SECTION: OutlineSection = {
  index: 0,
  title: "",
  purpose: "",
  question_id: null,
  key_claims: [],
  evidence_ids: [],
  support_evidence_ids: [],
  refute_evidence_ids: [],
  target_words: 400,
  status: "planned",
};

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
  const [planDraft, setPlanDraft] = useState<PlanDraft | null>(null);
  const [outlineDraft, setOutlineDraft] = useState<OutlineDraft | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [outlineError, setOutlineError] = useState<string | null>(null);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const initializedPlan = useRef<string | null>(null);
  const initializedOutline = useRef<string | null>(null);
  const initialPlan = useRef<string | null>(null);
  const initialOutline = useRef<string | null>(null);

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
    if (!pending || pending.kind !== "plan") return;
    if (initializedPlan.current === pending.id) return;
    const plan = planFromApproval(pending);
    if (!plan) return;
    initializedPlan.current = pending.id;
    const draft = planDraftFrom(plan);
    initialPlan.current = JSON.stringify(draft);
    setPlanDraft(draft);
    setPlanError(null);
    setFeedback("");
    setFeedbackError(null);
  }, [pending]);

  useEffect(() => {
    if (!pending || pending.kind !== "outline") return;
    if (initializedOutline.current === pending.id) return;
    const content = outlineFromApproval(pending);
    if (!content) return;
    initializedOutline.current = pending.id;
    const draft = outlineDraftFrom(content);
    initialOutline.current = JSON.stringify(draft);
    setOutlineDraft(draft);
    setOutlineError(null);
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

  const latestPlan = useMemo(() => {
    const plans = [...(approvals ?? [])]
      .filter((approval) => approval.kind === "plan")
      .sort((a, b) => b.version - a.version);
    return (
      plans
        .map((approval) => {
          const plan = planFromApproval(approval);
          return plan ? { plan, approval } : null;
        })
        .find((value) => value !== null) ?? null
    );
  }, [approvals]);

  // Approving saves whatever is in the editor, so an untouched draft stays a
  // plain approval instead of bumping the plan/outline version.
  function approveDecision(approval: Approval): "approved" | "modified" {
    const draft = approval.kind === "plan" ? planDraft : outlineDraft;
    const initial =
      approval.kind === "plan" ? initialPlan.current : initialOutline.current;
    if (draft === null || initial === null) return "approved";
    return JSON.stringify(draft) === initial ? "approved" : "modified";
  }

  async function submitDecision(
    approval: Approval,
    decision: "approved" | "modified" | "rejected",
  ) {
    setActionError(null);
    setSuccess(null);
    setPlanError(null);
    setOutlineError(null);
    setFeedbackError(null);

    let content: unknown;
    if (decision === "modified") {
      if (approval.kind === "plan") {
        if (!planDraft) return;
        const validation = validatePlan(planDraft);
        if (validation) {
          setPlanError(validation);
          return;
        }
        content = {
          version: approval.version,
          summary: planDraft.summary.trim(),
          scope_notes: planDraft.scope_notes.trim() || null,
          questions: planDraft.questions.map((question) => ({
            question_id: question.question_id,
            question: question.question.trim(),
            priority: question.priority,
            coverage_status: question.coverage_status,
            search_terms: question.search_terms,
          })),
        };
      } else if (approval.kind === "outline") {
        if (!outlineDraft) return;
        const validation = validateOutline(outlineDraft);
        if (validation) {
          setOutlineError(validation);
          return;
        }
        content = {
          version: approval.version,
          title: outlineDraft.title.trim(),
          summary: outlineDraft.summary.trim(),
          sections: outlineDraft.sections.map((section, index) => ({
            ...section,
            index,
            title: section.title.trim(),
            purpose: section.purpose.trim(),
            key_claims: section.key_claims.filter((claim) => claim.trim()),
          })),
        };
      }
    }

    if (decision === "rejected" && !feedback.trim()) {
      setFeedbackError(
        pick(
          locale,
          "驳回时需要填写修改意见，工作流会据此生成下一版。",
          "Feedback is required when requesting a revision.",
        ),
      );
      return;
    }

    const label =
      decision === "rejected" ? reviseLabel : approveLabel;
    setSubmitting(label);
    try {
      await apiFetch(`/runs/${runId}/approvals/${approval.id}`, {
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
              "已提交修改意见，工作流将生成下一版。",
              "Revision requested. A new version will be generated.",
            )
          : pick(
              locale,
              "审批已提交，工作流继续执行。",
              "Decision submitted. The workflow is resuming.",
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

  function validatePlan(draft: PlanDraft): string | null {
    if (!draft.summary.trim()) {
      return pick(locale, "计划摘要不能为空。", "Plan summary is required.");
    }
    if (draft.questions.length === 0) {
      return pick(locale, "至少保留一个研究问题。", "Keep at least one question.");
    }
    if (draft.questions.some((question) => !question.question.trim())) {
      return pick(
        locale,
        "研究问题不能为空。",
        "Research questions cannot be empty.",
      );
    }
    const seen = new Set<string>();
    for (const question of draft.questions) {
      const key = question.question.trim().toLowerCase();
      if (seen.has(key)) {
        return pick(
          locale,
          "研究问题不能重复。",
          "Research questions must be unique.",
        );
      }
      seen.add(key);
      if (question.priority < 0 || question.priority > 1000) {
        return pick(
          locale,
          "优先级需在 0–1000 之间。",
          "Priority must be between 0 and 1000.",
        );
      }
    }
    return null;
  }

  function validateOutline(draft: OutlineDraft): string | null {
    if (!draft.title.trim()) {
      return pick(locale, "提纲标题不能为空。", "Outline title is required.");
    }
    if (!draft.summary.trim()) {
      return pick(locale, "提纲摘要不能为空。", "Outline summary is required.");
    }
    if (draft.sections.length === 0) {
      return pick(locale, "至少保留一个章节。", "Keep at least one section.");
    }
    for (const section of draft.sections) {
      if (!section.title.trim() || !section.purpose.trim()) {
        return pick(
          locale,
          "每个章节都需要标题与写作目的。",
          "Every section needs a title and purpose.",
        );
      }
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
                {pending.kind === "plan"
                  ? pick(locale, "审阅研究计划", "Review the research plan")
                  : pick(locale, "审阅报告提纲", "Review the report outline")}
              </h2>
              <p className="mt-1 text-sm text-foreground-muted">
                {pick(
                  locale,
                  "批准会保存这里的修改并继续执行；需要重新生成时填写意见后驳回。",
                  "Approving saves these edits and continues. Add feedback and reject to regenerate.",
                )}
              </p>
            </div>
          </div>

          {pending.kind === "plan" && planDraft ? (
            <div className="mt-5 flex flex-col gap-6">
              <div className="grid gap-4 lg:grid-cols-2">
                <Field label={pick(locale, "计划摘要", "Plan summary")}>
                  <Textarea
                    value={planDraft.summary}
                    onChange={(event) =>
                      setPlanDraft({ ...planDraft, summary: event.target.value })
                    }
                    rows={4}
                    maxLength={20000}
                  />
                </Field>
                <Field
                  label={pick(locale, "范围说明", "Scope notes")}
                  hint={pick(locale, "可选", "Optional")}
                >
                  <Textarea
                    value={planDraft.scope_notes}
                    onChange={(event) =>
                      setPlanDraft({
                        ...planDraft,
                        scope_notes: event.target.value,
                      })
                    }
                    rows={4}
                    maxLength={20000}
                  />
                </Field>
              </div>

              <div>
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-xs font-medium uppercase tracking-wide text-foreground-muted">
                    {pick(locale, "研究问题", "Research questions")}
                  </h3>
                  <Button
                    variant="outline"
                    size="xs"
                    onClick={() =>
                      setPlanDraft({
                        ...planDraft,
                        questions: [
                          ...planDraft.questions,
                          {
                            question_id: null,
                            question: "",
                            priority: 50,
                            coverage_status: "open",
                            search_terms: [],
                          },
                        ],
                      })
                    }
                  >
                    <Plus className="h-3 w-3" />
                    {pick(locale, "添加问题", "Add question")}
                  </Button>
                </div>
                <ol className="mt-3 space-y-2">
                  {planDraft.questions.map((question, index) => (
                    <li
                      key={`${question.question_id ?? "new"}-${index}`}
                      className="flex items-start gap-2"
                    >
                      <span className="mt-2.5 w-6 shrink-0 font-mono text-[11px] text-foreground-faint">
                        {String(index + 1).padStart(2, "0")}
                      </span>
                      <Textarea
                        value={question.question}
                        onChange={(event) => {
                          const questions = [...planDraft.questions];
                          questions[index] = {
                            ...question,
                            question: event.target.value,
                          };
                          setPlanDraft({ ...planDraft, questions });
                        }}
                        autoGrow
                        rows={1}
                        className="flex-1"
                        maxLength={10000}
                      />
                      <Input
                        type="number"
                        min={0}
                        max={1000}
                        value={question.priority}
                        onChange={(event) => {
                          const questions = [...planDraft.questions];
                          questions[index] = {
                            ...question,
                            priority: Number(event.target.value),
                          };
                          setPlanDraft({ ...planDraft, questions });
                        }}
                        className="mt-0 w-20 shrink-0"
                        aria-label={pick(locale, "优先级", "Priority")}
                      />
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="shrink-0 text-foreground-subtle"
                        onClick={() =>
                          setPlanDraft({
                            ...planDraft,
                            questions: planDraft.questions.filter(
                              (_, questionIndex) => questionIndex !== index,
                            ),
                          })
                        }
                        aria-label={pick(locale, "删除问题", "Remove question")}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </li>
                  ))}
                </ol>
                {planError ? (
                  <p className="mt-3 text-xs text-red-fg">{planError}</p>
                ) : null}
              </div>
            </div>
          ) : pending.kind === "outline" && outlineDraft ? (
            <div className="mt-5 flex flex-col gap-6">
              <div className="grid gap-4 lg:grid-cols-2">
                <Field label={pick(locale, "提纲标题", "Outline title")}>
                  <Input
                    value={outlineDraft.title}
                    onChange={(event) =>
                      setOutlineDraft({
                        ...outlineDraft,
                        title: event.target.value,
                      })
                    }
                    maxLength={1000}
                  />
                </Field>
                <Field label={pick(locale, "提纲摘要", "Outline summary")}>
                  <Textarea
                    value={outlineDraft.summary}
                    onChange={(event) =>
                      setOutlineDraft({
                        ...outlineDraft,
                        summary: event.target.value,
                      })
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
                      setOutlineDraft({
                        ...outlineDraft,
                        sections: [...outlineDraft.sections, { ...DEFAULT_SECTION }],
                      })
                    }
                  >
                    <Plus className="h-3 w-3" />
                    {pick(locale, "添加章节", "Add section")}
                  </Button>
                </div>
                <ol className="mt-3 space-y-3">
                  {outlineDraft.sections.map((section, index) => (
                    <li
                      key={`${section.index}-${index}`}
                      className="rounded-md border border-border p-3"
                    >
                      <div className="flex items-start gap-2">
                        <span className="mt-2.5 w-6 shrink-0 font-mono text-[11px] text-foreground-faint">
                          {String(index + 1).padStart(2, "0")}
                        </span>
                        <Input
                          value={section.title}
                          onChange={(event) => {
                            const sections = [...outlineDraft.sections];
                            sections[index] = {
                              ...section,
                              title: event.target.value,
                            };
                            setOutlineDraft({ ...outlineDraft, sections });
                          }}
                          placeholder={pick(locale, "章节标题", "Section title")}
                          maxLength={1000}
                        />
                        <Input
                          type="number"
                          min={50}
                          max={5000}
                          value={section.target_words}
                          onChange={(event) => {
                            const sections = [...outlineDraft.sections];
                            sections[index] = {
                              ...section,
                              target_words: Number(event.target.value),
                            };
                            setOutlineDraft({ ...outlineDraft, sections });
                          }}
                          className="w-24 shrink-0"
                          aria-label={pick(locale, "目标字数", "Target words")}
                        />
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          className="shrink-0 text-foreground-subtle"
                          onClick={() =>
                            setOutlineDraft({
                              ...outlineDraft,
                              sections: outlineDraft.sections.filter(
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
                            const sections = [...outlineDraft.sections];
                            sections[index] = {
                              ...section,
                              purpose: event.target.value,
                            };
                            setOutlineDraft({ ...outlineDraft, sections });
                          }}
                          placeholder={pick(locale, "写作目的", "Section purpose")}
                          rows={2}
                          maxLength={5000}
                        />
                        <Textarea
                          value={section.key_claims.join("\n")}
                          onChange={(event) => {
                            const sections = [...outlineDraft.sections];
                            sections[index] = {
                              ...section,
                              key_claims: event.target.value
                                .split("\n")
                                .map((claim) => claim.trim())
                                .filter(Boolean),
                            };
                            setOutlineDraft({ ...outlineDraft, sections });
                          }}
                          placeholder={pick(
                            locale,
                            "关键论点，每行一条",
                            "Key claims, one per line",
                          )}
                          rows={2}
                        />
                      </div>
                    </li>
                  ))}
                </ol>
                {outlineError ? (
                  <p className="mt-3 text-xs text-red-fg">{outlineError}</p>
                ) : null}
              </div>
            </div>
          ) : (
            <Notice variant="danger" className="mt-5" title={pick(locale, "审批内容无法解析", "Approval payload is invalid")}>
              {pick(
                locale,
                "请刷新页面，或通过 API 查看原始审批内容。",
                "Refresh the page or inspect the raw approval through the API.",
              )}
            </Notice>
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
                onClick={() => void submitDecision(pending, approveDecision(pending))}
                loading={submitting === approveLabel}
                disabled={submitting !== null}
              >
                <Check className="h-4 w-4" />
                {approveLabel}
              </Button>
              <Button
                variant="danger"
                onClick={() => void submitDecision(pending, "rejected")}
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
            "计划或提纲准备好后，工作流会暂停在此处等待审阅。",
            "When a plan or outline is ready, the workflow pauses here for review.",
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
              <li key={section.index} className="flex items-start gap-3 py-2.5">
                <span className="font-mono text-[11px] text-foreground-faint">
                  {String(section.index + 1).padStart(2, "0")}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{section.title}</p>
                  <p className="mt-0.5 text-xs leading-relaxed text-foreground-muted">
                    {section.purpose}
                  </p>
                </div>
                <span className="shrink-0 font-mono text-[10px] text-foreground-faint">
                  {section.target_words}w
                </span>
              </li>
            ))}
          </ol>
        </Card>
      ) : null}

      {latestPlan && !pending ? (
        <Card className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-display text-sm font-semibold tracking-tight">
              {pick(locale, "当前研究计划", "Current research plan")}
            </h2>
            <Badge variant="secondary" size="xs">
              v{latestPlan.plan.version}
            </Badge>
          </div>
          <p className="mt-2 text-sm leading-relaxed text-foreground-muted">
            {latestPlan.plan.summary}
          </p>
          <ol className="mt-3 divide-y divide-border">
            {latestPlan.plan.questions.map((question, index) => (
              <li key={`${question.question}-${index}`} className="flex gap-3 py-2.5">
                <span className="font-mono text-[11px] text-foreground-faint">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span className="text-sm leading-relaxed">{question.question}</span>
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
                  {approval.kind === "plan"
                    ? pick(locale, "研究计划", "Research plan")
                    : approval.kind === "outline"
                      ? pick(locale, "报告提纲", "Report outline")
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
