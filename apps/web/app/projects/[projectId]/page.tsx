"use client";

import {
  ArrowLeft,
  ArrowUpRight,
  CalendarRange,
  Pencil,
  Play,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { ProjectForm } from "@/components/project-form";
import { ProjectUploads } from "@/components/project-uploads";
import { EmptyState, ErrorState, LoadingState, Notice } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";
import {
  formatCost,
  formatDate,
  formatDateTime,
  formatDuration,
  formatNumber,
  shortId,
} from "@/lib/format";
import {
  runStatusLabel,
  runStatusVariant,
  TERMINAL_RUN_STATUSES,
} from "@/lib/labels";
import { pick, useLocale } from "@/lib/locale";
import type { Project, Run, RunListResponse } from "@/lib/types";

export default function ProjectPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = Array.isArray(params.projectId)
    ? params.projectId[0]
    : params.projectId;
  const locale = useLocale();
  const router = useRouter();

  const [project, setProject] = useState<Project | null>(null);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [starting, setStarting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!projectId) return;
    try {
      const [projectData, runData] = await Promise.all([
        apiFetch<Project>(`/projects/${projectId}`),
        apiFetch<RunListResponse>(`/projects/${projectId}/runs?limit=100`),
      ]);
      setProject(projectData);
      setRuns(runData.items);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : String(caught));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  const refresh = useCallback(async () => {
    setLoading(true);
    await load();
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  async function startRun() {
    if (!project) return;
    setStarting(true);
    setActionError(null);
    try {
      const run = await apiFetch<Run>(`/projects/${project.id}/runs`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      router.push(`/runs/${run.id}`);
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.detail : String(caught));
      setStarting(false);
    }
  }

  if (loading && !project) {
    return (
      <main className="mx-auto w-full max-w-[1400px] px-6 py-10 lg:px-8">
        <LoadingState label={pick(locale, "加载项目…", "Loading project…")} />
      </main>
    );
  }

  if (error || !project) {
    return (
      <main className="mx-auto w-full max-w-[1400px] px-6 py-10 lg:px-8">
        <ErrorState message={error ?? pick(locale, "项目不存在", "Project not found")} onRetry={() => void refresh()} />
      </main>
    );
  }

  const latest = runs?.[0];
  const active = runs?.find((run) => !TERMINAL_RUN_STATUSES.has(run.status));

  return (
    <main className="mx-auto w-full max-w-[1400px] px-6 py-10 lg:px-8 lg:py-14">
      <PageHeader
        eyebrow={
          <Button asChild variant="ghost" size="xs" className="-ml-2">
            <Link href="/">
              <ArrowLeft className="h-3.5 w-3.5" />
              {pick(locale, "全部项目", "All projects")}
            </Link>
          </Button>
        }
        title={project.title}
        description={<p className="whitespace-pre-line">{project.topic}</p>}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
              <RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
              {pick(locale, "刷新", "Refresh")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setEditing((value) => !value)}
            >
              {editing ? <X className="h-4 w-4" /> : <Pencil className="h-4 w-4" />}
              {editing
                ? pick(locale, "关闭编辑", "Close editor")
                : pick(locale, "编辑参数", "Edit settings")}
            </Button>
            <Button size="sm" onClick={() => void startRun()} loading={starting}>
              <Play className="h-4 w-4" />
              {pick(locale, "启动研究", "Start research")}
            </Button>
          </>
        }
      />

      {actionError ? (
        <Notice
          variant="danger"
          title={pick(locale, "无法启动运行", "Could not start the run")}
          className="mt-6"
        >
          {actionError}
        </Notice>
      ) : null}

      {active ? (
        <Notice
          variant="warning"
          title={pick(locale, "已有运行正在进行", "A run is already active")}
          className="mt-6"
          action={
            <Button asChild variant="outline" size="sm">
              <Link href={`/runs/${active.id}`}>
                {pick(locale, "查看运行", "View run")}
                <ArrowUpRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
          }
        >
          {pick(
            locale,
            "可以继续启动新的运行，但建议先等待当前运行结束。",
            "You can start another run, but it is better to wait for the active one to finish.",
          )}
        </Notice>
      ) : null}

      <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          {
            label: pick(locale, "语言 / 深度", "Language / depth"),
            value:
              (project.language === "zh" ? "中文" : "English") +
              " · " +
              (project.research_depth === "quick"
                ? pick(locale, "快速", "Quick")
                : project.research_depth === "deep"
                  ? pick(locale, "深入", "Deep")
                  : pick(locale, "标准", "Standard")),
          },
          {
            label: pick(locale, "目标字数", "Target words"),
            value: project.target_words
              ? formatNumber(project.target_words, locale)
              : pick(locale, "自动", "Automatic"),
          },
          {
            label: pick(locale, "时间范围", "Date range"),
            value:
              project.date_from || project.date_to
                ? `${formatDate(project.date_from, locale)} – ${formatDate(project.date_to, locale)}`
                : pick(locale, "不限", "Any"),
          },
          {
            label: pick(locale, "引用格式", "Citation style"),
            value: project.citation_style.toUpperCase(),
          },
        ].map((item) => (
          <Card key={item.label} className="p-4">
            <p className="text-xs text-foreground-subtle">{item.label}</p>
            <p className="mt-1.5 text-sm font-medium">{item.value}</p>
          </Card>
        ))}
      </div>

      {project.research_scope ? (
        <Card className="mt-3 p-5">
          <p className="text-xs font-medium uppercase tracking-wide text-foreground-subtle">
            {pick(locale, "研究范围", "Research scope")}
          </p>
          <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-foreground-muted">
            {project.research_scope}
          </p>
        </Card>
      ) : null}

      <ProjectUploads projectId={project.id} />

      {editing ? (
        <Card className="mt-6 p-6 lg:p-8">
          <h2 className="font-display text-lg font-semibold tracking-tight">
            {pick(locale, "项目设置", "Project settings")}
          </h2>
          <div className="mt-6">
            <ProjectForm
              project={project}
              onSaved={(saved) => {
                setProject(saved);
                setEditing(false);
              }}
              onCancel={() => setEditing(false)}
            />
          </div>
        </Card>
      ) : null}

      <Card className="mt-6 overflow-hidden">
        <div className="flex items-center justify-between gap-3 border-b border-border p-5">
          <div>
            <h2 className="font-display text-base font-semibold tracking-tight">
              {pick(locale, "运行历史", "Run history")}
            </h2>
            <p className="mt-0.5 text-xs text-foreground-subtle">
              {pick(locale, "每次运行保留独立计划、来源、证据与报告版本。", "Each run keeps its own plan, sources, evidence, and report versions.")}
            </p>
          </div>
          {latest ? (
            <Button asChild variant="outline" size="sm">
              <Link href={`/runs/${latest.id}`}>
                {pick(locale, "最近运行", "Latest run")}
                <ArrowUpRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
          ) : null}
        </div>

        {runs === null ? (
          <div className="p-6">
            <LoadingState />
          </div>
        ) : runs.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={<Search className="h-5 w-5" />}
              title={pick(locale, "还没有运行记录", "No runs yet")}
              description={pick(
                locale,
                "启动一次研究，工作流会生成研究计划与提纲等待审批。",
                "Start a run to generate a research plan and outline for approval.",
              )}
              action={
                <Button onClick={() => void startRun()} loading={starting}>
                  <Play className="h-4 w-4" />
                  {pick(locale, "启动研究", "Start research")}
                </Button>
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] border-collapse text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-foreground-subtle">
                  <th className="px-5 py-3 font-medium">{pick(locale, "运行", "Run")}</th>
                  <th className="px-5 py-3 font-medium">{pick(locale, "状态", "Status")}</th>
                  <th className="px-5 py-3 font-medium">{pick(locale, "开始时间", "Started")}</th>
                  <th className="px-5 py-3 font-medium">{pick(locale, "用时", "Duration")}</th>
                  <th className="px-5 py-3 font-medium">{pick(locale, "用量", "Usage")}</th>
                  <th className="px-5 py-3 font-medium">{pick(locale, "成本", "Cost")}</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr
                    key={run.id}
                    className="border-b border-border last:border-b-0 hover:bg-hover-bg/50"
                  >
                    <td className="px-5 py-3.5">
                      <Link
                        href={`/runs/${run.id}`}
                        className="font-mono text-xs font-medium hover:underline"
                      >
                        {shortId(run.id)}
                      </Link>
                      {run.error_summary ? (
                        <p className="mt-1 max-w-[320px] truncate text-xs text-red-fg">
                          {run.error_summary}
                        </p>
                      ) : null}
                    </td>
                    <td className="px-5 py-3.5">
                      <Badge variant={runStatusVariant(run.status)} size="xs" dot>
                        {runStatusLabel(run.status, locale)}
                      </Badge>
                    </td>
                    <td className="px-5 py-3.5 text-xs text-foreground-muted">
                      {formatDateTime(run.started_at ?? run.created_at, locale)}
                    </td>
                    <td className="px-5 py-3.5 text-xs text-foreground-muted">
                      {formatDuration(run.started_at, run.finished_at, locale)}
                    </td>
                    <td className="px-5 py-3.5 font-mono text-xs text-foreground-muted">
                      {formatNumber(run.input_tokens + run.output_tokens, locale)}{" "}
                      {pick(locale, "tokens", "tokens")}
                    </td>
                    <td className="px-5 py-3.5 font-mono text-xs text-foreground-muted">
                      {formatCost(run.cost_usd)}
                    </td>
                    <td className="px-5 py-3.5 text-right">
                      <Button asChild variant="ghost" size="xs">
                        <Link href={`/runs/${run.id}`}>
                          {pick(locale, "打开", "Open")}
                          <ArrowUpRight className="h-3 w-3" />
                        </Link>
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="mt-4 flex flex-wrap items-center gap-4 text-xs text-foreground-subtle">
        <span className="inline-flex items-center gap-1.5">
          <CalendarRange className="h-3.5 w-3.5" />
          {pick(locale, "创建于 ", "Created ")}
          {formatDateTime(project.created_at, locale)}
        </span>
        <span>
          {pick(locale, "项目编号 ", "Project ID ")}
          <span className="font-mono">{project.id}</span>
        </span>
      </div>
    </main>
  );
}
