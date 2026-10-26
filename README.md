# Enforge

English | [简体中文](README.zh-CN.md)

**A deep research writing agent with a traceable evidence chain**

A planning agent researches the topic, reads the source material, and proposes an article outline. After one human approval, an evidence agent collects material within the frozen outline sections, and deterministic writing and verification complete the article.

---

Enforge is not a "type a topic, get an article" tool. It breaks research into inspectable steps, and every conclusion traces back to a specific source, source version, and location in the original text:

- **Citations come only from the database**: every `[S1]` marker in the text maps to an ingested source, and references are generated from source metadata in APA / IEEE / Chicago / MLA format. No invented bibliography.
- **Claim-by-claim verification**: factual, quantitative, causal, and comparative claims are extracted and checked against citation mapping, source location, numbers and years, and counter-evidence, producing `SUPPORTED` / `PARTIAL` / `CONTRADICTED` / `UNSUPPORTED` / `UNVERIFIABLE`.
- **Gap-driven, not a fixed pipeline**: when outline-section coverage is insufficient, the system runs additional searches; when rounds or budget run out, the limitations go into the report instead of pretending the work is complete.
- **Outline is the contract**: the approved outline is the single structural authority. Every section has a stable ID, role, search terms, and word budget; evidence, queries, writing, and verification all link back to those IDs, so a run can be inspected end to end.
- **Human in the loop**: the article outline pauses for approval, editing, or rejection by default, and the workflow resumes from a PostgreSQL checkpoint.
- **Works offline**: with no API key configured, deterministic mock providers run the full workflow, the entire test suite, and the evaluation set. Real search, crawling, and models can be enabled individually.

## Capabilities

### Research workflow

A LangGraph workflow with 19 business nodes and one outline-approval gate, covering brief parsing, the planning agent, outline approval, the evidence agent, query generation, search, normalization, ranking, full-text fetching, document parsing, evidence extraction and analysis, gap analysis, follow-up retrieval, evidence attachment, writing, polishing, claim verification, citation verification, and finalization.

- **Planning agent (before approval)**: it searches from several angles, reads and fetches sources, follows references, citing works, and names it discovers (`A → B → C`), and then commits to an outline grounded in what it actually found. Every source goes through normal ingestion, and the agent cannot supply citations.
- **Evidence agent (after approval)**: it works inside the approved section IDs, chooses new search terms or fetches more material, and attaches evidence only to those sections. It cannot rename, reorder, add, or remove sections.
- **Deterministic core**: with the agents disabled or unavailable, a one-shot model outline or the deterministic outline template is used, deterministic queries drive retrieval, and writing, citation mapping, references, and verification always run through the same traceable code path.
- The outline has no fixed section count; the planning agent chooses the number of body sections from the target word budget. The introduction, optional contradictions, optional limitations, and conclusion roles are normalized by the system.
- Nine article types are chosen at project creation. Each supplies bilingual intent, outline focus, research focus, and review focus to the prompts without hard-coding a fixed template.
- Agent rounds, tool calls, searches, sources, page fetches, and citation lookups each have their own cap; every tool call is surfaced as a progress event with its query, result count, and source title.
- Every model call (outline planning, each agent round, each writing section, polishing, and claim review) reports a started/completed/failed event with its section title, token usage, and duration.
- The conditional loop is driven by evidence coverage per outline section; when the search-round or budget limit is reached, the run continues to writing and records the limitation.
- Every node's side effects have a database unique constraint or idempotency key, so task retries and worker restarts never write duplicates.
- A stable thread ID plus PostgreSQL checkpoints means approval resumption, task retries, and duplicate deliveries all continue from the latest checkpoint.

### Article types

| Type | Emphasis |
|---|---|
| Explainer | Define the subject, how it works, and why it matters |
| Mechanism Analysis | Explain the mechanism behind a phenomenon and why it persists |
| Investigative Research | Map the facts, evidence, and disputes around a complex issue |
| Case Study | Examine one case's course, causes, and outcome |
| Comparative Analysis | Compare options under shared criteria and assess fit |
| Argument & Critique | Test a claim and examine where popular explanations fail |
| Trend & Scenario Analysis | Analyze change and plausible future scenarios |
| Decision & Strategy | Recommend action for a situation and justify it |
| Historical Development | Trace how something reached its present form |

### Evidence layer and hybrid retrieval

- Parsed documents are chunked by page, section, and paragraph with location metadata, then written to pgvector and the full-text index.
- Keyword search over `to_tsvector('simple', text)` plus pgvector HNSW vector search, fused with RRF, with filters for project, source type, date, and source version.
- When vectors are unavailable, retrieval degrades to keyword search instead of failing the research run.
- The vector width is a setting, not a hard-coded constant: `EMBEDDING_DIMENSION` decides the width of the chunk vector column, and the migrate service retypes that column on every start. Switching to a model with a different width therefore only needs a configuration change; the vectors of the previous width are dropped and rebuilt by the next indexing pass.
- Evidence stores its source, source version, quotation, surrounding context, and a `locator` (page / section / paragraph / character range); verification status only says whether the quotation can be located again, not whether its content is true.
- The evidence matrix reports, per outline section, the main conclusion, supporting and refuting evidence, independent source count, and itemized reasons for source quality (recency, methodological transparency, retraction status) and evidence quality (directness, context, independence, applicability). Counter-evidence is never filtered out.
- Preprint revisions (`..._v1` / `..._v2`, arXiv `2101.00001v2`) collapse into one source during normalization, so version copies do not inflate the independent-source count or the reference list.
- Full-text fetching and remote scraping validate scheme, host, port, and literal IPs locally, but leave domain resolution to the remote fetch provider: a local proxy fake-IP DNS answer can no longer reject every public URL. Hostname blocklists and private-IP literal checks still apply.
- The planning prompt requires every `search_terms` entry to name the actual subject and to pair history/origin sections with terms such as history, timeline, or milestones, instead of searching bare words like “accessibility”.

### Writing and verification

- Argumentative outline: every section has a stable ID, role, purpose, search terms, target word count, and coverage status. A standalone contradictions section is included only when the planning model judges that disagreements cut across sections; refuting evidence otherwise stays inside its own section.
- Continuous prose per section instead of a list of evidence; the introduction frames the tension and through-line, and the conclusion only synthesizes.
- Optional model outlining, model writing, and article polishing. Every output passes strict structural validation (citation-marker set, numeric consistency, no reference section, and more) and falls back to deterministic writing if any check fails.
- The article's limitations section keeps only evidence-relevant caveats: budget caps, retrieval degradation, model fallbacks, missing full text, and source-count audit notes stay in the run workspace instead of leaking into the finished article.
- Polish outcomes are reported distinctly: `model_error:<type>`, `invalid_json:<type>`, a validation rejection with its reason, or a skip (single section / input over the cap). A failed call never blocks verification or finalization.
- When the writing model finds a section lacks evidence, it can trigger a bounded, in-run search: results are ingested, indexed, evidence is extracted, and the section is rewritten once.
- The `citation_audit` artifact combines citation verification, reference auditing, and per-claim results; the exported article contains only the title, sections, and references, while verification appendices stay in the workspace.

### Workspace

A Next.js 16 + React 19 single-page research workspace with Chinese/English UI and light/dark themes:

| Page | Contents |
|---|---|
| Project list / creation | Topic, article type, scope, language, depth, target length, date range, citation style, audience, and budget |
| Run overview | Phase progress, live SSE events, search and source status, evidence/claim/section metrics, cost, cancellation |
| Approvals | Review, edit, approve, or reject the outline; approval history is preserved |
| Sources / evidence | Fetch and parse status, evidence filters, original-text locators, supporting/refuting relations |
| Article | Table of contents, editing that creates new versions, click-through citations to evidence, re-verification and export |

### Engineering

- FastAPI + SQLAlchemy 2.0 repositories + Alembic migrations, with 16 business tables cascading by project.
- Two Celery queues (`research` / `parse`), exponential backoff for transient failures, and immediate `failed` state for permanent failures.
- A persistent event table plus SSE (`Last-Event-ID` reconnection); the database is the single source of truth.
- Optional Langfuse observability: run traces, node spans, provider calls, LLM usage, and evaluation scores; metadata only by default.
- Security controls: SSRF validation (host blocklist, port allowlist, literal-IP checks, plus DNS resolution for direct fetchers), upload type/magic-byte/size checks, server-generated storage keys, path-traversal protection, log secret redaction, prompt-injection scanning, and untrusted-content isolation.
- A repeatable quality evaluation set with 12 scenarios, using deterministic rule-based metrics rather than LLM-as-judge. Ten cases exercise workflow invariants on generated snippets; two replay frozen full-text documents so the quality metrics (evidence target achievement, full-text evidence share, snippet-only citations, quote traceability) can actually move.

## Architecture

```text
Browser
  │  http://localhost:3000
  ▼
web (Next.js) ──▶ api (FastAPI, :8000)
                     │
        ┌────────────┼──────────────┐
        ▼            ▼              ▼
   postgres       redis        /app/data/files
   (pgvector)   (broker/cache)   (Named Volume)
        ▲            ▲
        │            │
        └────── worker (Celery, research + parse)
                     │
              LangGraph checkpoint
```

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 App Router, React 19, TypeScript, Tailwind CSS |
| API | FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic |
| Workflow | LangGraph, langgraph-checkpoint-postgres |
| Tasks | Celery + Redis, separate `research` / `parse` queues |
| Storage | PostgreSQL 16 + pgvector, local file volume, Redis |
| Parsing | pypdf, trafilatura, BeautifulSoup |
| External services | Tavily, Exa, Crossref, Semantic Scholar, Firecrawl, any OpenAI-compatible endpoint (all optional) |

## Quick start

Prerequisites: Docker Desktop, or Docker Engine with the Compose plugin. Python and Node.js are not required on the host.

```bash
git clone https://github.com/wes383/enforge.git && cd enforge
cp .env.example .env      # optional; built-in development defaults are used without it
docker compose up --build -d
docker compose ps
```

The `migrate` service runs `alembic upgrade head` first; `api` and `worker` start only after it exits successfully.

| Entry point | Address |
|---|---|
| Workspace | <http://localhost:3000> |
| API docs | <http://localhost:8000/docs> |
| Health checks | <http://localhost:8000/health>, `/ready`, `/api/v1/health` |

`/ready` checks PostgreSQL, the migration version, Redis, and the shared file volume; any failure returns 503.

```bash
docker compose logs -f api worker    # view logs
docker compose down                  # stop while keeping data
```

> `docker compose down -v` deletes the database and file volumes. Avoid it unless you intend to.

## Run your first research

```bash
# 1. Create a project
PROJECT_ID=$(curl -s -X POST http://localhost:8000/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"title":"Example research","topic":"Traceable evidence chains","article_type":"explainer","language":"en"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

# 2. Start a run (Idempotency-Key supported; repeats return the same run)
RUN_ID=$(curl -s -X POST "http://localhost:8000/api/v1/projects/$PROJECT_ID/runs" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-run-1' \
  -d '{}' | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

# 3. Follow events, or open http://localhost:3000/runs/$RUN_ID
curl -N "http://localhost:8000/api/v1/runs/$RUN_ID/events/stream" -H 'Last-Event-ID: 0'
```

The run first pauses for outline approval. The pending approval contains the proposed sections, their IDs, roles, search terms, and word budgets:

```bash
curl -s "http://localhost:8000/api/v1/runs/$RUN_ID/approvals?status=pending"
curl -s -X POST "http://localhost:8000/api/v1/runs/$RUN_ID/approvals/$APPROVAL_ID" \
  -H 'Content-Type: application/json' -d '{"decision":"approved"}'
```

After approval, the evidence agent collects material inside those sections, followed by deterministic writing and verification. Once the run is finalized, five artifact types are available for download:

```bash
curl -s "http://localhost:8000/api/v1/runs/$RUN_ID/artifacts"
curl -s -OJ "http://localhost:8000/api/v1/runs/$RUN_ID/artifacts/$ARTIFACT_ID/download"
```

Main endpoints:

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/projects` | Create a project |
| `POST` | `/api/v1/projects/{id}/runs` | Start a research run; returns 202 with `run_id` |
| `GET` | `/api/v1/runs/{id}` | Run status and error summary |
| `POST` | `/api/v1/runs/{id}/cancel` | Cooperative cancellation |
| `GET` | `/api/v1/runs/{id}/events/stream` | Live SSE events |
| `GET` | `/api/v1/runs/{id}/overview` | Workspace overview |
| `GET` | `/api/v1/runs/{id}/evidence` | Filtered, paginated evidence |
| `PATCH` | `/api/v1/runs/{id}/sections/{section_id}` | Edit a section as a new version |
| `POST` | `/api/v1/runs/{id}/export` | Re-verify synchronously and export |
| `POST` | `/api/v1/projects/{id}/files` | Upload PDF/HTML/Markdown/text and parse asynchronously |

See `/docs` for the full list.

## Research workflow

```text
queued → planning → outline approval → searching → processing_sources → building_evidence
                           ▲                              │
                           │                              ├── gap-driven evidence agent ──┐
                           │                              │                              │
                           └──── rejected with feedback ◀─┘                              │
                                                                                         │
                                          (no gaps or budget exhausted) ◀────────────────┘
                                                          │
                                                          ▼
                                    writing → polish → verifying → completed

any non-terminal state → failed / cancelled
```

- **Approvals**: exactly one outline approval. Approving freezes the section IDs; editing changes the outline directly; rejection sends feedback back to the planning agent. Approval history is preserved.
- **Cancellation**: queued and awaiting-approval runs stop immediately; running tasks check the cancellation flag at node boundaries and inside fetch/parse loops, with Celery revoke used only for acceleration.
- **Budgets**: search rounds, query count, source count, fetch count, evidence count, per-node timeout, and token/cost budgets are all configurable. A budget stop does not fail the run; the report states the limitation.
- **Evidence targets**: the coverage floor (two supporting items from two independent sources) only decides whether a section can be written at all. Separately, each section has an evidence target derived from its own word budget; extraction stops once the target is met or two consecutive queries bring no new independent source. A covered section that is still below its target raises a low-severity gap, and that gap closes as soon as a round fails to improve it.
- **Failure semantics**: transient errors (timeouts, momentary database/storage failures) go to Celery with exponential backoff; permanent errors fail immediately with a readable `error_summary`.

## Configuration

See [`.env.example`](.env.example) for every variable and its default. The most commonly used groups:

| Variable | Default | Description |
|---|---|---|
| `RESEARCH_PROVIDER_MODE` | `auto` | `auto` / `real` / `mock`; falls back to mock when no real provider is configured |
| `OPENAI_API_KEY` | empty | When set, registers an OpenAI-compatible LLM; empty means fully offline |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` | OpenAI / `gpt-4o-mini` | Can point to any compatible gateway (vLLM, Ollama, etc.) |
| `RESEARCH_LLM_OUTLINE_ENABLED` | `false` | One-shot model outline when the tool-using planning agent is off |
| `RESEARCH_LLM_WRITING_ENABLED` | `false` | Model writes section by section (may request one bounded follow-up search) |
| `RESEARCH_LLM_POLISH_ENABLED` | `false` | Whole-article language polishing |
| `RESEARCH_AGENT_ENABLED` | `false` | Tool-using planning agent before approval and evidence agent after approval; requires a tool-calling LLM and an enabled search provider, otherwise the run uses the deterministic outline and retrieval path |
| `RESEARCH_AGENT_MAX_ROUNDS` / `RESEARCH_AGENT_MAX_TOOL_CALLS` | `6` / `18` | Model rounds and total tool calls per agent run; search, read, page fetch and citation lookup each spend one tool call, so the tool-call budget must stay above the search budget |
| `RESEARCH_AGENT_MAX_SEARCHES` / `RESEARCH_AGENT_MAX_SOURCES` / `RESEARCH_AGENT_MAX_FETCHES` / `RESEARCH_AGENT_MAX_CITATION_CALLS` | `12` / `16` / `6` / `4` | Per-run caps on indexed searches, stored sources, page fetches, and reference/citation lookups; one search stores at most 5 results, both agents share the source cap, which is further limited by `RESEARCH_MAX_SOURCES` |
| `RESEARCH_AGENT_PLANNING_MAX_SOURCES` | `60` | Sources the planning agent may store before the outline exists; the evidence agent keeps the remainder of `RESEARCH_AGENT_MAX_SOURCES` to fill the approved sections |
| `RESEARCH_CLAIM_LLM_REVIEW` | `false` | Conservative model review of failed claims; can only downgrade |
| `RESEARCH_OUTLINE_APPROVAL_REQUIRED` | `true` | Single outline approval; disable for fully automatic runs |
| `RESEARCH_MAX_SEARCH_ROUNDS` | `3` | Maximum search rounds |
| `RESEARCH_MAX_SOURCES` / `RESEARCH_MAX_EVIDENCE` | `20` / `40` | Source and evidence limits |
| `RESEARCH_SECTION_TARGET_WORDS_PER_EVIDENCE` | `80` | Evidence a section needs per 80 words of its own budget; the target is clamped by the min/max below |
| `RESEARCH_SECTION_TARGET_EVIDENCE_MIN` / `_MAX` | `4` / `12` | Lower and upper bound of a section's evidence target |
| `RESEARCH_SECTION_TARGET_SOURCES_MIN` / `_MAX` | `3` / `6` | Lower and upper bound of the independent sources a section needs |
| `RESEARCH_RETRIEVAL_CANDIDATE_LIMIT` | `20` | Candidates pulled per section query before extraction |
| `RESEARCH_WRITE_MAX_TOKENS_FLOOR` / `RESEARCH_WRITE_TOKENS_PER_WORD` | `3000` / `6` | Token budget for one model-written section; reasoning models bill their reasoning against the same budget |
| `RESEARCH_OUTLINE_MAX_TOKENS_FLOOR` / `RESEARCH_OUTLINE_TOKENS_PER_SECTION` | `6000` / `400` | Token budget for one outline call; the floor leaves a reasoning model room to think before it writes the structure |
| `RESEARCH_CLAIM_REVIEW_MAX_TOKENS` | `1200` | Token budget for one claim review |
| `RESEARCH_POLISH_MAX_TOKENS_FLOOR` | `4000` | Token floor for the whole-article polish call |
| `RESEARCH_AGENT_MAX_TOKENS_FLOOR` / `RESEARCH_EVIDENCE_AGENT_MAX_TOKENS_FLOOR` | `6000` / `6000` | Token floor for one tool-calling round of the planning agent and the evidence agent |
| `LLM_REASONING_RETRY_FACTOR` / `LLM_REASONING_RETRY_CEILING` | `2` / `24000` | A call that ends with an empty answer because the model spent the whole budget on reasoning is repeated once with a larger budget; the truncated attempt is still billed, and the factor `1` switches the repeat off |
| `EMBEDDING_ENABLED` / `EMBEDDING_API_KEY` | `false` / empty | Optional OpenAI-compatible embeddings; without them retrieval stays keyword-only and says so in the run events and the article limitations |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSION` | `text-embedding-3-small` / `1536` | The model and the vector width; the chunk vector column follows the width, so switching models only needs a configuration change |
| `RESEARCH_TOKEN_BUDGET` / `RESEARCH_COST_BUDGET_USD` | `0` | Budgets; 0 means unlimited |
| `MAX_ACTIVE_RUNS_PER_PROJECT` | `3` | Per-project concurrent run limit; exceeding it returns 429 |
| `LANGFUSE_ENABLED` | `false` | Optional observability; metadata only by default, no article text or secrets |
| `ENABLE_DEV_ENDPOINTS` | `true` | Smoke-test endpoints; must be `false` outside local development |

Real providers (Tavily, Exa, Crossref, Semantic Scholar, Firecrawl) are disabled by default and require both `*_ENABLED=true` and the corresponding key. Keys live only in backend environment variables and are never exposed to the browser.

No rebuild is needed after changing `.env`:

```bash
docker compose up -d --force-recreate api worker
```

Changing the public API address `NEXT_PUBLIC_API_BASE_URL` requires rebuilding `web`.

## Development and testing

Tests are fully isolated from real keys: `docker-compose.test.yml` forces `RESEARCH_PROVIDER_MODE=mock` and clears every provider key and LLM switch, so even a `.env` with real keys never reaches the network or consumes quota.

```bash
# Unit + integration + evaluation (currently 437 passed)
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  --profile test run --rm --build test

# Quality evaluation set only
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  --profile test run --rm test python -m dr_core.evaluation

# Also write each case's finished article and claim/citation pairs
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  --profile test run --rm test python -m dr_core.evaluation \
  --articles /app/data/files/eval_articles
```

The evaluation set has two halves. Ten cases use generated placeholder snippets and assert that the workflow itself holds together: interruption recovery, rate limiting, budget exhaustion, incomplete metadata. Two corpus cases replay frozen full-text documents from `tests/evaluation/corpus/` through the production parser, chunker and evidence extractor, and gate the quality metrics that the placeholder cases cannot move: evidence target achievement, the share of evidence taken from full text, citations that rest only on snippets, whether a quotation actually occurs in the source it cites, and whether contradicting results in real prose are recognised. The corpus documents are authored for this suite and marked `synthetic_content`; replacing the files with real documents is the intended way to evaluate a real corpus. Extraction splits the global evidence budget across sections by what each section still needs, so an early section cannot consume every later section's allowance; citation traceability checks every version of a source, so a quotation taken from a search snippet remains traceable once the full text has been fetched.

### Human review (optional)

Human labels are not a default gate. Generate the template first:

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  --profile test run --rm test python -m dr_core.evaluation \
  --review-template data/files/reviews
```

Each row carries `case_id`, `claim_id`, `claim_text`, `quoted_span` and `source_title`, but not the model's own verdict, so the label is not anchored. Fill `reviewer_label` with `supported`, `partial` or `unsupported`, save the file as `tests/evaluation/reviews/<date>.json`, then run:

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  --profile test run --rm test python -m dr_core.evaluation \
  --human-review tests/evaluation/reviews/<date>.json
```

The report includes `human_review.agreement_rate`. Below thirty labels it is recorded only; at thirty or more the 0.80 threshold becomes a gate. Empty and unknown labels do not count. `unsupported` counts as agreement with either `contradicted` or `unverifiable`; `partial` matches only `partial`.

### Writing-quality judge (optional, spends API tokens)

Citation validity, span traceability and claim support stay with the deterministic rules. Writing quality has a separate job that calls a real model only when `--provider real` is passed:

```bash
docker compose --profile judge run --rm --build judge \
  python -m dr_core.evaluation.judge --provider real --limit 10 \
  --export data/files/judge_articles --output data/files/judge.json
```

It generates articles from the evaluation cases, then scores coherence, specificity discipline (does it invent precise-sounding details) and calibrated tone at `temperature=0`. The model name and token usage are recorded; when Langfuse is enabled the job writes `judge_*` scores. It is capped at nine cases (interruption, rate-limit and budget-exhaustion cases are excluded) and a per-article token budget. Scores are a trend, never a release gate. To dry-run the scoring over existing `.md` files:

```bash
docker compose --profile judge run --rm judge \
  python -m dr_core.evaluation.judge --provider fake \
  --articles data/files/judge_articles
```

Frontend static checks:

```bash
cd apps/web
npm ci
npm run lint && npm run typecheck && npm run build
```

Database and maintenance:

```bash
docker compose exec api alembic current        # current migration version
docker compose exec api alembic check          # model/migration drift check
docker compose exec api python -m dr_core.maintenance   # file-record reconciliation
```

## Known limitations

- There is no login or multi-user authorization. Any public deployment must add authentication behind a reverse proxy.
- Writing is deterministic by default, which keeps it testable offline and citations traceable, but it does not have the reasoning or rewriting ability of a real model; model switches are off by default.
- No real embedding service is wired in yet. Offline runs use deterministic term-hash vectors, so semantic recall is limited; `auto` mode falls back to keyword search.
- Local parsing does not include OCR; scanned or encrypted PDFs fail explicitly, and table extraction is best-effort.
- The ten mock evaluation cases use generated snippets, so their metrics describe the pipeline rather than writing quality; the two corpus cases replay frozen full-text documents and are the ones that measure evidence quality. Neither half judges prose: the deterministic writer is exercised, not the configured model.
- The planning and evidence agents need an OpenAI-compatible tool-calling model and a real search provider. Offline and mock runs use the one-shot model outline (if enabled) or the deterministic template plus deterministic retrieval; agent output is always validated and never becomes citation authority.
- Citation chasing depends on academic metadata: DOI-less or unstructured references are returned to the agent as leads, not ingested. Agent searches appear as run events rather than search-query rows.
- The approved outline is authoritative: agents cannot add sources or claims outside stored evidence, and the evidence agent cannot alter section structure.
- Cancellation is cooperative. An in-flight provider HTTP request is not forcibly aborted; it is bounded by timeouts.
- Reasoning models bill their thinking against the same token limit as the answer. A call that ends with an empty answer is repeated once with a larger budget (`LLM_REASONING_RETRY_FACTOR`), and the truncated attempt is still billed; if the second attempt is truncated too, the node falls back to its deterministic path.
