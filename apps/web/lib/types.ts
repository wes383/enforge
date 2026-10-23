export type Locale = "zh" | "en";

export type ProjectLanguage = "zh" | "en";
export type ResearchDepth = "quick" | "standard" | "deep";
export type CitationStyle = "apa" | "ieee" | "chicago" | "mla";
export type ArticleType =
  | "explainer"
  | "mechanism_analysis"
  | "investigative_research"
  | "case_study"
  | "comparative_analysis"
  | "argument_critique"
  | "trend_scenario"
  | "decision_strategy"
  | "historical_development";

export type Project = {
  id: string;
  title: string;
  topic: string;
  research_scope: string | null;
  article_type: ArticleType;
  language: ProjectLanguage;
  target_audience: string | null;
  research_depth: ResearchDepth;
  citation_style: CitationStyle;
  target_words: number | null;
  date_from: string | null;
  date_to: string | null;
  budget_usd: string | number | null;
  status: "active" | "archived";
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ProjectListResponse = {
  items: Project[];
  total: number;
  limit: number;
  offset: number;
};

export type Run = {
  id: string;
  project_id: string;
  status: string;
  error_summary: string | null;
  cancel_requested_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  cost_usd: string | number;
  input_tokens: number;
  output_tokens: number;
  workflow_thread_id: string;
  checkpoint_id: string | null;
  created_at: string;
  updated_at: string;
};

export type RunListResponse = {
  items: Run[];
  total: number;
  limit: number;
  offset: number;
};

export type RunEvent = {
  id: number;
  run_id: string;
  event_type: string;
  stage: string | null;
  message: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};

export type EventListResponse = { items: RunEvent[] };

export type Approval = {
  id: string;
  project_id: string;
  run_id: string;
  kind: "plan" | "outline" | "evidence";
  status: "pending" | "approved" | "rejected" | "modified" | "cancelled";
  version: number;
  payload: Record<string, unknown>;
  feedback: string | null;
  decision_payload: Record<string, unknown> | null;
  created_at: string;
  decided_at: string | null;
};

export type ApprovalListResponse = { items: Approval[] };

export type OutlineSectionSummary = {
  id: string;
  title: string;
  role: string;
  purpose: string;
  search_terms: string[];
  target_words: number | null;
  priority: number;
  coverage_status: string;
  created_at: string;
};

export type RunOverview = {
  run: Run;
  project: Project;
  outline_sections: OutlineSectionSummary[];
  counts: {
    sources: number;
    evidence: number;
    claims: number;
    sections: number;
    artifacts: number;
  };
  source_status: {
    total: number;
    full_text: number;
    snippet_only: number;
    parsed: number;
    parse_failed: number;
    pending_parse: number;
  };
  search_status: {
    queries: number;
    completed: number;
    running: number;
    failed: number;
    results: number;
    rounds: number;
  };
  gaps: {
    section_id: string;
    section_title: string;
    severity: string;
    coverage_status: string;
  }[];
  pending_approval: Approval | null;
  latest_events: RunEvent[];
};

export type SourceVersion = {
  id: string;
  version_number: number;
  content_kind: string;
  mime_type: string | null;
  size_bytes: number | null;
  parser_status: string;
  parser_version: string | null;
  parse_error: string | null;
  fetched_url: string | null;
  fetched_at: string;
  has_stored_file: boolean;
  metadata: Record<string, unknown>;
};

export type Source = {
  id: string;
  title: string;
  authors: string[];
  published_at: string | null;
  doi: string | null;
  url: string | null;
  source_type: string;
  source_origin: string;
  venue: string | null;
  publisher: string | null;
  metadata: Record<string, unknown>;
  versions: SourceVersion[];
  section_ids: string[];
  evidence_count: number;
  fetch_status: string | null;
};

export type SourceListResponse = { items: Source[] };

export type UploadedFile = {
  file_id: string;
  source_id: string;
  project_id: string;
  original_name: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  parser_status: string;
  parser_version: string | null;
  parse_error: string | null;
  created_at: string;
};

export type FileListResponse = { items: UploadedFile[] };

export type Evidence = {
  id: string;
  source_id: string;
  source_version_id: string;
  outline_section_id: string | null;
  evidence_type: string;
  claim_summary: string;
  quoted_text: string;
  context_before: string | null;
  context_after: string | null;
  locator: Record<string, unknown>;
  verification_status: string;
  source_title: string;
  source_url: string | null;
  source_type: string | null;
  source_authors: string[];
  source_published_at: string | null;
  source_version_number: number | null;
  source_version_kind: string | null;
  created_at: string;
};

export type EvidenceListResponse = {
  items: Evidence[];
  total: number;
  limit: number;
  offset: number;
};

export type OutlineSection = {
  section_id: string;
  index: number;
  role:
    | "introduction"
    | "body"
    | "contradictions"
    | "limitations"
    | "conclusion";
  title: string;
  purpose: string;
  search_terms: string[];
  key_claims: string[];
  evidence_ids: string[];
  support_evidence_ids: string[];
  refute_evidence_ids: string[];
  target_words: number;
  coverage_status: string;
  status: "planned" | "drafted" | "verified";
};

export type OutlineContent = {
  version: number;
  title: string;
  sections: OutlineSection[];
  summary: string;
};

export type OutlineWorkspace = {
  outline: OutlineContent | null;
  version: number | null;
  status: string | null;
  approved_at: string | null;
};

export type DraftSection = {
  id: string;
  run_id: string;
  outline_id: string | null;
  section_index: number;
  title: string;
  content_markdown: string;
  citation_map: Record<string, string>;
  evidence_ids: string[];
  version: number;
  generation_status: string;
  content_hash: string | null;
  created_at: string;
  audit_status: string | null;
  audit_details: string[];
};

export type SectionListResponse = { items: DraftSection[] };

export type ClaimEvidenceLink = {
  evidence_id: string;
  relation: string;
  rationale: string | null;
};

export type Claim = {
  id: string;
  claim_text: string;
  claim_type: string;
  verification_status: string;
  evidence: ClaimEvidenceLink[];
};

export type ClaimListResponse = { items: Claim[] };

export type Artifact = {
  id: string;
  artifact_type: string;
  title: string | null;
  version: number;
  mime_type: string;
  size_bytes: number;
  content_hash: string;
  status: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type ArtifactListResponse = { items: Artifact[] };

export type CitationIssue = {
  section_index: number;
  status: string;
  issue_type: string | null;
  details: string;
};

export type CitationAuditSummary = {
  issues: CitationIssue[];
  passed: number;
  warnings: number;
  failed: number;
};

export type ReferenceAudit = {
  cited_source_ids: string[];
  unused_source_ids: string[];
  invalid_metadata_source_ids: string[];
  dangling_markers: string[];
  issues: string[];
};

export type ExportClaim = {
  claim_id?: string;
  section_index: number | null;
  claim_text: string;
  claim_type: string;
  status: string;
  citation_markers: string[];
  reasons: string[];
  evidence: {
    evidence_id: string;
    relation: string;
    rationale: string | null;
  }[];
};

export type VerificationPayload = {
  available: boolean;
  artifact?: Artifact;
  generated_at?: string;
  citation_audit?: CitationAuditSummary;
  reference_audit?: ReferenceAudit;
  claims?: ExportClaim[];
  claim_summary?: Record<string, number>;
};

export type ExportResponse = {
  artifacts: Artifact[];
  claims: ExportClaim[];
  citation_audit: CitationAuditSummary;
  reference_audit: ReferenceAudit;
  section_count: number;
};
