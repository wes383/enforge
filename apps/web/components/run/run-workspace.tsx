"use client";

import {
  ArrowLeft,
  BookOpenCheck,
  ClipboardCheck,
  FileText,
  Gauge,
  RefreshCw,
  ScrollText,
  Square,
  X,
} from "lucide-react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ErrorState, LoadingState, Notice } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError, apiFetch } from "@/lib/api";
import { formatCost, formatDateTime, formatDuration, shortId } from "@/lib/format";
import {
  runStatusLabel,
  runStatusVariant,
  TERMINAL_RUN_STATUSES,
} from "@/lib/labels";
import { pick, useLocale } from "@/lib/locale";
import type { RunOverview } from "@/lib/types";
import { cn } from "@/lib/utils";

type RefreshHandler = () => void | Promise<void>;

type RunWorkspaceValue = {
  runId: string;
  overview: RunOverview | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
  refresh: () => Promise<void>;
  registerRefresh: (handler: RefreshHandler) => () => void;
};

const RunWorkspaceContext = createContext<RunWorkspaceValue | null>(null);

export function useRunWorkspace(): RunWorkspaceValue {
  const value = useContext(RunWorkspaceContext);
  if (!value) {
    throw new Error("useRunWorkspace must be used inside the run workspace");
  }
  return value;
}

export function RunWorkspaceLayout({ children }: { children: ReactNode }) {
  const params = useParams<{ runId: string }>();
  const runId = Array.isArray(params.runId) ? params.runId[0] : params.runId;
  const locale = useLocale();
  const pathname = usePathname();
  const [overview, setOverview] = useState<RunOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!runId) return;
    try {
      const data = await apiFetch<RunOverview>(`/runs/${runId}/overview`);
      setOverview(data);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : String(caught));
    } finally {
      setLoading(false);
    }
  }, [runId]);

  // Tab pages register their own loaders so the header button refreshes both
  // the run overview and whatever list the current tab shows.
  const refreshHandlers = useRef(new Set<RefreshHandler>());
  const registerRefresh = useCallback((handler: RefreshHandler) => {
    refreshHandlers.current.add(handler);
    return () => {
      refreshHandlers.current.delete(handler);
    };
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      await Promise.allSettled([
        reload(),
        ...Array.from(refreshHandlers.current, (handler) => handler()),
      ]);
    } finally {
      setLoading(false);
    }
  }, [reload]);

  const [cancelArmed, setCancelArmed] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const active = Boolean(
    overview && !TERMINAL_RUN_STATUSES.has(overview.run.status),
  );

  const cancelRun = useCallback(async () => {
    setCancelling(true);
    setCancelError(null);
    try {
      await apiFetch(`/runs/${runId}/cancel`, { method: "POST" });
      setCancelArmed(false);
      await reload();
    } catch (caught) {
      setCancelError(caught instanceof ApiError ? caught.detail : String(caught));
    } finally {
      setCancelling(false);
    }
  }, [reload, runId]);

  useEffect(() => {
    if (!active) setCancelArmed(false);
  }, [active]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const tabs = useMemo(() => {
    const base = `/runs/${runId}`;
    return [
      {
        href: base,
        label: pick(locale, "运行概览", "Overview"),
        icon: Gauge,
        exact: true,
      },
      {
        href: `${base}/approvals`,
        label: pick(locale, "审阅与审批", "Review & approve"),
        icon: ClipboardCheck,
        pending: Boolean(overview?.pending_approval),
      },
      {
        href: `${base}/sources`,
        label: pick(locale, "来源", "Sources"),
        icon: BookOpenCheck,
      },
      {
        href: `${base}/evidence`,
        label: pick(locale, "证据", "Evidence"),
        icon: ScrollText,
      },
      {
        href: `${base}/article`,
        label: pick(locale, "长文与核验", "Article & verification"),
        icon: FileText,
      },
    ];
  }, [locale, overview?.pending_approval, runId]);

  const value: RunWorkspaceValue = {
    runId,
    overview,
    loading,
    error,
    reload,
    refresh,
    registerRefresh,
  };

  return (
    <RunWorkspaceContext.Provider value={value}>
      <div className="border-b border-border bg-surface/60">
        <div className="mx-auto w-full max-w-[1400px] px-6 py-6 lg:px-8">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2 text-xs text-foreground-subtle">
                <Link
                  href={overview ? `/projects/${overview.project.id}` : "/"}
                  className="inline-flex items-center gap-1 hover:text-foreground"
                >
                  <ArrowLeft className="h-3 w-3" />
                  {overview?.project.title ??
                    pick(locale, "返回项目", "Back to project")}
                </Link>
                <span>/</span>
                <span className="font-mono">run {shortId(runId)}</span>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2.5">
                <h1 className="font-display text-2xl font-semibold tracking-tight">
                  {overview
                    ? pick(locale, "研究运行", "Research run")
                    : pick(locale, "加载运行…", "Loading run…")}
                </h1>
                {overview ? (
                  <Badge
                    variant={runStatusVariant(overview.run.status)}
                    size="xs"
                    dot
                  >
                    {runStatusLabel(overview.run.status, locale)}
                  </Badge>
                ) : null}
              </div>
              {overview ? (
                <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-foreground-muted">
                  <span>
                    {pick(locale, "开始 ", "Started ")}
                    {formatDateTime(
                      overview.run.started_at ?? overview.run.created_at,
                      locale,
                    )}
                  </span>
                  <span>
                    {pick(locale, "用时 ", "Duration ")}
                    {formatDuration(
                      overview.run.started_at,
                      overview.run.finished_at,
                      locale,
                    )}
                  </span>
                  <span className="font-mono">
                    {formatCost(overview.run.cost_usd)}
                  </span>
                  <span className="font-mono">
                    {overview.run.input_tokens + overview.run.output_tokens}{" "}
                    tokens
                  </span>
                </div>
              ) : null}
            </div>

            <div className="flex shrink-0 items-center gap-2 self-start lg:self-auto">
              {active ? (
                cancelArmed ? (
                  <>
                    <Button
                      variant="danger"
                      size="sm"
                      loading={cancelling}
                      onClick={() => void cancelRun()}
                    >
                      {pick(locale, "确认取消", "Confirm cancel")}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => setCancelArmed(false)}
                      disabled={cancelling}
                      aria-label={pick(locale, "返回", "Back")}
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setCancelArmed(true)}
                  >
                    <Square className="h-3.5 w-3.5" />
                    {pick(locale, "取消运行", "Cancel run")}
                  </Button>
                )
              ) : null}
              <Button
                variant="outline"
                size="sm"
                onClick={() => void refresh()}
                disabled={loading}
              >
                <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
                {pick(locale, "刷新", "Refresh")}
              </Button>
            </div>
          </div>

          {cancelError ? (
            <Notice
              variant="danger"
              title={pick(locale, "取消运行失败", "Could not cancel the run")}
              className="mt-4"
            >
              {cancelError}
            </Notice>
          ) : null}

          <nav className="mt-5 flex gap-1 overflow-x-auto scrollbar-none">
            {tabs.map((tab) => {
              const active = tab.exact
                ? pathname === tab.href
                : pathname.startsWith(tab.href);
              const Icon = tab.icon;
              return (
                <Link
                  key={tab.href}
                  href={tab.href}
                  className={cn(
                    "inline-flex shrink-0 items-center gap-2 rounded-full px-3.5 py-2 text-xs font-medium transition-colors",
                    active
                      ? "bg-accent text-accent-fg"
                      : "text-foreground-muted hover:bg-hover-bg hover:text-foreground",
                  )}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {tab.label}
                  {tab.pending ? (
                    <span
                      className={cn(
                        "h-1.5 w-1.5 rounded-full",
                        active ? "bg-accent-fg" : "bg-orange",
                      )}
                      aria-label={pick(locale, "有待审批项", "Approval pending")}
                    />
                  ) : null}
                </Link>
              );
            })}
          </nav>
        </div>
      </div>

      <main className="mx-auto w-full max-w-[1400px] px-6 py-8 lg:px-8">
        {error && !overview ? (
          <ErrorState message={error} onRetry={() => void refresh()} />
        ) : loading && !overview ? (
          <LoadingState label={pick(locale, "加载运行数据…", "Loading run data…")} />
        ) : (
          children
        )}
      </main>
    </RunWorkspaceContext.Provider>
  );
}
