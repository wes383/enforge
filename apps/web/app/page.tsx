"use client";

import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  CalendarRange,
  FileText,
  Plus,
  RefreshCw,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";
import { formatDate, formatDateTime, formatNumber } from "@/lib/format";
import {
  runStatusLabel,
  runStatusVariant,
  TERMINAL_RUN_STATUSES,
} from "@/lib/labels";
import { pick, useLocale } from "@/lib/locale";
import type {
  Project,
  ProjectListResponse,
  Run,
  RunListResponse,
} from "@/lib/types";

type ProjectEntry = {
  project: Project;
  runs: Run[];
  totalRuns: number;
};

export default function ProjectsPage() {
  const locale = useLocale();
  const [entries, setEntries] = useState<ProjectEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const response = await apiFetch<ProjectListResponse>("/projects?limit=100");
      const items = await Promise.all(
        response.items.map(async (project): Promise<ProjectEntry> => {
          try {
            const runs = await apiFetch<RunListResponse>(
              `/projects/${project.id}/runs?limit=5`,
            );
            return { project, runs: runs.items, totalRuns: runs.total };
          } catch {
            return { project, runs: [], totalRuns: 0 };
          }
        }),
      );
      setEntries(items);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    await load();
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <main className="mx-auto w-full max-w-[1400px] px-6 py-10 lg:px-8 lg:py-14">
      <PageHeader
        eyebrow={
          <Badge variant="secondary" size="xs">
            {pick(locale, "研究工作台", "Research workspace")}
          </Badge>
        }
        title={pick(locale, "研究项目", "Research projects")}
        description={pick(
          locale,
          "从研究计划、证据检索到长篇写作与引用核验，所有产出均保留可追溯来源。",
          "From planning and evidence retrieval to long-form writing and citation verification, every output stays traceable.",
        )}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
              <RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
              {pick(locale, "刷新", "Refresh")}
            </Button>
            <Button asChild size="sm">
              <Link href="/projects/new">
                <Plus className="h-4 w-4" />
                {pick(locale, "新建项目", "New project")}
              </Link>
            </Button>
          </>
        }
      />

      <div className="mt-8">
        {loading && entries === null ? (
          <div className="grid gap-4 lg:grid-cols-2">
            {[0, 1, 2, 3].map((index) => (
              <Card key={index} className="h-[210px] animate-pulse bg-hover-bg/40" />
            ))}
          </div>
        ) : error ? (
          <ErrorState message={error} onRetry={() => void refresh()} />
        ) : entries && entries.length > 0 ? (
          <div className="grid gap-4 lg:grid-cols-2">
            {entries.map(({ project, runs, totalRuns }) => {
              const latest = runs[0];
              const active = runs.find(
                (run) => !TERMINAL_RUN_STATUSES.has(run.status),
              );
              return (
                <Card key={project.id} hoverable className="flex flex-col p-6">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <Link
                        href={`/projects/${project.id}`}
                        className="font-display text-lg font-semibold leading-snug tracking-tight transition-colors hover:text-foreground-muted"
                      >
                        {project.title}
                      </Link>
                      <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-foreground-muted">
                        {project.topic}
                      </p>
                    </div>
                    {latest ? (
                      <Badge
                        variant={runStatusVariant(latest.status)}
                        size="xs"
                        dot
                        className="shrink-0"
                      >
                        {runStatusLabel(latest.status, locale)}
                      </Badge>
                    ) : (
                      <Badge variant="secondary" size="xs" className="shrink-0">
                        {pick(locale, "尚未运行", "No runs")}
                      </Badge>
                    )}
                  </div>

                  <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-foreground-subtle">
                    <span className="inline-flex items-center gap-1.5">
                      <BookOpen className="h-3.5 w-3.5" />
                      {project.language === "zh" ? "中文" : "English"}
                    </span>
                    <span>
                      {project.research_depth === "quick"
                        ? pick(locale, "快速", "Quick")
                        : project.research_depth === "deep"
                          ? pick(locale, "深入", "Deep")
                          : pick(locale, "标准", "Standard")}
                    </span>
                    <span>{project.citation_style.toUpperCase()}</span>
                    {project.target_words ? (
                      <span>
                        {formatNumber(project.target_words, locale)}{" "}
                        {pick(locale, "字", "words")}
                      </span>
                    ) : null}
                    {project.date_from || project.date_to ? (
                      <span className="inline-flex items-center gap-1.5">
                        <CalendarRange className="h-3.5 w-3.5" />
                        {formatDate(project.date_from, locale)} –{" "}
                        {formatDate(project.date_to, locale)}
                      </span>
                    ) : null}
                    <span className="inline-flex items-center gap-1.5">
                      <FileText className="h-3.5 w-3.5" />
                      {pick(locale, `${totalRuns} 次运行`, `${totalRuns} runs`)}
                    </span>
                  </div>

                  <div className="mt-auto flex items-end justify-between gap-3 pt-5">
                    <div className="min-w-0 text-xs text-foreground-subtle">
                      {active ? (
                        <span>
                          {pick(locale, "正在运行 · ", "Active · ")}
                          {formatDateTime(active.created_at, locale)}
                        </span>
                      ) : latest ? (
                        <span>
                          {pick(locale, "最近运行 · ", "Last run · ")}
                          {formatDateTime(latest.created_at, locale)}
                        </span>
                      ) : (
                        <span>
                          {pick(locale, "创建于 ", "Created ")}
                          {formatDateTime(project.created_at, locale)}
                        </span>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {latest ? (
                        <Button asChild variant="outline" size="sm">
                          <Link href={`/runs/${latest.id}`}>
                            {pick(locale, "打开运行", "Open run")}
                            <ArrowUpRight className="h-3.5 w-3.5" />
                          </Link>
                        </Button>
                      ) : null}
                      <Button asChild variant="ghost" size="sm">
                        <Link href={`/projects/${project.id}`}>
                          {pick(locale, "项目", "Project")}
                          <ArrowRight className="h-3.5 w-3.5" />
                        </Link>
                      </Button>
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
        ) : (
          <EmptyState
            title={pick(locale, "还没有研究项目", "No research projects yet")}
            description={pick(
              locale,
              "创建项目并启动第一次研究运行。",
              "Create a project and start your first research run.",
            )}
            action={
              <Button asChild>
                <Link href="/projects/new">
                  <Plus className="h-4 w-4" />
                  {pick(locale, "新建项目", "New project")}
                </Link>
              </Button>
            }
          />
        )}
      </div>
    </main>
  );
}
