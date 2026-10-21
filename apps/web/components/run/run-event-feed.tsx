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
          {rows.map((event) => (
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
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
