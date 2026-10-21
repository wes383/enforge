import type { Locale } from "@/lib/types";

function intlLocale(locale: Locale): string {
  return locale === "zh" ? "zh-CN" : "en-US";
}

export function formatDateTime(
  value: string | null | undefined,
  locale: Locale,
): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(intlLocale(locale), {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function formatDate(
  value: string | null | undefined,
  locale: Locale,
): string {
  if (!value) return "—";
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(intlLocale(locale), {
    dateStyle: "medium",
  }).format(date);
}

export function formatNumber(value: number, locale: Locale): string {
  return new Intl.NumberFormat(intlLocale(locale)).format(value);
}

export function formatCost(value: string | number | null | undefined): string {
  const numeric = typeof value === "string" ? Number(value) : value;
  if (numeric === null || numeric === undefined || Number.isNaN(numeric)) {
    return "$0.0000";
  }
  return `$${numeric.toFixed(4)}`;
}

export function formatBytes(value: number | null | undefined): string {
  if (!value || value <= 0) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDuration(
  start: string | null | undefined,
  end: string | null | undefined,
  locale: Locale,
): string {
  if (!start) return "—";
  const startTime = new Date(start).getTime();
  const endTime = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(startTime) || Number.isNaN(endTime)) return "—";
  const seconds = Math.max(0, Math.round((endTime - startTime) / 1000));
  if (seconds < 60) {
    return locale === "zh" ? `${seconds} 秒` : `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes < 60) {
    return locale === "zh"
      ? `${minutes} 分 ${remainder} 秒`
      : `${minutes}m ${remainder}s`;
  }
  const hours = Math.floor(minutes / 60);
  return locale === "zh"
    ? `${hours} 小时 ${minutes % 60} 分`
    : `${hours}h ${minutes % 60}m`;
}

export function shortId(value: string): string {
  return value.slice(0, 8);
}

export function truncate(value: string, length: number): string {
  if (value.length <= length) return value;
  return `${value.slice(0, length - 1)}…`;
}

export function formatLocator(locator: Record<string, unknown>): string[] {
  const parts: string[] = [];
  const page =
    locator.page ?? locator.page_number ?? locator.page_start ?? locator.pdf_page;
  if (page !== undefined && page !== null) {
    parts.push(`p. ${String(page)}`);
  }
  const section =
    locator.section_title ?? locator.section ?? locator.heading ?? locator.chapter;
  if (section) parts.push(String(section));
  const paragraph = locator.paragraph ?? locator.paragraph_index;
  if (paragraph !== undefined && paragraph !== null) {
    parts.push(`¶ ${String(paragraph)}`);
  }
  const line = locator.line ?? locator.line_start;
  if (line !== undefined && line !== null) {
    parts.push(`L${String(line)}`);
  }
  const charStart = locator.char_start ?? locator.character_start;
  const charEnd = locator.char_end ?? locator.character_end;
  if (charStart !== undefined && charEnd !== undefined) {
    parts.push(`${String(charStart)}–${String(charEnd)}`);
  }
  if (locator.chunk_index !== undefined) {
    parts.push(`chunk ${String(locator.chunk_index)}`);
  }
  return parts;
}
