# Enforge

English | [简体中文](README.zh-CN.md)

**A deep research writing agent with a traceable evidence chain**

It researches a topic, reads the source material, extracts evidence, detects gaps, builds an outline, writes section by section, and verifies every claim and citation in the final text.

---

Enforge is not a "type a topic, get an article" tool. It breaks research into inspectable steps, and every conclusion traces back to a specific source, source version, and location in the original text:

- **Citations come only from the database**: every `[S1]` marker in the text maps to an ingested source, and references are generated from source metadata in APA / IEEE / Chicago / MLA format. No invented bibliography.
- **Claim-by-claim verification**: factual, quantitative, causal, and comparative claims are extracted and checked against citation mapping, source location, numbers and years, and counter-evidence, producing `SUPPORTED` / `PARTIAL` / `CONTRADICTED` / `UNSUPPORTED` / `UNVERIFIABLE`.
- **Gap-driven, not a fixed pipeline**: when question coverage is insufficient, the system runs additional searches; when rounds or budget run out, the limitations go into the report instead of pretending the work is complete.
- **Human in the loop**: the research plan and article outline pause for human approval, editing, or rejection by default, and the workflow resumes from a PostgreSQL checkpoint.
- **Works offline**: with no API key configured, deterministic mock providers run the full workflow, the entire test suite, and the evaluation set. Real search, crawling, and models can be enabled individually.

## Capabilities

### Research workflow

A LangGraph workflow with 18 business nodes and 2 approval nodes, covering brief parsing, upfront recon, planning, search, normalization, ranking, full-text fetching, document parsing, evidence extraction, evidence analysis, gap analysis, follow-up search, outlining, writing, polishing, claim verification, citation verification, and finalization.

- The conditional loop is driven by evidence coverage in the database; when the search-round or budget limit is reached, the run continues to writing and records the limitation.
- Every node's side effects have a database unique constraint or idempotency key, so task retries and worker restarts never write duplicates.
- A stable thread ID plus PostgreSQL checkpoints means approval resumption, task retries, and duplicate deliveries all continue from the latest checkpoint.

### Evidence layer and hybrid retrieval

- Parsed documents are chunked by page, section, and paragraph with location metadata, then written to pgvector and the full-text index.
- Keyword search over `to_tsvector('simple', text)` plus pgvector HNSW vector search, fused with RRF, with filters for project, source type, date, and source version.
- When vectors are unavailable, retrieval degrades to keyword search instead of failing the research run.
- Evidence stores its source, source version, quotation, surrounding context, and a `locator` (page / section / paragraph / character range); verification status only says whether the quotation can be located again, not whether its content is true.
- The evidence matrix reports, per research question, the main conclusion, supporting and refuting evidence, independent source count, and itemized reasons for source quality (recency, methodological transparency, retraction status) and evidence quality (directness, context, independence, applicability). Counter-evidence is never filtered out.
- Preprint revisions (`..._v1` / `..._v2`, arXiv `2101.00001v2`) collapse into one source during normalization, so version copies do not inflate the independent-source count or the reference list.
- Full-text fetching and remote scraping validate scheme, host, port, and literal IPs locally, but leave domain resolution to the remote fetch provider: a local proxy fake-IP DNS answer can no longer reject every public URL. Hostname blocklists and private-IP literal checks still apply.
- The planning prompt requires every `search_terms` entry to name the actual subject and to pair history/origin questions with terms such as history, timeline, or milestones, instead of searching bare words like “accessibility”.

### Writing and verification

- Argumentative outline: every section has a purpose, an associated research question, key claims, supporting and opposing evidence, a target word count, and a status.
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
| Project list / creation | Topic, scope, language, depth, target length, date range, citation style, audience, and budget |
| Run overview | Phase progress, live SSE events, search and source status, evidence/claim/section metrics, cost, cancellation |
| Approvals | Review, edit, approve, or reject the research plan and outline; approval history is preserved |
| Sources / evidence | Fetch and parse status, evidence filters, original-text locators, supporting/refuting relations |
| Article | Table of contents, editing that creates new versions, click-through citations to evidence, re-verification and export |

### Engineering

- FastAPI + SQLAlchemy 2.0 repositories + Alembic migrations, with 16 business tables cascading by project.
- Two Celery queues (`research` / `parse`), exponential backoff for transient failures, and immediate `failed` state for permanent failures.
- A persistent event table plus SSE (`Last-Event-ID` reconnection); the database is the single source of truth.
- Optional Langfuse observability: run traces, node spans, provider calls, LLM usage, and evaluation scores; metadata only by default.
- Security controls: SSRF validation (host blocklist, port allowlist, literal-IP checks, plus DNS resolution for direct fetchers), upload type/magic-byte/size checks, server-generated storage keys, path-traversal protection, log secret redaction, prompt-injection scanning, and untrusted-content isolation.
- A repeatable quality evaluation set with 10 scenarios, using deterministic rule-based metrics rather than LLM-as-judge.

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
  -d '{"title":"Example research","topic":"Traceable evidence chains","language":"en"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

# 2. Start a run (Idempotency-Key supported; repeats return the same run)
RUN_ID=$(curl -s -X POST "http://localhost:8000/api/v1/projects/$PROJECT_ID/runs" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-run-1' \
  -d '{}' | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

# 3. Follow events, or open http://localhost:3000/runs/$RUN_ID
curl -N "http://localhost:8000/api/v1/runs/$RUN_ID/events/stream" -H 'Last-Event-ID: 0'
```

The run first pauses for research-plan approval:

```bash
curl -s "http://localhost:8000/api/v1/runs/$RUN_ID/approvals?status=pending"
curl -s -X POST "http://localhost:8000/api/v1/runs/$RUN_ID/approvals/$APPROVAL_ID" \
  -H 'Content-Type: application/json' -d '{"decision":"approved"}'
```

Search, fetching, evidence, outline approval, writing, and verification follow. Once the run is finalized, five artifact types are available for download:

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
queued → planning ⇄ plan approval → searching → processing_sources → building_evidence
              ▲                                      │
              └──────── gap-driven search ◀──────────┘
                                                     │ (no gaps or budget exhausted)
                                                     ▼
                        outline approval → writing → polish → verifying → completed

any non-terminal state → failed / cancelled
```

- **Approvals**: approving, rejecting, or editing actually changes the downstream workflow; rejection re-plans with feedback, and edited content takes effect directly.
- **Cancellation**: queued and awaiting-approval runs stop immediately; running tasks check the cancellation flag at node boundaries and inside fetch/parse loops, with Celery revoke used only for acceleration.
- **Budgets**: search rounds, query count, source count, fetch count, evidence count, per-node timeout, and token/cost budgets are all configurable. A budget stop does not fail the run; the report states the limitation.
- **Failure semantics**: transient errors (timeouts, momentary database/storage failures) go to Celery with exponential backoff; permanent errors fail immediately with a readable `error_summary`.

## Configuration

See [`.env.example`](.env.example) for every variable and its default. The most commonly used groups:

| Variable | Default | Description |
|---|---|---|
| `RESEARCH_PROVIDER_MODE` | `auto` | `auto` / `real` / `mock`; falls back to mock when no real provider is configured |
| `OPENAI_API_KEY` | empty | When set, registers an OpenAI-compatible LLM; empty means fully offline |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` | OpenAI / `gpt-4o-mini` | Can point to any compatible gateway (vLLM, Ollama, etc.) |
| `RESEARCH_LLM_PLANNING_ENABLED` | `false` | Let the model generate research questions |
| `RESEARCH_LLM_OUTLINE_ENABLED` | `false` | Let the model order and title body sections |
| `RESEARCH_LLM_WRITING_ENABLED` | `false` | Model writes section by section (may request one bounded follow-up search) |
| `RESEARCH_LLM_POLISH_ENABLED` | `false` | Whole-article language polishing |
| `RESEARCH_LLM_RECON_ENABLED` | `false` | Bounded tool recon before planning (requires planning to be enabled) |
| `RESEARCH_CLAIM_LLM_REVIEW` | `false` | Conservative model review of failed claims; can only downgrade |
| `RESEARCH_PLAN_APPROVAL_REQUIRED` | `true` | Plan approval; disable for fully automatic runs |
| `RESEARCH_OUTLINE_APPROVAL_REQUIRED` | `true` | Outline approval |
| `RESEARCH_MAX_SEARCH_ROUNDS` | `3` | Maximum search rounds |
| `RESEARCH_MAX_SOURCES` / `RESEARCH_MAX_EVIDENCE` | `20` / `40` | Source and evidence limits |
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
# Unit + integration + evaluation (currently 324 passed)
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  --profile test run --rm --build test

# Quality evaluation set only
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  --profile test run --rm test python -m dr_core.evaluation
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
- The quality evaluation set uses mock providers, so its metrics describe offline behavior; re-run it after connecting real services.
- Cancellation is cooperative. An in-flight provider HTTP request is not forcibly aborted; it is bounded by timeouts.
