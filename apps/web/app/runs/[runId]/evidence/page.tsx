"use client";

import {
  ArrowUpRight,
  ChevronDown,
  Filter,
  MapPin,
  RefreshCw,
  Search,
  ScrollText,
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
import { formatDate, formatLocator, shortId } from "@/lib/format";
import {
  contentKindLabel,
  EVIDENCE_TYPE,
  evidenceTypeLabel,
  evidenceVerificationLabel,
  evidenceVerificationVariant,
  sourceTypeLabel,
} from "@/lib/labels";
import { pick, useLocale } from "@/lib/locale";
import type {
  Evidence,
  EvidenceListResponse,
  QuestionListResponse,
  ResearchQuestion,
} from "@/lib/types";

const PAGE_SIZE = 200;

const RELATION_LABEL = {
  supports: { zh: "支持", en: "Supports" },
  refutes: { zh: "反驳", en: "Refutes" },
  context: { zh: "背景", en: "Context" },
} as const;

function relationOf(evidenceType: string): keyof typeof RELATION_LABEL {
  if (evidenceType === "refutation") return "refutes";
  if (evidenceType === "direct_support") return "supports";
  return "context";
}

export default function EvidencePage() {
  const locale = useLocale();
  const { runId, overview } = useRunWorkspace();
  const [items, setItems] = useState<Evidence[]>([]);
  const [total, setTotal] = useState(0);
  const [questions, setQuestions] = useState<ResearchQuestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [questionFilter, setQuestionFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [relationFilter, setRelationFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [query, setQuery] = useState("");

  const loadInitial = useCallback(async () => {
    try {
      const [evidenceData, questionData] = await Promise.all([
        apiFetch<EvidenceListResponse>(
          `/runs/${runId}/evidence?limit=${PAGE_SIZE}&offset=0`,
        ),
        apiFetch<QuestionListResponse>(`/runs/${runId}/questions`),
      ]);
      setItems(evidenceData.items);
      setTotal(evidenceData.total);
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
    await loadInitial();
  }, [loadInitial]);

  const loadMore = useCallback(async () => {
    setLoadingMore(true);
    try {
      const data = await apiFetch<EvidenceListResponse>(
        `/runs/${runId}/evidence?limit=${PAGE_SIZE}&offset=${items.length}`,
      );
      setItems((current) => {
        const seen = new Set(current.map((item) => item.id));
        return [...current, ...data.items.filter((item) => !seen.has(item.id))];
      });
      setTotal(data.total);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : String(caught));
    } finally {
      setLoadingMore(false);
    }
  }, [items.length, runId]);

  useEffect(() => {
    void loadInitial();
  }, [loadInitial]);

  useEffect(() => {
    const initial = new URLSearchParams(window.location.search).get("question");
    if (initial) setQuestionFilter(initial);
  }, []);

  const questionMap = useMemo(
    () => new Map(questions.map((question) => [question.id, question])),
    [questions],
  );

  const sourceOptions = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of items) map.set(item.source_id, item.source_title);
    return [...map.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [items]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return items.filter((item) => {
      if (
        questionFilter !== "all" &&
        item.research_question_id !== questionFilter
      ) {
        return false;
      }
      if (typeFilter !== "all" && item.evidence_type !== typeFilter) return false;
      if (relationFilter !== "all" && relationOf(item.evidence_type) !== relationFilter) {
        return false;
      }
      if (sourceFilter !== "all" && item.source_id !== sourceFilter) return false;
      if (!normalized) return true;
      return [item.quoted_text, item.claim_summary, item.source_title]
        .join(" ")
        .toLowerCase()
        .includes(normalized);
    });
  }, [items, query, questionFilter, relationFilter, sourceFilter, typeFilter]);

  const activeRun = overview
    ? !["completed", "failed", "cancelled"].includes(overview.run.status)
    : false;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="font-display text-lg font-semibold tracking-tight">
            {pick(locale, "证据浏览器", "Evidence browser")}
          </h2>
          <p className="mt-0.5 text-xs text-foreground-subtle">
            {pick(
              locale,
              "逐条查看支持与反驳证据、来源定位与核验状态。",
              "Inspect supporting and refuting evidence, locators, and verification status.",
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {activeRun ? (
            <Badge variant="info" size="xs" dot>
              {pick(locale, "运行中，证据可能更新", "Run active; evidence may change")}
            </Badge>
          ) : null}
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
            <RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            {pick(locale, "刷新", "Refresh")}
          </Button>
        </div>
      </div>

      <Card className="grid gap-3 p-4 lg:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_220px_180px_180px]">
        <div className="relative lg:col-span-2 xl:col-span-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-foreground-faint" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={pick(locale, "搜索证据原文或来源…", "Search quotes or sources…")}
            className="pl-9"
          />
        </div>
        <Select
          value={questionFilter}
          onChange={(event) => setQuestionFilter(event.target.value)}
          aria-label={pick(locale, "按研究问题筛选", "Filter by question")}
        >
          <option value="all">{pick(locale, "全部问题", "All questions")}</option>
          {questions.map((question, index) => (
            <option key={question.id} value={question.id}>
              Q{index + 1}. {question.question}
            </option>
          ))}
        </Select>
        <div className="relative">
          <Filter className="pointer-events-none absolute left-3 top-1/2 z-10 h-4 w-4 -translate-y-1/2 text-foreground-faint" />
          <Select
            value={relationFilter}
            onChange={(event) => setRelationFilter(event.target.value)}
            className="pl-9"
            aria-label={pick(locale, "按关系筛选", "Filter by relation")}
          >
            <option value="all">{pick(locale, "全部关系", "All relations")}</option>
            <option value="supports">{pick(locale, "支持", "Supports")}</option>
            <option value="refutes">{pick(locale, "反驳", "Refutes")}</option>
            <option value="context">{pick(locale, "背景", "Context")}</option>
          </Select>
        </div>
        <Select
          value={sourceFilter}
          onChange={(event) => setSourceFilter(event.target.value)}
          aria-label={pick(locale, "按来源筛选", "Filter by source")}
        >
          <option value="all">{pick(locale, "全部来源", "All sources")}</option>
          {sourceOptions.map(([id, title]) => (
            <option key={id} value={id}>
              {title.slice(0, 80)}
            </option>
          ))}
        </Select>
        <Select
          value={typeFilter}
          onChange={(event) => setTypeFilter(event.target.value)}
          aria-label={pick(locale, "按证据类型筛选", "Filter by evidence type")}
        >
          <option value="all">{pick(locale, "全部类型", "All types")}</option>
          {Object.entries(EVIDENCE_TYPE).map(([type, meta]) => (
            <option key={type} value={type}>
              {locale === "zh" ? meta.label.zh : meta.label.en}
            </option>
          ))}
        </Select>
      </Card>

      {loading && items.length === 0 ? (
        <LoadingState label={pick(locale, "加载证据…", "Loading evidence…")} />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void refresh()} />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<ScrollText className="h-5 w-5" />}
          title={
            items.length > 0
              ? pick(locale, "没有匹配的证据", "No matching evidence")
              : pick(locale, "尚未抽取证据", "No evidence extracted yet")
          }
          description={
            items.length > 0
              ? pick(locale, "调整筛选条件后重试。", "Adjust the filters and try again.")
              : pick(
                  locale,
                  "证据会在来源解析完成后进入证据矩阵。",
                  "Evidence enters the matrix after sources are parsed.",
                )
          }
        />
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-foreground-subtle">
            <span>
              {pick(
                locale,
                `显示 ${filtered.length} / 已加载 ${items.length} 条（共 ${total}）`,
                `Showing ${filtered.length} of ${items.length} loaded (${total} total)`,
              )}
            </span>
            {items.length < total ? (
              <Button variant="outline" size="xs" onClick={() => void loadMore()} loading={loadingMore}>
                {pick(locale, "加载更多", "Load more")}
              </Button>
            ) : null}
          </div>
          <div className="grid gap-3 xl:grid-cols-2">
            {filtered.map((item) => {
              const relation = relationOf(item.evidence_type);
              const locators = formatLocator(item.locator);
              const question = item.research_question_id
                ? questionMap.get(item.research_question_id)
                : null;
              return (
                <Card key={item.id} className="flex min-w-0 flex-col p-5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge
                      variant={
                        relation === "supports"
                          ? "success"
                          : relation === "refutes"
                            ? "danger"
                            : "secondary"
                      }
                      size="xs"
                    >
                      {RELATION_LABEL[relation][locale]}
                    </Badge>
                    <Badge variant="outline" size="xs">
                      {evidenceTypeLabel(item.evidence_type, locale)}
                    </Badge>
                    <Badge
                      variant={evidenceVerificationVariant(item.verification_status)}
                      size="xs"
                      dot
                    >
                      {evidenceVerificationLabel(item.verification_status, locale)}
                    </Badge>
                    {question ? (
                      <Badge
                        variant="secondary"
                        size="xs"
                        className="min-w-0 max-w-full truncate"
                      >
                        {question.question}
                      </Badge>
                    ) : null}
                  </div>

                  <blockquote className="mt-3 break-words border-l-2 border-border-strong pl-3 text-sm leading-relaxed text-foreground">
                    “{item.quoted_text}”
                  </blockquote>
                  <p className="mt-2 text-xs leading-relaxed text-foreground-muted">
                    {item.claim_summary}
                  </p>

                  {item.context_before || item.context_after ? (
                    <details className="group mt-3">
                      <summary className="flex cursor-pointer list-none items-center gap-1 text-[11px] text-foreground-subtle hover:text-foreground">
                        <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-180" />
                        {pick(locale, "上下文", "Context")}
                      </summary>
                      <div className="mt-2 space-y-1.5 rounded-md bg-hover-bg/50 p-2.5 text-[11px] leading-relaxed text-foreground-muted">
                        {item.context_before ? (
                          <p>
                            <span className="text-foreground-faint">… </span>
                            {item.context_before}
                          </p>
                        ) : null}
                        {item.context_after ? (
                          <p>
                            {item.context_after}
                            <span className="text-foreground-faint"> …</span>
                          </p>
                        ) : null}
                      </div>
                    </details>
                  ) : null}

                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    {locators.length > 0 ? (
                      locators.map((locator) => (
                        <span
                          key={locator}
                          className="inline-flex items-center gap-1 rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[10px] text-foreground-muted"
                        >
                          <MapPin className="h-2.5 w-2.5" />
                          {locator}
                        </span>
                      ))
                    ) : (
                      <span className="text-[10px] text-foreground-faint">
                        {pick(locale, "未提供定位信息", "No locator recorded")}
                      </span>
                    )}
                    {typeof item.locator.chunk_id === "string" ? (
                      <span className="font-mono text-[10px] text-foreground-faint">
                        chunk {shortId(item.locator.chunk_id)}
                      </span>
                    ) : null}
                  </div>

                  <div className="mt-auto flex items-end justify-between gap-3 pt-4">
                    <div className="min-w-0 text-[11px] text-foreground-subtle">
                      <p className="truncate font-medium text-foreground-muted">
                        {item.source_title}
                      </p>
                      <p className="mt-0.5 truncate">
                        {[
                          item.source_authors.length > 0
                            ? item.source_authors.join(", ")
                            : null,
                          item.source_published_at
                            ? formatDate(item.source_published_at, locale)
                            : null,
                          item.source_type
                            ? sourceTypeLabel(item.source_type, locale)
                            : null,
                          item.source_version_number
                            ? `v${item.source_version_number}`
                            : null,
                          item.source_version_kind
                            ? contentKindLabel(item.source_version_kind, locale)
                            : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </p>
                    </div>
                    {item.source_url ? (
                      <Button asChild variant="ghost" size="icon-sm" className="shrink-0">
                        <a
                          href={item.source_url}
                          target="_blank"
                          rel="noreferrer"
                          aria-label={pick(locale, "打开来源", "Open source")}
                        >
                          <ArrowUpRight className="h-4 w-4" />
                        </a>
                      </Button>
                    ) : null}
                  </div>
                </Card>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
