import type { Locale } from "@/lib/types";

export type BadgeVariant =
  | "default"
  | "secondary"
  | "outline"
  | "success"
  | "warning"
  | "danger"
  | "info";

type Copy = { zh: string; en: string };

function label(value: Copy | undefined, locale: Locale): string {
  if (!value) return "";
  return locale === "zh" ? value.zh : value.en;
}

export const RUN_STATUS: Record<string, { label: Copy; variant: BadgeVariant }> = {
  queued: { label: { zh: "排队中", en: "Queued" }, variant: "secondary" },
  planning: { label: { zh: "规划中", en: "Planning" }, variant: "info" },
  searching: { label: { zh: "检索中", en: "Searching" }, variant: "info" },
  processing_sources: {
    label: { zh: "处理来源", en: "Processing sources" },
    variant: "info",
  },
  building_evidence: {
    label: { zh: "构建证据", en: "Building evidence" },
    variant: "info",
  },
  writing: { label: { zh: "写作中", en: "Writing" }, variant: "info" },
  verifying: { label: { zh: "核验中", en: "Verifying" }, variant: "info" },
  awaiting_approval: {
    label: { zh: "待审批", en: "Awaiting approval" },
    variant: "warning",
  },
  completed: { label: { zh: "已完成", en: "Completed" }, variant: "success" },
  failed: { label: { zh: "失败", en: "Failed" }, variant: "danger" },
  cancelled: { label: { zh: "已取消", en: "Cancelled" }, variant: "secondary" },
};

export const TERMINAL_RUN_STATUSES = new Set(["completed", "failed", "cancelled"]);

export function runStatusLabel(status: string, locale: Locale): string {
  return label(RUN_STATUS[status]?.label, locale) || status;
}

export function runStatusVariant(status: string): BadgeVariant {
  return RUN_STATUS[status]?.variant ?? "secondary";
}

export const STAGES: { key: string; statuses: string[]; label: Copy }[] = [
  {
    key: "planning",
    statuses: ["queued", "planning"],
    label: { zh: "研究规划", en: "Research plan" },
  },
  {
    key: "searching",
    statuses: ["searching"],
    label: { zh: "文献检索", en: "Search" },
  },
  {
    key: "processing_sources",
    statuses: ["processing_sources"],
    label: { zh: "来源解析", en: "Source processing" },
  },
  {
    key: "building_evidence",
    statuses: ["building_evidence"],
    label: { zh: "证据构建", en: "Evidence" },
  },
  {
    key: "writing",
    statuses: ["writing"],
    label: { zh: "长文写作", en: "Writing" },
  },
  {
    key: "verifying",
    statuses: ["verifying"],
    label: { zh: "引用核验", en: "Verification" },
  },
];

export function stageLabel(key: string, locale: Locale): string {
  const stage = STAGES.find((item) => item.key === key);
  return label(stage?.label, locale) || key;
}

export function stageIndexForStatus(status: string): number {
  if (status === "completed") return STAGES.length;
  if (status === "cancelled") return 0;
  return STAGES.findIndex((item) => item.statuses.includes(status));
}

export const EVENT_LABELS: Record<string, Copy> = {
  run_created: { zh: "运行已创建", en: "Run created" },
  phase_started: { zh: "阶段开始", en: "Stage started" },
  phase_completed: { zh: "阶段完成", en: "Stage completed" },
  phase_failed: { zh: "阶段失败", en: "Stage failed" },
  progress: { zh: "进度", en: "Progress" },
  source_fetched: { zh: "来源抓取", en: "Source fetched" },
  source_parsed: { zh: "来源解析", en: "Source parsed" },
  evidence_extracted: { zh: "证据提取", en: "Evidence extracted" },
  section_generated: { zh: "章节生成", en: "Section generated" },
  citation_audit_failed: { zh: "引用核验失败", en: "Citation audit failed" },
  awaiting_approval: { zh: "等待审批", en: "Awaiting approval" },
  approval_decided: { zh: "审批完成", en: "Approval decided" },
  run_cancelled: { zh: "运行取消", en: "Run cancelled" },
  error: { zh: "错误", en: "Error" },
  other: { zh: "其他", en: "Other" },
  cancel_requested: { zh: "取消请求", en: "Cancel requested" },
  retry_scheduled: { zh: "重试已安排", en: "Retry scheduled" },
};

export function eventLabel(type: string, locale: Locale): string {
  return label(EVENT_LABELS[type], locale) || type;
}

export const EVIDENCE_TYPE: Record<string, { label: Copy; variant: BadgeVariant }> = {
  direct_support: {
    label: { zh: "直接支持", en: "Direct support" },
    variant: "success",
  },
  refutation: { label: { zh: "反驳证据", en: "Refutation" }, variant: "danger" },
  contextual: {
    label: { zh: "背景", en: "Context" },
    variant: "secondary",
  },
  definition: { label: { zh: "定义", en: "Definition" }, variant: "info" },
  statistic: { label: { zh: "数据", en: "Statistic" }, variant: "info" },
  qualitative: {
    label: { zh: "定性", en: "Qualitative" },
    variant: "secondary",
  },
};

export function evidenceTypeLabel(type: string, locale: Locale): string {
  return label(EVIDENCE_TYPE[type]?.label, locale) || type;
}

export const EVIDENCE_VERIFICATION: Record<string, { label: Copy; variant: BadgeVariant }> = {
  verified: { label: { zh: "已核验", en: "Verified" }, variant: "success" },
  pending: { label: { zh: "待核验", en: "Pending" }, variant: "warning" },
  unverified: { label: { zh: "未核验", en: "Unverified" }, variant: "secondary" },
  invalid: { label: { zh: "无效", en: "Invalid" }, variant: "danger" },
};

export function evidenceVerificationLabel(
  status: string,
  locale: Locale,
): string {
  return label(EVIDENCE_VERIFICATION[status]?.label, locale) || status;
}

export function evidenceVerificationVariant(status: string): BadgeVariant {
  return EVIDENCE_VERIFICATION[status]?.variant ?? "secondary";
}

export const COVERAGE: Record<string, { label: Copy; variant: BadgeVariant }> = {
  open: { label: { zh: "待覆盖", en: "Open" }, variant: "secondary" },
  partial: { label: { zh: "部分覆盖", en: "Partial" }, variant: "warning" },
  covered: { label: { zh: "已覆盖", en: "Covered" }, variant: "success" },
  insufficient: {
    label: { zh: "证据不足", en: "Insufficient" },
    variant: "danger",
  },
};

export function coverageLabel(status: string, locale: Locale): string {
  return label(COVERAGE[status]?.label, locale) || status;
}

export const CLAIM_STATUS: Record<string, { label: Copy; variant: BadgeVariant }> = {
  SUPPORTED: { label: { zh: "有支持", en: "Supported" }, variant: "success" },
  PARTIAL: { label: { zh: "部分支持", en: "Partial" }, variant: "warning" },
  CONTRADICTED: {
    label: { zh: "存在矛盾", en: "Contradicted" },
    variant: "danger",
  },
  UNSUPPORTED: {
    label: { zh: "无支持", en: "Unsupported" },
    variant: "danger",
  },
  UNVERIFIABLE: {
    label: { zh: "无法核验", en: "Unverifiable" },
    variant: "secondary",
  },
  PENDING: { label: { zh: "待核验", en: "Pending" }, variant: "warning" },
};

export function claimStatusLabel(status: string, locale: Locale): string {
  return label(CLAIM_STATUS[status]?.label, locale) || status;
}

export function claimStatusVariant(status: string): BadgeVariant {
  return CLAIM_STATUS[status]?.variant ?? "secondary";
}

export const PARSER_STATUS: Record<string, { label: Copy; variant: BadgeVariant }> = {
  pending: { label: { zh: "待解析", en: "Pending" }, variant: "secondary" },
  parsing: { label: { zh: "解析中", en: "Parsing" }, variant: "info" },
  parsed: { label: { zh: "已解析", en: "Parsed" }, variant: "success" },
  failed: { label: { zh: "解析失败", en: "Failed" }, variant: "danger" },
  skipped: { label: { zh: "无需解析", en: "No parsing" }, variant: "secondary" },
};

export function parserStatusLabel(status: string, locale: Locale): string {
  return label(PARSER_STATUS[status]?.label, locale) || status;
}

export function parserStatusVariant(status: string): BadgeVariant {
  return PARSER_STATUS[status]?.variant ?? "secondary";
}

export const FETCH_STATUS: Record<string, { label: Copy; variant: BadgeVariant }> = {
  success: { label: { zh: "已抓取", en: "Fetched" }, variant: "success" },
  fetched: { label: { zh: "已抓取", en: "Fetched" }, variant: "success" },
  failed: { label: { zh: "抓取失败", en: "Fetch failed" }, variant: "danger" },
  skipped: { label: { zh: "已跳过", en: "Skipped" }, variant: "secondary" },
  pending: { label: { zh: "待抓取", en: "Pending" }, variant: "warning" },
};

export function fetchStatusLabel(status: string, locale: Locale): string {
  return label(FETCH_STATUS[status]?.label, locale) || status;
}

export function fetchStatusVariant(status: string): BadgeVariant {
  return FETCH_STATUS[status]?.variant ?? "secondary";
}

export const CONTENT_KINDS: Record<string, Copy> = {
  search_snippet: { zh: "搜索摘要", en: "Search snippet" },
  full_text: { zh: "全文", en: "Full text" },
  uploaded_document: { zh: "上传文档", en: "Uploaded document" },
};

export function contentKindLabel(kind: string, locale: Locale): string {
  return label(CONTENT_KINDS[kind], locale) || kind;
}

export const SOURCE_TYPES: Record<string, Copy> = {
  web_page: { zh: "网页", en: "Web page" },
  journal_article: { zh: "期刊论文", en: "Journal article" },
  conference_paper: { zh: "会议论文", en: "Conference paper" },
  book: { zh: "图书", en: "Book" },
  report: { zh: "报告", en: "Report" },
  news: { zh: "新闻", en: "News" },
  preprint: { zh: "预印本", en: "Preprint" },
  thesis: { zh: "学位论文", en: "Thesis" },
  government_document: { zh: "政府文件", en: "Government document" },
  other: { zh: "其他", en: "Other" },
};

export function sourceTypeLabel(type: string, locale: Locale): string {
  return label(SOURCE_TYPES[type], locale) || type;
}

export const APPROVAL_STATUS: Record<string, { label: Copy; variant: BadgeVariant }> = {
  pending: { label: { zh: "待审批", en: "Pending" }, variant: "warning" },
  approved: { label: { zh: "已批准", en: "Approved" }, variant: "success" },
  rejected: { label: { zh: "已驳回", en: "Rejected" }, variant: "danger" },
  modified: { label: { zh: "修改后批准", en: "Modified" }, variant: "info" },
  cancelled: { label: { zh: "已取消", en: "Cancelled" }, variant: "secondary" },
};

export const ARTIFACT_TYPES: Record<string, Copy> = {
  research_report: { zh: "研究报告", en: "Research report" },
  reference_list: { zh: "参考文献", en: "Reference list" },
  markdown_export: { zh: "Markdown 导出", en: "Markdown export" },
  evidence_matrix: { zh: "证据矩阵", en: "Evidence matrix" },
  citation_audit: { zh: "核验报告", en: "Verification report" },
};

export function artifactTypeLabel(type: string, locale: Locale): string {
  return label(ARTIFACT_TYPES[type], locale) || type;
}

export const SECTION_AUDIT: Record<string, { label: Copy; variant: BadgeVariant }> = {
  passed: { label: { zh: "引用通过", en: "Citations pass" }, variant: "success" },
  warning: { label: { zh: "引用警告", en: "Citation warning" }, variant: "warning" },
  failed: { label: { zh: "引用失败", en: "Citation failed" }, variant: "danger" },
};

export function sectionAuditLabel(status: string | null, locale: Locale): string {
  if (!status) return locale === "zh" ? "未核验" : "Not audited";
  return label(SECTION_AUDIT[status]?.label, locale) || status;
}

export function sectionAuditVariant(status: string | null): BadgeVariant {
  if (!status) return "secondary";
  return SECTION_AUDIT[status]?.variant ?? "secondary";
}

export const GENERATION_STATUS: Record<string, { label: Copy; variant: BadgeVariant }> = {
  pending: { label: { zh: "待生成", en: "Pending" }, variant: "secondary" },
  generating: { label: { zh: "生成中", en: "Generating" }, variant: "info" },
  drafted: { label: { zh: "草稿", en: "Drafted" }, variant: "info" },
  verified: { label: { zh: "已核验", en: "Verified" }, variant: "success" },
  failed: { label: { zh: "核验失败", en: "Failed" }, variant: "danger" },
};

export function generationStatusLabel(status: string, locale: Locale): string {
  return label(GENERATION_STATUS[status]?.label, locale) || status;
}

export function generationStatusVariant(status: string): BadgeVariant {
  return GENERATION_STATUS[status]?.variant ?? "secondary";
}

export const TASK_STATUS_LABELS = {
  completed: { zh: "已完成", en: "Completed" },
  running: { zh: "进行中", en: "Running" },
  failed: { zh: "失败", en: "Failed" },
} as const;
