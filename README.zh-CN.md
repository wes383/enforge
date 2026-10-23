# Enforge

[English](README.md) | 简体中文

**可追溯证据链的深度研究写作智能体**

规划 agent 先围绕研究主题检索、阅读原文并提出文章提纲；提纲经一次人工审批后，取证 agent 在冻结的章节内收集材料，随后由确定性写作与核验流程完成文章。

---

Enforge 不是一个"输入主题、一次生成"的写作工具。它把研究拆成可检查的步骤，每个结论都能回查到具体的来源、来源版本和原文位置：

- **引用只来自数据库**：正文里的每个 `[S1]` 都映射到入库来源；参考文献由来源元数据按 APA / IEEE / Chicago / MLA 生成，不编造文献。
- **主张逐条核验**：抽取事实性、量化、因果和比较类主张，检查引用映射、原文定位、数字年份与反驳证据，输出 `SUPPORTED` / `PARTIAL` / `CONTRADICTED` / `UNSUPPORTED` / `UNVERIFIABLE`。
- **缺口驱动而非固定流水线**：提纲章节覆盖不足时自动补充检索，预算或轮次耗尽则把限制写进报告，而不是假装完成。
- **提纲即契约**：审批后的提纲是唯一的结构权威。每个章节都有稳定 ID、角色、检索词与字数预算，证据、检索、写作和核验都回链到这些 ID，整次运行可以端到端检查。
- **人在回路**：文章提纲默认暂停等待人工批准、修改或驳回，工作流从 PostgreSQL checkpoint 继续。
- **离线可用**：不配置任何 API Key 也能用确定性 Mock 提供商跑通全流程、全部测试和评测集；配置后可逐项接入真实搜索、抓取与模型。

## 主要能力

### 研究工作流

基于 LangGraph 的 19 个业务节点加 1 个提纲审批门，覆盖解析简报、规划代理、提纲审批、取证代理、查询生成、检索、规范化、排序、正文抓取、文档解析、证据抽取与分析、缺口分析、补充检索、证据归属、写作、润色、主张核验、引用核验与定稿。

- **规划代理（审批前）**：它从多个角度检索、阅读与抓取来源，沿参考文献、被引文献和发现的人名/机构继续追踪（`A → B → C`），再基于实际找到的材料提出提纲。所有来源都走正常入库流程，代理本身不能提供引用。
- **取证代理（审批后）**：它只在已批准的章节 ID 内工作，可以提出新检索词或抓取更多材料，并把证据只归属到这些章节；不能改名、重排、增加或删除章节。
- **确定性核心**：代理关闭或不可用时，使用一次性模型提纲或确定性提纲模板，由确定性查询驱动检索；写作、引用映射、参考文献与核验始终走同一条可追溯代码路径。
- 提纲不限制章节条数，规划代理根据目标字数决定正文节数；引言、可选的矛盾与反方证据、可选的研究局限以及结论角色由系统规范化。
- 创建项目时从 9 类文章类型中选择。每类提供中英文的写作意图、提纲侧重、检索侧重与审阅侧重，注入提示词但不写死固定模板。
- 代理的模型轮次、工具调用、检索、来源、正文抓取与引文查询分别限额；每次工具调用都会写入进度事件，并带上检索词、结果数量和来源标题。
- 每次模型调用（提纲规划、各代理轮次、各章节写作、润色、主张复核）都会给出开始/完成/失败的进度事件，包含章节标题、token 用量与耗时。
- 条件循环由各提纲章节的证据覆盖情况驱动，达到检索轮次或预算上限后继续写作并记录局限。
- 每个节点的副作用都有数据库唯一约束或幂等键，任务重试和 Worker 重启不会重复写入。
- 稳定线程 ID + PostgreSQL checkpoint：审批恢复、任务重试和重复投递都从最近的 checkpoint 继续。

### 文章类型

| 类型 | 侧重 |
|---|---|
| 解释型文章 | 解释概念、运作方式与重要性 |
| 机制分析 | 分析现象背后的机制与持续原因 |
| 调查与深度研究 | 梳理复杂议题的事实、证据与争议 |
| 案例研究 | 复盘具体案例的经过、原因与结果 |
| 比较与评估 | 在共同标准下比较对象并给出评估 |
| 观点与批判性评论 | 检验观点是否成立，检讨流行解释 |
| 趋势与情景分析 | 分析领域变化与可能的未来情景 |
| 决策与策略报告 | 面向具体处境提出行动方案与理由 |
| 历史演进与发展史 | 追溯事物的发展脉络与演变过程 |

### 证据层与混合检索

- 解析后的文档按页、章节、段落切成带定位信息的切片，写入 pgvector 与全文索引。
- `to_tsvector('simple', text)` 关键词检索 + pgvector HNSW 向量检索，按 RRF 融合，支持项目、来源类型、日期、来源版本过滤。
- 向量不可用时自动降级为关键词检索，研究任务不会因此崩溃。
- 证据保存来源、来源版本、原文引用、前后上下文与 `locator`（页码 / 章节 / 段落 / 字符区间），核验状态只表示"能否重新定位"，不表示内容为真。
- 证据矩阵按提纲章节给出主要结论、支持/反驳证据、独立来源数，以及来源质量（时效、方法透明度、撤稿状态）与证据质量（直接性、上下文、独立性、适用条件）的分项理由。相反证据不会被过滤。

### 写作与核验

- 论证型提纲：每节包含稳定章节 ID、角色、写作目的、检索词、目标字数和覆盖状态。只有规划模型判断分歧跨多个章节时，才单独生成“矛盾与反方证据”节；否则反驳证据保留在对应章节内。
- 分章节连续行文，而不是证据罗列；引言交代张力与主线，结论只做综合。
- 可选模型提纲、模型写作与全文润色，输出均经过严格结构校验（引用标记集合、数字一致性、无参考文献章节等），任一项失败自动回退确定性写作。
- 写作模型发现某节证据不足时，可在运行内发起有界补检：正常入库、建索引、抽证据后重写一次。
- `citation_audit` artifact 汇总引用核验、参考文献审计与逐条主张；导出的主文章只包含标题、章节和参考文献，核验附录留在工作台。

### 工作台

Next.js 16 + React 19 单页研究工作台，中英文与明暗主题：

| 页面 | 内容 |
|---|---|
| 项目列表 / 创建 | 主题、文章类型、范围、语言、深度、目标字数、时间范围、引用格式、读者与预算 |
| 运行概览 | 阶段进度、SSE 实时事件、检索与来源状态、证据/主张/章节指标、成本、取消 |
| 审批 | 查看、修改、批准或拒绝提纲，保留审批历史 |
| 来源 / 证据 | 抓取与解析状态、证据筛选、原文定位与支持/反驳关系 |
| 长文 | 目录切换、编辑保存新版本、点击引用回查证据、重新核验并导出 |

### 工程化

- FastAPI + SQLAlchemy 2.0 仓储层 + Alembic 迁移，16 张业务表按项目级联。
- Celery 双队列（`research` / `parse`），暂时性失败指数退避重试，永久性失败立即落定为 `failed`。
- 持久化事件表 + SSE（`Last-Event-ID` 断线续传），数据库是唯一真相来源。
- 可选 Langfuse 观测：运行 trace、节点 span、提供商调用、LLM 用量与评测分数，默认只发元数据。
- 安全控制：SSRF 校验、上传类型/魔数/大小校验、服务端生成存储键、路径穿越防护、日志密钥脱敏、提示词注入扫描与不可信内容隔离。
- 10 类场景的可重复质量评测集，全部使用确定性规则指标，不依赖 LLM-as-judge。

## 架构

```text
浏览器
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

| 层 | 技术 |
|---|---|
| 前端 | Next.js 16 App Router、React 19、TypeScript、Tailwind CSS |
| API | FastAPI、Pydantic v2、SQLAlchemy 2.0、Alembic |
| 工作流 | LangGraph、langgraph-checkpoint-postgres |
| 任务 | Celery + Redis，独立 `research` / `parse` 队列 |
| 存储 | PostgreSQL 16 + pgvector、本地文件卷、Redis |
| 解析 | pypdf、trafilatura、BeautifulSoup |
| 外部服务 | Tavily、Exa、Crossref、Semantic Scholar、Firecrawl、任意 OpenAI 兼容端点（全部可选） |

## 快速开始

前置条件：Docker Desktop，或 Docker Engine + Compose 插件。宿主机不需要安装 Python 或 Node.js。

```bash
git clone https://github.com/wes383/enforge.git && cd enforge
cp .env.example .env      # 可选；不创建时使用内置开发默认值
docker compose up --build -d
docker compose ps
```

`migrate` 服务会先执行 `alembic upgrade head`，成功退出后 `api`、`worker` 才会启动。

| 入口 | 地址 |
|---|---|
| 工作台 | <http://localhost:3000> |
| API 文档 | <http://localhost:8000/docs> |
| 健康检查 | <http://localhost:8000/health>、`/ready`、`/api/v1/health` |

`/ready` 同时检查 PostgreSQL、迁移版本、Redis 与共享文件卷，任一失败返回 503。

```bash
docker compose logs -f api worker    # 查看日志
docker compose down                  # 停止并保留数据
```

> `docker compose down -v` 会删除数据库和文件卷，非必要不要执行。

## 运行第一个研究

```bash
# 1. 创建项目
PROJECT_ID=$(curl -s -X POST http://localhost:8000/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"title":"示例研究","topic":"可追溯证据链","article_type":"explainer","language":"zh"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

# 2. 启动运行（支持 Idempotency-Key，重复提交返回同一运行）
RUN_ID=$(curl -s -X POST "http://localhost:8000/api/v1/projects/$PROJECT_ID/runs" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-run-1' \
  -d '{}' | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

# 3. 跟进事件，或直接打开 http://localhost:3000/runs/$RUN_ID
curl -N "http://localhost:8000/api/v1/runs/$RUN_ID/events/stream" -H 'Last-Event-ID: 0'
```

运行会先停在提纲审批。待审批记录中包含提议的章节、章节 ID、角色、检索词与字数预算：

```bash
curl -s "http://localhost:8000/api/v1/runs/$RUN_ID/approvals?status=pending"
curl -s -X POST "http://localhost:8000/api/v1/runs/$RUN_ID/approvals/$APPROVAL_ID" \
  -H 'Content-Type: application/json' -d '{"decision":"approved"}'
```

审批后，取证代理在这些章节内收集材料，随后进入确定性写作与核验。定稿后可下载 5 类 artifact：

```bash
curl -s "http://localhost:8000/api/v1/runs/$RUN_ID/artifacts"
curl -s -OJ "http://localhost:8000/api/v1/runs/$RUN_ID/artifacts/$ARTIFACT_ID/download"
```

主要接口：

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/projects` | 创建项目 |
| `POST` | `/api/v1/projects/{id}/runs` | 启动研究运行，返回 202 与 `run_id` |
| `GET` | `/api/v1/runs/{id}` | 运行状态与错误摘要 |
| `POST` | `/api/v1/runs/{id}/cancel` | 协作式取消 |
| `GET` | `/api/v1/runs/{id}/events/stream` | SSE 实时事件 |
| `GET` | `/api/v1/runs/{id}/overview` | 工作台汇总 |
| `GET` | `/api/v1/runs/{id}/evidence` | 证据筛选分页 |
| `PATCH` | `/api/v1/runs/{id}/sections/{section_id}` | 编辑章节，生成新版本 |
| `POST` | `/api/v1/runs/{id}/export` | 同步重新核验并导出 |
| `POST` | `/api/v1/projects/{id}/files` | 上传 PDF/HTML/Markdown/文本并异步解析 |

完整列表见 `/docs`。

## 研究工作流

```text
queued → planning → 提纲审批 → searching → processing_sources → building_evidence
                         ▲                            │
                         │                            ├── 缺口驱动取证代理 ──┐
                         │                            │                      │
                         └──── 拒绝并反馈 ◀───────────┘                      │
                                                                             │
                                     （无缺口或预算耗尽）◀───────────────────┘
                                                    │
                                                    ▼
                                  writing → polish → verifying → completed

任意非终态 → failed / cancelled
```

- **审批**：只有一次提纲审批。批准即冻结章节 ID；修改内容直接生效；拒绝会带反馈回到规划代理。审批历史完整保留。
- **取消**：排队与待审批立即终止；执行中的运行在节点边界和抓取/解析循环内检查取消标记，Celery revoke 只作加速。
- **预算**：检索轮次、查询数、来源数、抓取数、证据数、单节点超时、token 与成本预算均可配置。预算停止不会让运行失败，报告会写明限制。
- **失败语义**：暂时性错误（超时、数据库/存储瞬时故障）交给 Celery 退避重试；永久性错误立即失败并写入可读的 `error_summary`。

## 配置

全部变量及默认值见 [`.env.example`](.env.example)。最常用的几组：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RESEARCH_PROVIDER_MODE` | `auto` | `auto` / `real` / `mock`；无真实提供商时回退 Mock |
| `OPENAI_API_KEY` | 空 | 非空时注册 OpenAI 兼容 LLM；为空则完全离线 |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` | OpenAI / `gpt-4o-mini` | 可指向任意兼容网关（vLLM、Ollama 等） |
| `RESEARCH_LLM_OUTLINE_ENABLED` | `false` | 不启用工具型规划代理时，由模型一次性生成提纲 |
| `RESEARCH_LLM_WRITING_ENABLED` | `false` | 模型分章节写作（可发起一次补检） |
| `RESEARCH_LLM_POLISH_ENABLED` | `false` | 全文语言润色 |
| `RESEARCH_AGENT_ENABLED` | `false` | 审批前的规划代理与审批后的取证代理；需要支持工具调用的 LLM 与启用的检索提供商，否则使用确定性提纲与检索路径 |
| `RESEARCH_AGENT_MAX_ROUNDS` / `RESEARCH_AGENT_MAX_TOOL_CALLS` | `6` / `18` | 单次代理运行的模型轮次与工具调用总数；检索、阅读、正文抓取与引文查询各消耗一次工具调用，因此工具调用额度必须大于检索额度 |
| `RESEARCH_AGENT_MAX_SEARCHES` / `RESEARCH_AGENT_MAX_SOURCES` / `RESEARCH_AGENT_MAX_FETCHES` / `RESEARCH_AGENT_MAX_CITATION_CALLS` | `12` / `16` / `6` / `4` | 单次运行的检索、入库来源、正文抓取与引文查询上限；一次检索最多入库 5 条结果，来源额度由两个 agent 共享，并受 `RESEARCH_MAX_SOURCES` 限制 |
| `RESEARCH_CLAIM_LLM_REVIEW` | `false` | 对未通过的主张做保守模型复核，只能降级 |
| `RESEARCH_OUTLINE_APPROVAL_REQUIRED` | `true` | 唯一一次提纲审批；关闭则全自动 |
| `RESEARCH_MAX_SEARCH_ROUNDS` | `3` | 最大检索轮次 |
| `RESEARCH_MAX_SOURCES` / `RESEARCH_MAX_EVIDENCE` | `20` / `40` | 来源与证据上限 |
| `RESEARCH_TOKEN_BUDGET` / `RESEARCH_COST_BUDGET_USD` | `0` | 预算；0 表示不限制 |
| `MAX_ACTIVE_RUNS_PER_PROJECT` | `3` | 项目级并发运行限制，超限返回 429 |
| `LANGFUSE_ENABLED` | `false` | 可选观测；默认只发元数据，不含正文与密钥 |
| `ENABLE_DEV_ENDPOINTS` | `true` | 冒烟接口；非本地环境必须设为 `false` |

真实提供商（Tavily、Exa、Crossref、Semantic Scholar、Firecrawl）均默认关闭，需同时设置 `*_ENABLED=true` 与对应密钥。密钥只存在于后端环境变量，浏览器不可见。

修改 `.env` 后无需重新构建：

```bash
docker compose up -d --force-recreate api worker
```

修改前端公开的 API 地址 `NEXT_PUBLIC_API_BASE_URL` 需要重建 `web`。

## 开发与测试

测试与真实密钥完全隔离：`docker-compose.test.yml` 强制 `RESEARCH_PROVIDER_MODE=mock` 并清空所有提供商密钥与 LLM 开关，即使 `.env` 里有真实 Key 也不会联网或消耗配额。

```bash
# 单元 + 集成 + 评测（当前 324 passed）
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  --profile test run --rm --build test

# 只跑质量评测集
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  --profile test run --rm test python -m dr_core.evaluation
```

前端静态检查：

```bash
cd apps/web
npm ci
npm run lint && npm run typecheck && npm run build
```

数据库与运维：

```bash
docker compose exec api alembic current        # 当前迁移版本
docker compose exec api alembic check          # 模型与迁移漂移检查
docker compose exec api python -m dr_core.maintenance   # 文件记录对账
```

## 已知限制

- 没有登录与多用户权限。对外部署必须在反向代理后自行启用认证。
- 默认写作由确定性生成器完成，保证离线可测、引用可追溯，但不具备真实模型的推理与改写能力；模型开关默认关闭。
- 尚未接入真实 Embedding 服务，离线使用确定性词项哈希向量，语义召回质量有限；`auto` 模式会自动降级为关键词检索。
- 本地解析不包含 OCR，扫描版/加密 PDF 会明确失败；表格提取为尽力而为。
- 质量评测集使用 Mock 提供商，指标反映离线行为；接入真实服务后需要重新评测。
- 规划代理与取证代理需要兼容 OpenAI 的工具调用模型与真实检索提供商；离线与 Mock 运行使用一次性模型提纲（若启用）或确定性模板加确定性检索。代理输出始终经过校验，不构成引用依据。
- 引文追踪依赖学术元数据：没有 DOI 或只有非结构化文本的参考文献会作为线索返回给代理，不会入库。代理的检索以运行事件呈现，不写入检索记录表。
- 审批后的提纲是结构权威：代理不能增加库外来源或主张，取证代理也不能改动章节结构。
- 取消是协作式的，已发出的单个提供商 HTTP 请求不会被强制中断，只会受超时约束。