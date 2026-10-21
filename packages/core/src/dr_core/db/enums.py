from enum import StrEnum


class ProjectLanguage(StrEnum):
    ZH = "zh"
    EN = "en"


class ResearchDepth(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class CitationStyle(StrEnum):
    APA = "apa"
    IEEE = "ieee"
    CHICAGO = "chicago"
    MLA = "mla"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class RunStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    SEARCHING = "searching"
    PROCESSING_SOURCES = "processing_sources"
    BUILDING_EVIDENCE = "building_evidence"
    WRITING = "writing"
    VERIFYING = "verifying"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
)


class QuestionCoverageStatus(StrEnum):
    OPEN = "open"
    PARTIAL = "partial"
    COVERED = "covered"
    INSUFFICIENT = "insufficient"


class SearchProviderName(StrEnum):
    TAVILY = "tavily"
    EXA = "exa"
    CROSSREF = "crossref"
    SEMANTIC_SCHOLAR = "semantic_scholar"


class SearchQueryStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class SourceType(StrEnum):
    WEB_PAGE = "web_page"
    JOURNAL_ARTICLE = "journal_article"
    CONFERENCE_PAPER = "conference_paper"
    PREPRINT = "preprint"
    BOOK = "book"
    REPORT = "report"
    DATASET = "dataset"
    USER_DOCUMENT = "user_document"
    OTHER = "other"


class SourceOrigin(StrEnum):
    WEB_SEARCH = "web_search"
    ACADEMIC_SEARCH = "academic_search"
    USER_UPLOAD = "user_upload"
    MANUAL = "manual"


class SourceVersionKind(StrEnum):
    SEARCH_SNIPPET = "search_snippet"
    FULL_TEXT = "full_text"
    UPLOADED_DOCUMENT = "uploaded_document"


class ParserStatus(StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    PARSED = "parsed"
    FAILED = "failed"
    SKIPPED = "skipped"


class EvidenceType(StrEnum):
    DIRECT_SUPPORT = "direct_support"
    REFUTATION = "refutation"
    CONTEXTUAL = "contextual"
    DEFINITION = "definition"
    STATISTIC = "statistic"
    QUALITATIVE = "qualitative"


class EvidenceVerificationStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    INVALID = "invalid"


class ClaimType(StrEnum):
    FACTUAL = "factual"
    CAUSAL = "causal"
    COMPARATIVE = "comparative"
    DEFINITIONAL = "definitional"
    QUANTITATIVE = "quantitative"
    NORMATIVE = "normative"
    PREDICTION = "prediction"
    OTHER = "other"


class ClaimVerificationStatus(StrEnum):
    PENDING = "PENDING"
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    CONTRADICTED = "CONTRADICTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNVERIFIABLE = "UNVERIFIABLE"


class EvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    PARTIALLY_SUPPORTS = "partially_supports"
    RELATED = "related"


class OutlineStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class DraftSectionStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    DRAFTED = "drafted"
    VERIFIED = "verified"
    FAILED = "failed"


class CitationAuditStatus(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class CitationIssueType(StrEnum):
    MISSING_CITATION = "missing_citation"
    DANGLING_REFERENCE = "dangling_reference"
    METADATA_MISMATCH = "metadata_mismatch"
    LOCATOR_MISSING = "locator_missing"
    CLAIM_NOT_SUPPORTED = "claim_not_supported"
    LIMITATION_OMITTED = "limitation_omitted"
    NUMERIC_MISMATCH = "numeric_mismatch"
    OTHER = "other"


class AuditMethod(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    HUMAN = "human"


class RunEventType(StrEnum):
    RUN_CREATED = "run_created"
    CANCEL_REQUESTED = "cancel_requested"
    RETRY_SCHEDULED = "retry_scheduled"
    PHASE_STARTED = "phase_started"
    PHASE_COMPLETED = "phase_completed"
    PHASE_FAILED = "phase_failed"
    PROGRESS = "progress"
    SOURCE_FETCHED = "source_fetched"
    SOURCE_PARSED = "source_parsed"
    EVIDENCE_EXTRACTED = "evidence_extracted"
    SECTION_GENERATED = "section_generated"
    CITATION_AUDIT_FAILED = "citation_audit_failed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVAL_DECIDED = "approval_decided"
    RUN_CANCELLED = "run_cancelled"
    ERROR = "error"
    OTHER = "other"


class ArtifactType(StrEnum):
    RESEARCH_REPORT = "research_report"
    REFERENCE_LIST = "reference_list"
    EVIDENCE_MATRIX = "evidence_matrix"
    CITATION_AUDIT = "citation_audit"
    MARKDOWN_EXPORT = "markdown_export"
    PDF_EXPORT = "pdf_export"
    DOCX_EXPORT = "docx_export"
    OTHER = "other"


class ArtifactStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    MISSING = "missing"
