"use client";

import {
  ArrowUpRight,
  BookOpenCheck,
  ChevronDown,
  Filter,
  RefreshCw,
  Search,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useRunWorkspace } from "@/components/run/run-workspace";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { ApiError, apiFetch } from "@/lib/api";
import { formatBytes, formatDate, formatDateTime } from "@/lib/format";
import {
  contentKindLabel,
  fetchStatusLabel,
  fetchStatusVariant,
  parserStatusLabel,
  parserStatusVariant,
  sourceTypeLabel,
} from "@/lib/labels";
import { pick, useLocale } from "@/lib/locale";
import type {
  QuestionListResponse,
  ResearchQuestion,
  Source,
  SourceListResponse,
} from "@/lib/types";

type StatusFilter = "all" | "parsed" | "pending" | "failed" | "snippet";

function sourceHasVersion(source: Source, predicate: (status: string) => boolean) {
  return source.versions.some((version) => predicate(version.parser_status));
}

export default function SourcesPage() {
  const locale = useLocale();
  const { runId, overview } = useRunWorkspace();
  const [sources, setSources] = useState<Source[] | null>(null);
  const [questions, setQuestions] = useState<ResearchQuestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [questionFilter, setQuestionFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    try {
      const [sourceData, questionData] = await Promise.all([
        apiFetch<SourceListResponse>(`/runs/${runId}/sources`),
        apiFetch<QuestionListResponse>(`/runs/${runId}/questions`),
      ]);
      setSources(sourceData.items);
      setQuestions(questionData.items);
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
    const initial = new URLSearchParams(window.location.search).get("question");
    if (initial) setQuestionFilter(initial);
  }, []);

  const questionMap = useMemo(
    () => new Map(questions.map((question) => [question.id, question.question])),
    [questions],
  );

  const filtered = useMemo(() => {
    if (!sources) return [];
    const normalizedQuery = query.trim().toLowerCase();
    return sources.filter((source) => {
      if (
        questionFilter !== "all" &&
        !source.question_ids.includes(questionFilter)
      ) {
        return false;
      }
      if (statusFilter === "parsed" && !sourceHasVersion(source, (status) => status === "parsed")) {
        return false;
      }
      if (
        statusFilter === "pending" &&
        !sourceHasVersion(
          source,
          (status) => status === "pending" || status === "parsing",
        )
      ) {
        return false;
      }
      if (statusFilter === "failed" && !sourceHasVersion(source, (status) => status === "failed")) {
        return false;
      }
      if (
        statusFilter === "snippet" &&
        (source.versions.length === 0 ||
          source.versions.some(
            (version) => version.content_kind === "full_text",
          ))
      ) {
        return false;
      }
      if (!normalizedQuery) return true;
      return [
        source.title,
        ...source.authors,
        source.venue ?? "",
        source.publisher ?? "",
        source.doi ?? "",
        source.url ?? "",
      ]
        .join(" ")
        .toLowerCase()
        .includes(normalizedQuery);
    });
  }, [query, questionFilter, sources, statusFilter]);

  const activeRun = overview
    ? !["completed", "failed", "cancelled"].includes(overview.run.status)
    : false;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="font-display text-lg font-semibold tracking-tight">
            {pick(locale, "来源与版本", "Sources & versions")}
          </h2>
          <p className="mt-0.5 text-xs text-foreground-subtle">
            {pick(
              locale,
              "来源元数据、抓取状态与每次解析版本。",
              "Source metadata, fetch status, and every parsed version.",
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {activeRun ? (
            <Badge variant="info" size="xs" dot>
              {pick(locale, "运行中，列表可能更新", "Run active; list may change")}
            </Badge>
          ) : null}
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
            <RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            {pick(locale, "刷新", "Refresh")}
          </Button>
        </div>
      </div>

      <Card className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_240px_200px]">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-foreground-faint" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={pick(locale, "搜索标题、作者、DOI…", "Search title, authors, DOI…")}
            className="pl-9"
          />
        </div>
        <Select
          value={questionFilter}
          onChange={(event) => setQuestionFilter(event.target.value)}
          aria-label={pick(locale, "按研究问题筛选", "Filter by research question")}
        >
          <option value="all">
            {pick(locale, "全部研究问题", "All questions")}
          </option>
          {questions.map((question) => (
            <option key={question.id} value={question.id}>
              {question.question}
            </option>
          ))}
        </Select>
        <div className="relative">
          <Filter className="pointer-events-none absolute left-3 top-1/2 z-10 h-4 w-4 -translate-y-1/2 text-foreground-faint" />
          <Select
            value={statusFilter}
            onChange={(event) =>
              setStatusFilter(event.target.value as StatusFilter)
            }
            className="pl-9"
            aria-label={pick(locale, "按解析状态筛选", "Filter by parse status")}
          >
            <option value="all">{pick(locale, "全部状态", "All statuses")}</option>
            <option value="parsed">{pick(locale, "已解析", "Parsed")}</option>
            <option value="pending">{pick(locale, "解析中 / 待解析", "Parsing / pending")}</option>
            <option value="failed">{pick(locale, "解析失败", "Failed")}</option>
            <option value="snippet">{pick(locale, "仅摘要", "Snippets only")}</option>
          </Select>
        </div>
      </Card>

      {loading && sources === null ? (
        <LoadingState label={pick(locale, "加载来源…", "Loading sources…")} />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void refresh()} />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<BookOpenCheck className="h-5 w-5" />}
          title={
            sources && sources.length > 0
              ? pick(locale, "没有匹配的来源", "No matching sources")
              : pick(locale, "尚未收集来源", "No sources collected yet")
          }
          description={
            sources && sources.length > 0
              ? pick(locale, "调整筛选条件后重试。", "Adjust the filters and try again.")
              : pick(
                  locale,
                  "检索阶段完成后，来源会出现在这里。",
                  "Sources appear after the search stage completes.",
                )
          }
        />
      ) : (
        <>
          <p className="text-xs text-foreground-subtle">
            {pick(
              locale,
              `显示 ${filtered.length} / ${sources?.length ?? 0} 个来源`,
              `Showing ${filtered.length} of ${sources?.length ?? 0} sources`,
            )}
          </p>
          <div className="grid gap-3 xl:grid-cols-2">
            {filtered.map((source) => (
              <Card key={source.id} className="flex flex-col p-5">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge variant="outline" size="xs">
                        {sourceTypeLabel(source.source_type, locale)}
                      </Badge>
                      {source.fetch_status ? (
                        <Badge
                          variant={fetchStatusVariant(source.fetch_status)}
                          size="xs"
                        >
                          {fetchStatusLabel(source.fetch_status, locale)}
                        </Badge>
                      ) : null}
                      <Badge variant="secondary" size="xs">
                        {source.evidence_count}{" "}
                        {pick(locale, "条证据", "evidence")}
                      </Badge>
                    </div>
                    <h3 className="mt-2 font-display text-base font-semibold leading-snug tracking-tight">
                      {source.title}
                    </h3>
                    <p className="mt-1 text-xs leading-relaxed text-foreground-muted">
                      {source.authors.length > 0
                        ? source.authors.join(", ")
                        : pick(locale, "未知作者", "Unknown authors")}
                    </p>
                    <p className="mt-0.5 text-[11px] text-foreground-subtle">
                      {[
                        source.venue,
                        source.publisher,
                        source.published_at
                          ? formatDate(source.published_at, locale)
                          : null,
                      ]
                        .filter(Boolean)
                        .join(" · ") || pick(locale, "未提供出版信息", "No publication metadata")}
                    </p>
                  </div>
                  {source.url ? (
                    <Button asChild variant="ghost" size="icon-sm" className="shrink-0">
                      <a
                        href={source.url}
                        target="_blank"
                        rel="noreferrer"
                        aria-label={pick(locale, "打开来源", "Open source")}
                      >
                        <ArrowUpRight className="h-4 w-4" />
                      </a>
                    </Button>
                  ) : null}
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-foreground-subtle">
                  {source.doi ? (
                    <a
                      href={`https://doi.org/${source.doi}`}
                      target="_blank"
                      rel="noreferrer"
                      className="font-mono hover:text-foreground"
                    >
                      DOI {source.doi}
                    </a>
                  ) : null}
                  {source.question_ids.length > 0 ? (
                    <span>
                      {pick(locale, "关联问题", "Questions")}:{" "}
                      {source.question_ids
                        .map((id, index) =>
                          questionMap.has(id)
                            ? `Q${questions.findIndex((q) => q.id === id) + 1}`
                            : `Q${index + 1}`,
                        )
                        .join(", ")}
                    </span>
                  ) : null}
                </div>

                <details className="group mt-auto pt-4">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-xs text-foreground-muted transition-colors hover:bg-hover-bg">
                    <span>
                      {pick(
                        locale,
                        `${source.versions.length} 个版本`,
                        `${source.versions.length} versions`,
                      )}
                    </span>
                    <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
                  </summary>
                  {source.versions.length === 0 ? (
                    <p className="mt-2 px-1 text-xs text-foreground-subtle">
                      {pick(locale, "没有来源版本。", "No source versions.")}
                    </p>
                  ) : (
                    <ul className="mt-2 space-y-2">
                      {source.versions.map((version) => (
                        <li
                          key={version.id}
                          className="rounded-md border border-border px-3 py-2.5"
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-mono text-[11px] font-medium">
                              v{version.version_number}
                            </span>
                            <Badge variant="outline" size="xs">
                              {contentKindLabel(version.content_kind, locale)}
                            </Badge>
                            <Badge
                              variant={parserStatusVariant(version.parser_status)}
                              size="xs"
                              dot
                            >
                              {parserStatusLabel(version.parser_status, locale)}
                            </Badge>
                            <span className="ml-auto text-[10px] text-foreground-faint">
                              {formatDateTime(version.fetched_at, locale)}
                            </span>
                          </div>
                          <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-foreground-subtle">
                            {version.mime_type ? <span>{version.mime_type}</span> : null}
                            <span>{formatBytes(version.size_bytes)}</span>
                            <span>
                              {version.has_stored_file
                                ? pick(locale, "已存储文件", "File stored")
                                : pick(locale, "无存储文件", "No stored file")}
                            </span>
                            {version.parser_version ? (
                              <span>parser {version.parser_version}</span>
                            ) : null}
                          </div>
                          {version.fetched_url ? (
                            <a
                              href={version.fetched_url}
                              target="_blank"
                              rel="noreferrer"
                              className="mt-1 block truncate font-mono text-[10px] text-foreground-subtle hover:text-foreground"
                            >
                              {version.fetched_url}
                            </a>
                          ) : null}
                          {version.parse_error ? (
                            <p className="mt-1.5 text-[11px] text-red-fg">
                              {version.parse_error}
                            </p>
                          ) : null}
                          {typeof version.metadata.snippet === "string" ? (
                            <p className="mt-1.5 line-clamp-3 text-[11px] leading-relaxed text-foreground-muted">
                              {version.metadata.snippet}
                            </p>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  )}
                </details>
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
