"use client";

import {
  Check,
  Circle,
  CircleAlert,
  Loader2,
} from "lucide-react";

import { STAGES, stageIndexForStatus, stageLabel } from "@/lib/labels";
import { pick, useLocale } from "@/lib/locale";
import type { Approval, RunEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

function inferredStageIndex({
  status,
  events,
  pendingApproval,
}: {
  status: string;
  events: RunEvent[];
  pendingApproval: Approval | null;
}): number {
  if (status === "completed") return STAGES.length;
  if (status === "awaiting_approval" && pendingApproval) {
    return 0;
  }
  const direct = stageIndexForStatus(status);
  if (direct >= 0) return direct;

  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (!event.stage) continue;
    const stage = stageIndexForStatus(event.stage);
    if (stage >= 0) return stage;
  }
  return 0;
}

export function StageProgress({
  status,
  events,
  pendingApproval,
}: {
  status: string;
  events: RunEvent[];
  pendingApproval: Approval | null;
}) {
  const locale = useLocale();
  const current = inferredStageIndex({ status, events, pendingApproval });
  const failed = status === "failed";
  const cancelled = status === "cancelled";
  const awaiting = status === "awaiting_approval";

  return (
    <ol className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
      {STAGES.map((stage, index) => {
        const complete = index < current || status === "completed";
        const active = index === current && !complete;
        const state = complete
          ? "complete"
          : active
            ? failed
              ? "failed"
              : awaiting
                ? "waiting"
                : cancelled
                  ? "cancelled"
                  : "active"
            : "pending";
        const Icon = complete ? Check : active ? Loader2 : Circle;
        return (
          <li
            key={stage.key}
            className={cn(
              "flex items-center gap-3 rounded-md border px-3.5 py-3",
              state === "complete" && "border-border bg-surface",
              state === "active" && "border-blue-border bg-blue-soft/40",
              state === "waiting" && "border-orange-border bg-orange-soft/40",
              state === "failed" && "border-red-border bg-red-soft/40",
              (state === "pending" || state === "cancelled") &&
                "border-border bg-surface/50",
            )}
          >
            <span
              className={cn(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-[11px] font-medium",
                state === "complete" && "border-green-border bg-green-soft text-green-fg",
                state === "active" && "border-blue-border bg-blue-soft text-blue-fg",
                state === "waiting" && "border-orange-border bg-orange-soft text-orange-fg",
                state === "failed" && "border-red-border bg-red-soft text-red-fg",
                (state === "pending" || state === "cancelled") &&
                  "border-border text-foreground-subtle",
              )}
            >
              {state === "active" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : state === "failed" ? (
                <CircleAlert className="h-3.5 w-3.5" />
              ) : (
                <Icon className="h-3.5 w-3.5" />
              )}
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">
                {stageLabel(stage.key, locale)}
              </p>
              <p className="text-[11px] text-foreground-subtle">
                {state === "complete"
                  ? pick(locale, "已完成", "Completed")
                  : state === "active"
                    ? pick(locale, "执行中", "Running")
                    : state === "waiting"
                      ? pick(locale, "等待审批", "Awaiting approval")
                      : state === "failed"
                        ? pick(locale, "失败", "Failed")
                        : state === "cancelled"
                          ? pick(locale, "已取消", "Cancelled")
                          : pick(locale, "待执行", "Pending")}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
