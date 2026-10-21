"use client";

import {
  Activity,
  ArrowUpRight,
  Database,
  HardDrive,
  Languages,
  Moon,
  RefreshCw,
  Server,
  Sun,
} from "lucide-react";
import { useTheme } from "next-themes";
import Link from "next/link";
import {
  useEffect,
  useState,
  useSyncExternalStore,
  type ComponentType,
} from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Heading, Text } from "@/components/ui/typography";
import { cn } from "@/lib/utils";

const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

const LOCALE_STORAGE_KEY = "enforge.locale";
const LOCALE_EVENT = "enforge:locale-change";

type Locale = "zh" | "en";
type ServiceState = "ok" | "error" | "checking" | "unknown";
type StatusVariant = "success" | "warning" | "danger" | "secondary";

type ReadyCheck = {
  status?: string;
  latency_ms?: number;
  error?: string | null;
};

type ReadyPayload = {
  status?: string;
  checks?: Record<string, ReadyCheck>;
};

type SystemState = {
  phase: "checking" | "online" | "offline";
  apiVersion: string | null;
  apiLatencyMs: number | null;
  environment: string | null;
  readyStatus: string | null;
  checks: Record<string, ReadyCheck>;
  lastChecked: Date | null;
};

const COPY = {
  zh: {
    brandSubtitle: "Deep Research Writing Agent",
    phase: "阶段 07",
    title: "系统状态",
    refresh: "刷新",
    docs: "API 文档",
    languageAria: "切换到英文",
    theme: "切换主题",
    connection: "连接",
    endpoint: "端点",
    environment: "环境",
    version: "版本",
    lastChecked: "最后检查",
    never: "尚未检查",
    api: "API",
    database: "PostgreSQL",
    redis: "Redis",
    storage: "文件存储",
    apiDetail: "FastAPI",
    databaseDetail: "pgvector",
    redisDetail: "队列与缓存",
    storageDetail: "共享卷",
    ok: "正常",
    error: "异常",
    checking: "检查中",
    unknown: "未知",
    allReady: "全部正常",
    degraded: "部分异常",
    apiOffline: "API 不可达",
    statusUnknown: "状态未知",
  },
  en: {
    brandSubtitle: "Deep Research Writing Agent",
    phase: "Phase 07",
    title: "System status",
    refresh: "Refresh",
    docs: "API docs",
    languageAria: "Switch to Chinese",
    theme: "Toggle theme",
    connection: "Connection",
    endpoint: "Endpoint",
    environment: "Environment",
    version: "Version",
    lastChecked: "Last checked",
    never: "Not checked yet",
    api: "API",
    database: "PostgreSQL",
    redis: "Redis",
    storage: "Storage",
    apiDetail: "FastAPI",
    databaseDetail: "pgvector",
    redisDetail: "Queue & cache",
    storageDetail: "Shared volume",
    ok: "Operational",
    error: "Error",
    checking: "Checking",
    unknown: "Unknown",
    allReady: "All systems operational",
    degraded: "Partial disruption",
    apiOffline: "API unreachable",
    statusUnknown: "Status unknown",
  },
} as const;

let inMemoryLocale: Locale | null = null;

function getLocaleSnapshot(): Locale {
  if (inMemoryLocale) return inMemoryLocale;
  try {
    const saved = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    if (saved === "zh" || saved === "en") return saved;
  } catch {
    // localStorage can be unavailable in private browsing contexts.
  }
  return navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
}

function getServerLocaleSnapshot(): Locale {
  return "zh";
}

function subscribeToLocale(onStoreChange: () => void): () => void {
  window.addEventListener("storage", onStoreChange);
  window.addEventListener(LOCALE_EVENT, onStoreChange);
  return () => {
    window.removeEventListener("storage", onStoreChange);
    window.removeEventListener(LOCALE_EVENT, onStoreChange);
  };
}

function persistLocale(locale: Locale): void {
  inMemoryLocale = locale;
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Keep the in-memory preference for the current session.
  }
  window.dispatchEvent(new Event(LOCALE_EVENT));
}

const STATUS_VARIANT: Record<ServiceState, StatusVariant> = {
  ok: "success",
  error: "danger",
  checking: "warning",
  unknown: "secondary",
};

function StatusCard({
  icon: Icon,
  label,
  detail,
  value,
  unit,
  state,
  stateLabel,
}: {
  icon: ComponentType<{ className?: string }>;
  label: string;
  detail: string;
  value: string;
  unit?: string;
  state: ServiceState;
  stateLabel: string;
}) {
  return (
    <Card className="p-5" hoverable>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-md bg-hover-bg text-foreground-muted">
            <Icon className="h-4 w-4" />
          </span>
          <span className="text-sm font-medium">{label}</span>
        </div>
        <Badge variant={STATUS_VARIANT[state]} size="xs" dot>
          {stateLabel}
        </Badge>
      </div>
      <div className="mt-5 flex items-baseline gap-1.5">
        <span className="font-mono text-2xl font-medium tracking-tight">
          {value}
        </span>
        {unit ? (
          <span className="text-xs text-foreground-subtle">{unit}</span>
        ) : null}
      </div>
      <p className="mt-1 text-xs text-foreground-subtle">{detail}</p>
    </Card>
  );
}

export default function Home() {
  const locale = useSyncExternalStore(
    subscribeToLocale,
    getLocaleSnapshot,
    getServerLocaleSnapshot,
  );
  const { resolvedTheme, setTheme } = useTheme();
  const [refreshKey, setRefreshKey] = useState(0);
  const [system, setSystem] = useState<SystemState>({
    phase: "checking",
    apiVersion: null,
    apiLatencyMs: null,
    environment: null,
    readyStatus: null,
    checks: {},
    lastChecked: null,
  });

  const t = COPY[locale];

  useEffect(() => {
    document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
  }, [locale]);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8000);

    async function checkSystem() {
      const startedAt = performance.now();
      try {
        const healthResponse = await fetch(`${API_BASE_URL}/api/v1/health`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!healthResponse.ok) {
          throw new Error("Health check failed");
        }
        const health = (await healthResponse.json()) as {
          version?: string;
          environment?: string;
        };
        const apiLatencyMs = Math.round(performance.now() - startedAt);

        let ready: ReadyPayload | null = null;
        try {
          const readyResponse = await fetch(`${API_BASE_URL}/ready`, {
            cache: "no-store",
            signal: controller.signal,
          });
          ready = (await readyResponse.json()) as ReadyPayload;
        } catch (error) {
          if (controller.signal.aborted) throw error;
        }

        if (!active) return;
        setSystem({
          phase: "online",
          apiVersion: health.version ?? null,
          apiLatencyMs,
          environment: health.environment ?? null,
          readyStatus: ready?.status ?? null,
          checks: ready?.checks ?? {},
          lastChecked: new Date(),
        });
      } catch {
        if (!active) return;
        setSystem({
          phase: "offline",
          apiVersion: null,
          apiLatencyMs: null,
          environment: null,
          readyStatus: null,
          checks: {},
          lastChecked: new Date(),
        });
      } finally {
        window.clearTimeout(timeout);
      }
    }

    void checkSystem();
    return () => {
      active = false;
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [refreshKey]);

  useEffect(() => {
    const interval = window.setInterval(
      () => setRefreshKey((value) => value + 1),
      30_000,
    );
    return () => window.clearInterval(interval);
  }, []);

  function refreshStatus() {
    setSystem((previous) => ({ ...previous, phase: "checking" }));
    setRefreshKey((value) => value + 1);
  }

  function checkState(key: string): ServiceState {
    if (system.phase === "checking") return "checking";
    if (system.phase === "offline") return "unknown";
    const check = system.checks[key];
    if (!check?.status) return "unknown";
    return check.status === "ok" ? "ok" : "error";
  }

  function latencyText(key: string): string | null {
    const value = system.checks[key]?.latency_ms;
    return typeof value === "number" ? value.toFixed(1) : null;
  }

  function combinedState(...states: ServiceState[]): ServiceState {
    if (states.includes("error")) return "error";
    if (states.includes("checking")) return "checking";
    if (states.every((state) => state === "ok")) return "ok";
    return "unknown";
  }

  const isChecking = system.phase === "checking";
  const isOffline = system.phase === "offline";
  const apiState: ServiceState = isChecking
    ? "checking"
    : isOffline
      ? "error"
      : "ok";

  const overall: { variant: StatusVariant; label: string } = isChecking
    ? { variant: "warning", label: t.checking }
    : isOffline
      ? { variant: "danger", label: t.apiOffline }
      : system.readyStatus === "ready"
        ? { variant: "success", label: t.allReady }
        : system.readyStatus === "not_ready"
          ? { variant: "warning", label: t.degraded }
          : { variant: "warning", label: t.statusUnknown };

  const stateLabels: Record<ServiceState, string> = {
    ok: t.ok,
    error: t.error,
    checking: t.checking,
    unknown: t.unknown,
  };

  const databaseLatency = latencyText("database");
  const redisLatency = latencyText("redis");
  const storageLatency = latencyText("storage");
  const apiLatency =
    system.apiLatencyMs !== null ? String(system.apiLatencyMs) : null;

  const cards = [
    {
      key: "api",
      icon: Server,
      label: t.api,
      detail: t.apiDetail,
      value: apiLatency ?? "—",
      unit: apiLatency ? "ms" : undefined,
      state: apiState,
    },
    {
      key: "database",
      icon: Database,
      label: t.database,
      detail: t.databaseDetail,
      value: databaseLatency ?? "—",
      unit: databaseLatency ? "ms" : undefined,
      state: combinedState(checkState("database"), checkState("schema")),
    },
    {
      key: "redis",
      icon: Activity,
      label: t.redis,
      detail: t.redisDetail,
      value: redisLatency ?? "—",
      unit: redisLatency ? "ms" : undefined,
      state: checkState("redis"),
    },
    {
      key: "storage",
      icon: HardDrive,
      label: t.storage,
      detail: t.storageDetail,
      value: storageLatency ?? "—",
      unit: storageLatency ? "ms" : undefined,
      state: checkState("storage"),
    },
  ];

  const timeLabel = system.lastChecked
    ? new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-US", {
        dateStyle: "medium",
        timeStyle: "medium",
      }).format(system.lastChecked)
    : t.never;

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <header className="sticky top-0 z-sticky border-b border-border bg-background">
        <div className="mx-auto flex min-h-16 max-w-[1200px] items-center justify-between gap-4 px-6 py-2 lg:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <Link
              href="/"
              className="shrink-0 font-display text-base font-bold leading-none tracking-tight transition-colors hover:text-foreground-muted"
            >
              Enforge
            </Link>
            <Badge
              variant="secondary"
              size="xs"
              className="hidden shrink-0 sm:inline-flex"
            >
              {t.phase}
            </Badge>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <Button asChild variant="ghost" size="sm" className="px-3">
              <a
                href={`${API_BASE_URL}/docs`}
                target="_blank"
                rel="noreferrer"
                aria-label={t.docs}
              >
                <span className="hidden sm:inline">{t.docs}</span>
                <ArrowUpRight className="h-3.5 w-3.5" />
              </a>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => persistLocale(locale === "zh" ? "en" : "zh")}
              aria-label={t.languageAria}
              className="gap-1.5 px-3"
            >
              <Languages className="h-4 w-4" />
              <span className="text-xs">{locale === "zh" ? "中" : "EN"}</span>
            </Button>
            <Button
              variant="outline"
              size="icon-sm"
              onClick={() =>
                setTheme(resolvedTheme === "dark" ? "light" : "dark")
              }
              aria-label={t.theme}
            >
              <Sun className="h-4 w-4 dark:hidden" />
              <Moon className="hidden h-4 w-4 dark:block" />
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1200px] flex-1 px-6 pb-20 pt-10 lg:px-8 lg:pt-14">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <Heading level="h3" as="h1">
              {t.title}
            </Heading>
            <Text variant="muted" className="mt-1 text-sm">
              {t.brandSubtitle}
            </Text>
          </div>

          <div className="flex flex-wrap items-center gap-2" aria-live="polite">
            <Badge variant={overall.variant} size="sm" dot>
              {overall.label}
            </Badge>
            <Button
              variant="outline"
              size="sm"
              onClick={refreshStatus}
              disabled={isChecking}
            >
              <RefreshCw
                className={cn("h-4 w-4", isChecking && "animate-spin")}
              />
              {t.refresh}
            </Button>
          </div>
        </div>

        <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {cards.map((card) => (
            <StatusCard
              key={card.key}
              icon={card.icon}
              label={card.label}
              detail={card.detail}
              value={card.value}
              unit={card.unit}
              state={card.state}
              stateLabel={stateLabels[card.state]}
            />
          ))}
        </div>

        <Card className="mt-3 p-5">
          <h2 className="font-display text-base font-semibold tracking-tight">
            {t.connection}
          </h2>
          <dl className="mt-4 grid gap-5 border-t border-border pt-5 sm:grid-cols-2 lg:grid-cols-4">
            <div className="min-w-0">
              <dt className="text-xs text-foreground-subtle">{t.endpoint}</dt>
              <dd className="mt-1 truncate">
                <a
                  href={API_BASE_URL}
                  target="_blank"
                  rel="noreferrer"
                  className="font-mono text-xs text-foreground transition-colors hover:text-foreground-muted"
                >
                  {API_BASE_URL}
                </a>
              </dd>
            </div>
            <div>
              <dt className="text-xs text-foreground-subtle">{t.environment}</dt>
              <dd className="mt-1 text-sm">{system.environment ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-xs text-foreground-subtle">{t.version}</dt>
              <dd className="mt-1 font-mono text-sm">
                {system.apiVersion ? `v${system.apiVersion}` : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-foreground-subtle">
                {t.lastChecked}
              </dt>
              <dd className="mt-1 text-sm">{timeLabel}</dd>
            </div>
          </dl>
        </Card>
      </main>
    </div>
  );
}
