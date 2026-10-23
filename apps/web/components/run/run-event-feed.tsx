"use client";

import { CircleDot } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import { eventLabel, stageLabel } from "@/lib/labels";
import { pick, useLocale } from "@/lib/locale";
import type { RunEvent } from "@/lib/types";
import type { StreamStatus } from "@/hooks/use-run-event-stream";
import { cn } from "@/lib/utils";

function streamCopy(status: StreamStatus, locale: "zh" | "en"): string {
  if (status === "live") return pick(locale, "实时", "Live");
  if (status === "reconnecting") return pick(locale, "重连中", "Reconnecting");
  if (status === "closed") return pick(locale, "已结束", "Closed");
  return pick(locale, "连接中", "Connecting");
}

function streamColor(status: StreamStatus): string {
  if (status === "live") return "bg-green";
  if (status === "reconnecting") return "bg-orange animate-pulse";
  return "bg-foreground-faint";
}

function asText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function clip(value: string, limit: number): string {
  return value.length > limit ? `${value.slice(0, limit)}…` : value;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}

function agentToolDetail(event: RunEvent, locale: "zh" | "en"): string | null {
  if (!asText(event.payload.tool)) return null;
  const query = asText(event.payload.query);
  const direction = asText(event.payload.direction);
  const title = asText(event.payload.title);
  const status = asText(event.payload.status);
  const error = asText(event.payload.error);
  const rawResults = event.payload.results;
  const results =
    typeof rawResults === "number" && Number.isFinite(rawResults)
      ? rawResults
      : null;

  const parts: string[] = [];
  if (query) parts.push(`“${clip(query, 120)}”`);
  if (direction) {
    parts.push(
      direction === "references"
        ? pick(locale, "参考文献", "references")
        : pick(locale, "被引文献", "citing works"),
    );
  }
  if (title) {
    parts.push(
      pick(locale, `《${clip(title, 140)}》`, clip(title, 140)),
    );
  }
  if (results !== null) {
    parts.push(
      pick(
        locale,
        `${results} 条结果`,
        `${results} result${results === 1 ? "" : "s"}`,
      ),
    );
  }
  if (status) parts.push(status);
  if (error) {
    parts.push(
      pick(locale, `失败：${clip(error, 160)}`, `error: ${clip(error, 160)}`),
    );
  }
  return parts.length ? parts.join(" · ") : null;
}

function llmCallDetail(event: RunEvent, locale: "zh" | "en"): string | null {
  if (asNumber(event.payload.model_call) === null) return null;
  const title = asText(event.payload.title);
  const error = asText(event.payload.error);
  const input = asNumber(event.payload.input_tokens);
  const output = asNumber(event.payload.output_tokens);
  const duration = asNumber(event.payload.duration_ms);
  const tokens = input !== null && output !== null ? input + output : null;

  const parts: string[] = [];
  if (title) {
    parts.push(pick(locale, `《${clip(title, 140)}》`, clip(title, 140)));
  }
  if (tokens !== null) parts.push(`${tokens} tokens`);
  if (duration !== null) parts.push(formatDuration(duration));
  if (error) {
    parts.push(
      pick(locale, `失败：${clip(error, 160)}`, `error: ${clip(error, 160)}`),
    );
  }
  return parts.length ? parts.join(" · ") : null;
}

function eventDetail(event: RunEvent, locale: "zh" | "en"): string | null {
  return agentToolDetail(event, locale) ?? llmCallDetail(event, locale);
}

export function RunEventFeed({
  events,
  status,
  limit = 40,
}: {
  events: RunEvent[];
  status: StreamStatus;
  limit?: number;
}) {
  const locale = useLocale();
  const rows = events.slice(-limit).reverse();

  return (
    <Card className="flex flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3.5">
        <h2 className="font-display text-sm font-semibold tracking-tight">
          {pick(locale, "运行事件", "Run events")}
        </h2>
        <Badge variant="outline" size="xs" className="gap-1.5 text-foreground-muted">
          <span className={cn("h-1.5 w-1.5 rounded-full", streamColor(status))} />
          {streamCopy(status, locale)}
        </Badge>
      </div>
      {rows.length === 0 ? (
        <p className="px-5 py-8 text-center text-sm text-foreground-subtle">
          {pick(locale, "暂无事件", "No events yet")}
        </p>
      ) : (
        <ol className="max-h-[460px] divide-y divide-border overflow-y-auto">
          {rows.map((event) => {
            const detail = eventDetail(event, locale);
            return (
              <li key={event.id} className="flex gap-3 px-5 py-3">
                <CircleDot className="mt-0.5 h-3.5 w-3.5 shrink-0 text-foreground-faint" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="text-xs font-medium">
                      {eventLabel(event.event_type, locale)}
                    </span>
                    {event.stage ? (
                      <span className="text-[11px] text-foreground-subtle">
                        {stageLabel(event.stage, locale)}
                      </span>
                    ) : null}
                    <span className="ml-auto font-mono text-[10px] text-foreground-faint">
                      {formatDateTime(event.created_at, locale)}
                    </span>
                  </div>
                  {event.message ? (
                    <p className="mt-0.5 break-words text-xs leading-relaxed text-foreground-muted">
                      {event.message}
                    </p>
                  ) : null}
                  {detail ? (
                    <p className="mt-1 break-words text-[11px] leading-relaxed text-foreground-subtle">
                      {detail}
                    </p>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}
