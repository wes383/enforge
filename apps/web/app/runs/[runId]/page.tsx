"use client";

import {
  AlertTriangle,
  ArrowRight,
  BookOpenCheck,
  Check,
  FileText,
  Loader2,
  Radio,
  ScrollText,
  Search,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useRunEventStream, type StreamStatus } from "@/hooks/use-run-event-stream";
import { useRunWorkspace } from "@/components/run/run-workspace";
import { RunEventFeed } from "@/components/run/run-event-feed";
import { StageProgress } from "@/components/run/stage-progress";
import { ErrorState, Notice } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import {
  COVERAGE,
  coverageLabel,
  eventLabel,
  stageLabel,
  TERMINAL_RUN_STATUSES,
} from "@/lib/labels";
import { pick, useLocale } from "@/lib/locale";
import type {
  Approval,
  ApprovalListResponse,
  ResearchPlan,
  RunEvent,
} from "@/lib/types";
import { cn } from "@/lib/utils";

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

function latestPlan(approvals: Approval[]): {
  plan: ResearchPlan;
  approval: Approval;
} | null {
  const plans = approvals
    .filter((approval) => approval.kind === "plan")
    .sort((a, b) => {
      if (a.version !== b.version) return b.version - a.version;
      return Date.parse(b.created_at) - Date.parse(a.created_at);
    });
  for (const approval of plans) {
    const plan = planFromApproval(approval);
    if (plan) return { plan, approval };
  }
  return null;
}

const PHASE_EVENT_TYPES = new Set([
  "phase_started",
  "phase_completed",
  "phase_failed",
  "run_cancelled",
  "error",
]);

type TaskState = "completed" | "running" | "failed" | "neutral";

function phaseTasks(events: RunEvent[], active: boolean) {
  const relevant = events.filter((event) =>
    PHASE_EVENT_TYPES.has(event.event_type),
  );
  const states: TaskState[] = [];
  // Index of the newest start that no later event has resolved yet.
  let openIndex: number | null = null;

  relevant.forEach((event, index) => {
    let state: TaskState = "neutral";
    if (event.event_type === "phase_started") {
      if (openIndex !== null) states[openIndex] = "neutral";
      state = "running";
      openIndex = index;
    } else if (
      event.event_type === "phase_completed" ||
      event.event_type === "phase_failed" ||
      event.event_type === "error"
    ) {
      state =
        event.event_type === "phase_completed"
          ? "completed"
          : "failed";
      // The start row stops spinning once its own stage is resolved, or once the
      // run reaches its final stage because nothing can still be in flight.
      if (
        openIndex !== null &&
        (relevant[openIndex].stage === event.stage ||
          event.stage === "completed")
      ) {
        states[openIndex] = "neutral";
        openIndex = null;
      }
    }
    states.push(state);
  });

  if (openIndex !== null && !active) states[openIndex] = "neutral";

  return relevant
    .map((event, index) => ({ event, state: states[index] }))
    .slice(-10)
    .reverse();
}

function MetricCard({
  icon: Icon,
  label,
  value,
  detail,
}: {
  icon: typeof Search;
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-foreground-subtle">{label}</span>
        <Icon className="h-4 w-4 text-foreground-faint" />
      </div>
      <p className="mt-2 font-mono text-2xl font-medium tracking-tight">
        {value}
      </p>
      <p className="mt-1 text-[11px] leading-relaxed text-foreground-subtle">
        {detail}
      </p>
    </Card>
  );
}

function StreamBadge({ status }: { status: StreamStatus }) {
  const locale = useLocale();
  const copy =
    status === "live"
      ? pick(locale, "实时连接", "Live")
      : status === "reconnecting"
        ? pick(locale, "重连中", "Reconnecting")
        : status === "closed"
          ? pick(locale, "事件流已结束", "Stream closed")
          : pick(locale, "连接中", "Connecting");
  return (
    <Badge variant="outline" size="xs" className="gap-1.5 text-foreground-muted">
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          status === "live"
            ? "bg-green"
            : status === "reconnecting"
              ? "animate-pulse bg-orange"
              : "bg-foreground-faint",
        )}
      />
      {copy}
    </Badge>
  );
}

export default function RunOverviewPage() {
  const locale = useLocale();
  const { runId, overview, error, reload, registerRefresh } =
    useRunWorkspace();
  const run = overview?.run;
  const active = Boolean(run && !TERMINAL_RUN_STATUSES.has(run.status));

  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const refreshTimer = useRef<number | null>(null);

  const loadApprovals = useCallback(async () => {
    try {
      const response = await apiFetch<ApprovalListResponse>(
        `/runs/${runId}/approvals`,
      );
      setApprovals(response.items);
      setApprovalError(null);
    } catch (caught) {
      setApprovalError(
        caught instanceof ApiError ? caught.detail : String(caught),
      );
    }
  }, [runId]);

  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current !== null) {
      window.clearTimeout(refreshTimer.current);
    }
    refreshTimer.current = window.setTimeout(() => {
      void reload();
      void loadApprovals();
    }, 500);
  }, [loadApprovals, reload]);

  const stream = useRunEventStream({
    runId,
    active,
    onEvent: (event) => {
      if (
        event.event_type === "phase_completed" ||
        event.event_type === "phase_failed" ||
        event.event_type === "awaiting_approval" ||
        event.event_type === "approval_decided" ||
        event.event_type === "error" ||
        event.event_type === "run_cancelled" ||
        event.event_type === "citation_audit_failed" ||
        event.event_type === "section_generated"
      ) {
        scheduleRefresh();
      }
    },
  });

  useEffect(() => {
    void loadApprovals();
  }, [loadApprovals]);

  useEffect(
    () => registerRefresh(loadApprovals),
    [loadApprovals, registerRefresh],
  );

  useEffect(() => {
    if (!active) return;
    const interval = window.setInterval(() => void loadApprovals(), 8000);
    return () => window.clearInterval(interval);
  }, [active, loadApprovals]);

  useEffect(() => {
    if (!active || stream.status === "live") return;
    const interval = window.setInterval(() => void reload(), 4000);
    return () => window.clearInterval(interval);
  }, [active, reload, stream.status]);

  useEffect(() => {
    return () => {
      if (refreshTimer.current !== null) {
        window.clearTimeout(refreshTimer.current);
      }
    };
  }, []);

  const planInfo = useMemo(() => latestPlan(approvals), [approvals]);
  const coverageById = useMemo(
    () =>
      new Map((overview?.questions ?? []).map((item) => [item.id, item])),
    [overview?.questions],
  );
  const events = useMemo(
    () =>
      stream.events.length > 0
        ? stream.events
        : (overview?.latest_events ?? []),
    [overview?.latest_events, stream.events],
  );
  const tasks = useMemo(() => phaseTasks(events, active), [events, active]);
  const completedTasks = useMemo(
    () => events.filter((event) => event.event_type === "phase_completed").length,
    [events],
  );

  if (!overview || !run) {
    return error ? <ErrorState message={error} onRetry={() => void reload()} /> : null;
  }

  const { project } = overview;
  const source = overview.source_status;
  const search = overview.search_status;
  const pending = overview.pending_approval;
  const gaps = overview.gaps;
  // The latest approved plan version lists the questions this run actually
  // researches; coverage comes from the database rows linked by question id.
  const planQuestions = planInfo
    ? planInfo.plan.questions.map((question) => {
        const row = question.question_id
          ? coverageById.get(question.question_id)
          : undefined;
        return {
          id: row?.id ?? question.question_id ?? question.question,
          evidenceId: row?.id ?? question.question_id ?? null,
          question: question.question,
          priority: row?.priority ?? question.priority,
          coverage:
            row?.coverage_status ?? question.coverage_status ?? "open",
        };
      })
    : overview.questions.map((item) => ({
        id: item.id,
        evidenceId: item.id,
        question: item.question,
        priority: item.priority,
        coverage: item.coverage_status,
      }));

  return (
    <div className="flex flex-col gap-4">
      {run.status === "failed" && run.error_summary ? (
        <Notice
          variant="danger"
          title={pick(locale, "运行失败", "The run failed")}
        >
          {run.error_summary}
        </Notice>
      ) : null}
      {run.cancel_requested_at && !TERMINAL_RUN_STATUSES.has(run.status) ? (
        <Notice variant="warning" title={pick(locale, "已请求取消", "Cancellation requested")}>
          {pick(
            locale,
            "工作流将在当前任务结束后停止。",
            "The workflow will stop after the current task finishes.",
          )}
        </Notice>
      ) : null}
      {pending ? (
        <Notice
          variant="warning"
          title={
            pending.kind === "plan"
              ? pick(locale, "研究计划待审批", "Research plan awaiting approval")
              : pick(locale, "报告提纲待审批", "Outline awaiting approval")
          }
          action={
            <Button asChild variant="outline" size="sm">
              <Link href={`/runs/${runId}/approvals`}>
                {pick(locale, "审阅", "Review")}
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
          }
        >
          {pick(
            locale,
            "工作流已暂停，批准或修订后将继续执行。",
            "The workflow is paused. Approve or revise to continue.",
          )}
        </Notice>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="flex min-w-0 flex-col gap-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              icon={Search}
              label={pick(locale, "检索命中", "Search hits")}
              value={String(search.results)}
              detail={pick(
                locale,
                `${search.queries} 条查询 · ${search.rounds} 轮`,
                `${search.queries} queries · ${search.rounds} rounds`,
              )}
            />
            <MetricCard
              icon={BookOpenCheck}
              label={pick(locale, "来源", "Sources")}
              value={String(source.total)}
              detail={pick(
                locale,
                `全文 ${source.full_text} · 摘要 ${source.snippet_only} · 解析失败 ${source.parse_failed}`,
                `${source.full_text} full text · ${source.snippet_only} snippets · ${source.parse_failed} failed`,
              )}
            />
            <MetricCard
              icon={ScrollText}
              label={pick(locale, "证据 / 主张", "Evidence / claims")}
              value={`${overview.counts.evidence} / ${overview.counts.claims}`}
              detail={pick(
                locale,
                "证据条目与抽取的事实主张",
                "Evidence items and extracted claims",
              )}
            />
            <MetricCard
              icon={FileText}
              label={pick(locale, "章节 / 交付物", "Sections / artifacts")}
              value={`${overview.counts.sections} / ${overview.counts.artifacts}`}
              detail={pick(
                locale,
                "已生成章节与报告文件",
                "Generated sections and report files",
              )}
            />
          </div>

          <Card className="p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-display text-sm font-semibold tracking-tight">
                {pick(locale, "工作流阶段", "Workflow stages")}
              </h2>
              <div className="flex items-center gap-2">
                <StreamBadge status={stream.status} />
              </div>
            </div>
            <div className="mt-4">
              <StageProgress
                status={run.status}
                events={events}
                pendingApproval={pending}
              />
            </div>
          </Card>

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
            <Card className="p-5">
              <div className="flex items-center justify-between gap-2">
                <h2 className="font-display text-sm font-semibold tracking-tight">
                  {pick(locale, "任务记录", "Task log")}
                </h2>
                <span className="text-[11px] text-foreground-subtle">
                  {pick(
                    locale,
                    `${completedTasks} 条阶段完成记录`,
                    `${completedTasks} stage completions`,
                  )}
                </span>
              </div>
              {tasks.length === 0 ? (
                <p className="mt-6 text-sm text-foreground-subtle">
                  {pick(locale, "暂无任务记录。", "No task records yet.")}
                </p>
              ) : (
                <ol className="mt-3 divide-y divide-border">
                  {tasks.map(({ event, state }) => (
                    <li key={event.id} className="flex gap-2.5 py-2.5">
                      <span className="mt-0.5">
                        {state === "completed" ? (
                          <Check className="h-3.5 w-3.5 text-green-fg" />
                        ) : state === "running" ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-fg" />
                        ) : state === "failed" ? (
                          <AlertTriangle className="h-3.5 w-3.5 text-red-fg" />
                        ) : (
                          <Radio className="h-3.5 w-3.5 text-foreground-faint" />
                        )}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-medium">
                          {event.message ?? eventLabel(event.event_type, locale)}
                        </p>
                        <p className="mt-0.5 text-[10px] text-foreground-faint">
                          {event.stage
                            ? `${stageLabel(event.stage, locale)} · `
                            : ""}
                          {formatDateTime(event.created_at, locale)}
                        </p>
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </Card>

            <Card className="p-5">
              <div className="flex items-center justify-between gap-2">
                <h2 className="font-display text-sm font-semibold tracking-tight">
                  {pick(locale, "来源处理状态", "Source processing")}
                </h2>
                <Badge variant="secondary" size="xs">
                  {source.total}
                </Badge>
              </div>
              <dl className="mt-4 space-y-3 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <dt className="text-foreground-muted">
                    {pick(locale, "全文 / 摘要", "Full text / snippets")}
                  </dt>
                  <dd className="font-mono">
                    {source.full_text} / {source.snippet_only}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className="text-foreground-muted">
                    {pick(locale, "已解析版本", "Parsed versions")}
                  </dt>
                  <dd className="font-mono text-green-fg">{source.parsed}</dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className="text-foreground-muted">
                    {pick(locale, "解析中 / 待解析", "Parsing / pending")}
                  </dt>
                  <dd className="font-mono">{source.pending_parse}</dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className="text-foreground-muted">
                    {pick(locale, "解析失败", "Parse failures")}
                  </dt>
                  <dd
                    className={cn(
                      "font-mono",
                      source.parse_failed > 0 && "text-red-fg",
                    )}
                  >
                    {source.parse_failed}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-2 border-t border-border pt-3">
                  <dt className="text-foreground-muted">
                    {pick(locale, "检索查询", "Search queries")}
                  </dt>
                  <dd className="font-mono">
                    {search.completed}/{search.queries}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className="text-foreground-muted">
                    {pick(locale, "查询失败", "Query failures")}
                  </dt>
                  <dd className="font-mono">{search.failed}</dd>
                </div>
              </dl>
              <Button asChild variant="outline" size="sm" className="mt-4 w-full">
                <Link href={`/runs/${runId}/sources`}>
                  {pick(locale, "浏览来源", "Browse sources")}
                  <ArrowRight className="h-3.5 w-3.5" />
                </Link>
              </Button>
            </Card>
          </div>

          <Card className="p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-display text-sm font-semibold tracking-tight">
                {pick(locale, "研究计划与问题覆盖", "Research plan & coverage")}
              </h2>
              {planInfo ? (
                <div className="flex items-center gap-2">
                  <Badge variant="secondary" size="xs">
                    v{planInfo.plan.version}
                  </Badge>
                  <Badge
                    variant={
                      planInfo.approval.status === "modified"
                        ? "info"
                        : planInfo.approval.status === "approved"
                          ? "success"
                          : "warning"
                    }
                    size="xs"
                  >
                    {planInfo.approval.status === "modified"
                      ? pick(locale, "已修订批准", "Modified & approved")
                      : planInfo.approval.status === "approved"
                        ? pick(locale, "已批准", "Approved")
                        : pick(locale, "待审批", "Pending")}
                  </Badge>
                </div>
              ) : planQuestions.length > 0 ? (
                <span className="text-[11px] text-foreground-subtle">
                  {planQuestions.length}{" "}
                  {pick(locale, "个问题", "questions")}
                </span>
              ) : null}
            </div>
            {planInfo ? (
              <>
                <p className="mt-3 whitespace-pre-line text-sm leading-relaxed text-foreground-muted">
                  {planInfo.plan.summary}
                </p>
                {planInfo.plan.scope_notes ? (
                  <p className="mt-2 whitespace-pre-line border-l-2 border-border pl-3 text-xs leading-relaxed text-foreground-subtle">
                    {planInfo.plan.scope_notes}
                  </p>
                ) : null}
              </>
            ) : null}
            {planQuestions.length === 0 ? (
              approvalError ? (
                <p className="mt-3 text-xs text-red-fg">{approvalError}</p>
              ) : (
                <p className="mt-3 text-sm text-foreground-subtle">
                  {pick(
                    locale,
                    "研究计划与问题尚未生成。",
                    "The research plan and its questions have not been generated yet.",
                  )}
                </p>
              )
            ) : (
              <ol
                className={cn(
                  "divide-y divide-border",
                  planInfo ? "mt-4" : "mt-3",
                )}
              >
                {planQuestions.map((item, index) => (
                  <li key={item.id} className="flex items-start gap-3 py-2.5">
                    <span className="mt-0.5 w-6 shrink-0 font-mono text-[11px] text-foreground-faint">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    {item.evidenceId ? (
                      <Link
                        href={`/runs/${runId}/evidence?question=${item.evidenceId}`}
                        className="min-w-0 flex-1 text-sm leading-relaxed transition-colors hover:text-foreground-muted"
                      >
                        {item.question}
                      </Link>
                    ) : (
                      <span className="min-w-0 flex-1 text-sm leading-relaxed">
                        {item.question}
                      </span>
                    )}
                    <Badge
                      variant={
                        COVERAGE[item.coverage]?.variant ?? "secondary"
                      }
                      size="xs"
                      className="shrink-0"
                    >
                      {coverageLabel(item.coverage, locale)}
                    </Badge>
                    <span className="mt-0.5 shrink-0 font-mono text-[10px] text-foreground-faint">
                      P{item.priority}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <RunEventFeed events={events} status={stream.status} />
          <Card className="p-5">
            <div className="flex items-center justify-between gap-2">
              <h2 className="font-display text-sm font-semibold tracking-tight">
                {pick(locale, "覆盖缺口", "Coverage gaps")}
              </h2>
              <Badge
                variant={gaps.length > 0 ? "warning" : "secondary"}
                size="xs"
              >
                {gaps.length}
              </Badge>
            </div>
            {gaps.length === 0 ? (
              <p className="mt-3 text-sm text-foreground-subtle">
                {overview.counts.evidence === 0
                  ? pick(locale, "尚未构建证据。", "Evidence has not been built yet.")
                  : pick(
                      locale,
                      "所有研究问题均已被证据覆盖。",
                      "All research questions are covered by evidence.",
                    )}
              </p>
            ) : (
              <ul className="mt-3 space-y-2.5">
                {gaps.slice(0, 8).map((gap) => (
                  <li key={gap.question_id} className="flex items-start gap-2">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-orange-fg" />
                    <div className="min-w-0">
                      <p className="text-xs leading-relaxed">{gap.question}</p>
                      <Link
                        href={`/runs/${runId}/evidence?question=${gap.question_id}`}
                        className="mt-0.5 inline-block text-[10px] text-foreground-subtle hover:text-foreground"
                      >
                        {pick(locale, "查看相关证据", "View evidence")}
                      </Link>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card className="p-5">
            <h2 className="font-display text-sm font-semibold tracking-tight">
              {pick(locale, "项目参数", "Project parameters")}
            </h2>
            <dl className="mt-3 space-y-2 text-xs">
              <div className="flex justify-between gap-2">
                <dt className="text-foreground-subtle">
                  {pick(locale, "研究主题", "Topic")}
                </dt>
                <dd className="max-w-[220px] text-right text-foreground-muted">
                  {project.topic}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-foreground-subtle">
                  {pick(locale, "语言", "Language")}
                </dt>
                <dd>{project.language === "zh" ? "中文" : "English"}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-foreground-subtle">
                  {pick(locale, "目标字数", "Target words")}
                </dt>
                <dd>{project.target_words ?? pick(locale, "自动", "Auto")}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-foreground-subtle">
                  {pick(locale, "引用格式", "Citation style")}
                </dt>
                <dd>{project.citation_style.toUpperCase()}</dd>
              </div>
            </dl>
            <Button asChild variant="ghost" size="sm" className="mt-3 w-full">
              <Link href={`/projects/${project.id}`}>
                {pick(locale, "打开项目设置", "Open project settings")}
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
          </Card>
        </div>
      </div>
    </div>
  );
}
