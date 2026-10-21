"use client";

import { AlertTriangle, Inbox, Loader2, RefreshCw } from "lucide-react";
import type * as React from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { pick, useLocale } from "@/lib/locale";
import { cn } from "@/lib/utils";

export function LoadingState({
  label,
  className,
}: {
  label?: string;
  className?: string;
}) {
  const locale = useLocale();
  return (
    <div
      className={cn(
        "flex min-h-[160px] flex-col items-center justify-center gap-3 rounded-lg border border-border bg-surface p-8",
        className,
      )}
      role="status"
      aria-live="polite"
    >
      <Loader2 className="h-5 w-5 animate-spin text-foreground-subtle" />
      <p className="text-sm text-foreground-muted">
        {label ?? pick(locale, "加载中…", "Loading…")}
      </p>
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
  className,
}: {
  message: string;
  onRetry?: () => void;
  className?: string;
}) {
  const locale = useLocale();
  return (
    <Card className={cn("border-red-border bg-red-soft/40 p-6", className)}>
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-red-fg" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-red-fg">
            {pick(locale, "请求失败", "Request failed")}
          </p>
          <p className="mt-1 break-words text-sm text-foreground-muted">{message}</p>
          {onRetry ? (
            <Button
              variant="outline"
              size="sm"
              className="mt-4"
              onClick={onRetry}
            >
              <RefreshCw className="h-3.5 w-3.5" />
              {pick(locale, "重试", "Retry")}
            </Button>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
  className,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
  icon?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex min-h-[200px] flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border bg-surface p-10 text-center",
        className,
      )}
    >
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-hover-bg text-foreground-subtle">
        {icon ?? <Inbox className="h-5 w-5" />}
      </span>
      <div>
        <p className="font-display text-base font-semibold tracking-tight">
          {title}
        </p>
        {description ? (
          <p className="mt-1 max-w-md text-sm text-foreground-muted">
            {description}
          </p>
        ) : null}
      </div>
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}

export function Notice({
  variant = "info",
  title,
  children,
  action,
  className,
}: {
  variant?: "info" | "success" | "warning" | "danger";
  title?: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  const styles = {
    info: "border-blue-border bg-blue-soft text-blue-fg",
    success: "border-green-border bg-green-soft text-green-fg",
    warning: "border-orange-border bg-orange-soft text-orange-fg",
    danger: "border-red-border bg-red-soft text-red-fg",
  } as const;
  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-md border px-4 py-3 text-sm sm:flex-row sm:items-start sm:justify-between",
        styles[variant],
        className,
      )}
    >
      <div className="min-w-0">
        {title ? <p className="font-medium">{title}</p> : null}
        {children ? (
          <div className={cn("break-words", title && "mt-0.5 opacity-90")}>
            {children}
          </div>
        ) : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}
