# Enforge · Deep Research Writing Agent

阶段 5：LangGraph 研究循环、证据矩阵、审批与断点恢复。本阶段把阶段 4 的搜索、抓取和解析能力接入由证据缺口驱动的条件工作流：规划与提纲可人工审批，审批被拒绝或修改会改变后续行为；运行状态与 LangGraph checkpoint 一起持久化，Worker 重启后可继续；轮次、来源、抓取并发、token 与成本预算到达上限时生成带明确局限说明的报告，而不是静默失败。

## 当前已实现

- Next.js 16.4 + React 19 + TypeScript + Tailwind CSS 3，按 Orkest UI 设计体系搭建的状态首页，支持中英文与明暗主题切换。
- FastAPI 健康检查：`/health`、`/ready`、`/api/v1/health`；`/ready` 同时检查 PostgreSQL、数据库迁移版本、Redis 和共享文件卷。
- 项目 API：创建、分页列表、详情、更新和软删除；软删除项目及其运行、文件对外立即不可见。
- 研究运行 API：快速返回 `run_id` 的创建接口、状态查询、取消、事件历史和 SSE 实时事件流；SSE 支持 `Last-Event-ID` 与 `after` 游标重连。
- 文件 API：项目级上传、类型/大小/内容校验、受控下载和解析状态查询；存储键由服务端生成，响应不暴露宿主机路径。上传成功后自动投递 `documents.parse` 任务。
- 外部研究工具层：`SearchProvider`、`AcademicSearchProvider`、`WebContentProvider`、`DocumentParserProvider`、`EmbeddingProvider`、`LLMProvider` 协议；Tavily、Exa、Crossref、Semantic Scholar、Firecrawl 适配器及离线 Mock Provider。
- 统一的提供商基础设施：Pydantic 结果模型、连接/读取超时、429 与 5xx 指数退避、`Retry-After`、进程内 TTL 查询缓存、SSRF 主机/端口/IP 校验和密钥脱敏。
- 本地文档解析：Worker 内使用 pypdf、trafilatura 和 BeautifulSoup 解析 PDF、HTML、Markdown 和纯文本，提取标题、章节、段落、页码及尽力而为的表格，持久化解析器版本、状态和错误。
- 来源分层入库：检索摘要、实际抓取的全文、解析成功的文档分别使用 `search_snippet`、`full_text`、`parser_status=parsed` 表达，抓取失败/付费墙/仅摘要状态记录在元数据中。
- Celery 独立入口与任务：任务 ID 等于运行 ID，重试采用指数退避，永久失败与暂时性失败分类处理，`research` 与 `parse` 队列隔离；文档解析任务只传递项目 ID 和来源版本 ID。
- 协作式取消：排队和待审批运行立即终止；执行中的任务通过数据库取消标记在安全检查点停止。Celery revoke 仅作加速，不承担正确性。
- 持久化事件系统：阶段开始/完成/失败、取消请求、重试调度和待审批事件写入 `run_events`，SSE 以数据库事件为唯一真相来源。
- LangGraph 研究工作流：16 个业务节点加 2 个审批节点，覆盖解析研究简报、规划、检索、规范化、排序、正文获取、文档解析、证据抽取、证据分析、缺口分析、补充检索、提纲、写作、引用核验和报告定稿。
- 缺口驱动的条件循环：`analyze_gaps` 根据研究问题覆盖率、证据数量与反方证据缺口决定是否进入 `revise_search_plan` 并开始下一轮检索，轮次或预算耗尽后继续生成提纲，而不是固定线性执行。
- 研究计划与提纲人工审批：通过 LangGraph `interrupt` 暂停，`run_approvals` 表保存待审批版本；批准、拒绝和修改都通过 `Command(resume=...)` 恢复并实际改变后续工作流（拒绝重新规划/重新生成提纲，修改内容直接生效）。
- 稳定线程 ID 与 PostgreSQL checkpoint：`research_runs.workflow_thread_id` 贯穿首次执行和审批恢复，`checkpoint_id` 同步到运行记录；Worker 重启、任务重试和重复投递都能从最近 checkpoint 继续，节点副作用通过数据库唯一约束保持幂等。
- 预算与终止语义：最大检索轮次、每轮查询数、来源数、抓取数、抓取并发、证据数、单节点超时、token 预算和成本预算；预算停止写入局限说明并继续产出报告，暂时性错误进入 Celery 重试，永久性错误和超时耗尽明确进入 `failed`。
- 报告导出：`research_report`、`reference_list`、`markdown_export` 三种 artifact；参考文献只从数据库来源元数据按 APA/IEEE/Chicago/MLA 生成，引用标记与参考文献一一对应，不编造文献。
- 阶段 2 完整数据模型：16 张研究业务表（新增 `run_approvals`）、项目级外键、DOI/URL 去重、幂等唯一约束、相对存储键约束和 HNSW 向量索引。
- SQLAlchemy 2.0 仓储层，覆盖项目、运行、研究问题、检索、来源、切片、证据、主张、提纲、章节、引用核验、审批、事件和导出文件。
- Alembic 从空数据库初始化、重复升级、降级/再升级和 `alembic check` 均通过；Compose 中的 `migrate` 服务在 `api`、`worker` 启动前完成迁移。
- 文件记录与实际文件对账工具，能够报告缺失文件和孤儿文件。
- 容器内单元测试和集成测试，当前 `188 passed`。
- 不包含 MinIO、S3 或任何对象存储服务。

## 服务拓扑

```text
浏览器
  │  http://localhost:3000
  ▼
web (Next.js, 3000) ──浏览器直接访问──▶ api (FastAPI, 8000)
                                            │
                     ┌──────────────────────┼─────────────────────┐
                     ▼                      ▼                     ▼
              postgres:5432            redis:6379           /app/data/files
              (pgvector)              (broker/cache)        (Named Volume)
                                             ▲
                                             │
                                        worker (Celery)
                                   queues: research, parse
                                             │
                                     /app/data/files

migrate (Alembic, 一次性任务) ──▶ postgres:5432
```

- 浏览器访问公开地址：`http://localhost:3000` 和 `http://localhost:8000`。
- 容器之间使用服务名：`api:8000`、`postgres:5432`、`redis:6379`。
- `research` 承载常规研究运行，`parse` 承载重型文档解析任务；当前单个 Worker 同时消费两个队列，部署时可以拆分为独立 Worker 进程，队列名已通过路由隔离。
- LangGraph checkpoint 保存在 PostgreSQL 的 `checkpoints`、`checkpoint_blobs`、`checkpoint_writes`、`checkpoint_migrations` 表中，与业务表使用同一数据库但由 LangGraph 管理生命周期；Alembic 不接管这四张表。
- PostgreSQL 与 Redis 不暴露宿主机端口，只能从 `app_net` 网络或 `docker compose exec` 访问。
- `migrate` 是 Compose 管理的短生命周期服务，成功执行 `alembic upgrade head` 后以状态码 0 退出；`api` 与 `worker` 只在迁移成功后启动。
- `api`、`worker` 必须使用相同的 `FILE_STORAGE_ROOT=/app/data/files` 并挂载同一 Named Volume。

## 目录结构

```text
enforge/
├── apps/web/                       Next.js 16 前端
├── services/api/                   FastAPI 应用
├── services/worker/                Celery 应用与任务
├── packages/core/                  共享配置、存储、数据库、仓储、Celery 工厂
│   └── src/dr_core/
│       ├── db/
│       │   ├── models/             SQLAlchemy 2.0 模型与枚举
│       │   ├── repositories/       按领域的 Repository
│       │   ├── base.py             声明式基类、命名约定、时间戳
│       │   └── session.py          引擎与会话工厂
│       ├── schemas/                Pydantic 输入校验
│       ├── services/               运行状态机、取消/失败/重试、审批、来源入库、解析状态
│       ├── research/               LangGraph 图、18 个节点、研究领域模型、文本与报告生成
│       ├── providers/              搜索/学术/抓取/解析接口、适配器、缓存、Mock
│       ├── tasks/                  Celery 工厂、任务名与重试退避策略
│       ├── maintenance/            文件对账与运维入口
│       ├── normalization.py        DOI、URL、指纹规范化
│       ├── embeddings.py           Embedding Provider 协议与维度校验
│       └── storage/                FileStorage 与 LocalFileStorage
├── packages/prompts/               后续阶段的版本化提示词
├── migrations/                     Alembic 环境与版本脚本
├── alembic.ini                     Alembic 配置（默认值仅用于本地开发）
├── docker/                         Dockerfile 与 Postgres 初始化脚本
├── tests/unit/                     Python 单元测试
├── tests/integration/              Docker 环境集成测试
├── tests/evaluation/               阶段 9 的质量评测集
├── docker-compose.yml
├── .env.example
├── .gitignore
├── .dockerignore
├── pytest.ini
└── README.md
```

> 根目录的 `orkest-ui/` 是设计参考项目，仅用于提取设计 token 与组件风格；已在 `.gitignore` 和 `.dockerignore` 中排除，不参与构建。

## 快速启动

前置条件：Docker Desktop 或 Docker Engine + Docker Compose 插件。宿主机不需要安装 Python 或 Node.js。

```bash
cp .env.example .env       # 可选；不创建 .env 时 Compose 使用内置开发默认值
docker compose up --build -d
docker compose ps
```

首次构建需要下载基础镜像和依赖。数据库迁移由 `migrate` 服务自动执行，失败时 `api` 和 `worker` 不会启动。所有服务健康后：

- 前端：<http://localhost:3000>
- API 文档：<http://localhost:8000/docs>
- 健康检查：

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/api/v1/health
```

`/ready` 返回四个检查项：

| 检查项 | 含义 |
|---|---|
| `database` | 能否连接 PostgreSQL |
| `schema` | `alembic_version` 是否等于当前迁移头 |
| `redis` | Redis 是否响应 PING |
| `storage` | 文件卷能否完成写入、读取和删除探测 |

任一检查失败时返回 HTTP 503。查看日志：

```bash
docker compose logs -f api worker migrate
```

停止服务但保留数据：

```bash
docker compose down
```

注意：`docker compose down -v` 会删除所有 Named Volume，包括数据库和文件，非必要不要执行。

## 业务 API 与任务系统

### 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/projects` | 创建项目，返回 201 |
| `GET` | `/api/v1/projects` | 分页列出未删除项目，支持 `limit`、`offset` |
| `GET` | `/api/v1/projects/{project_id}` | 项目详情 |
| `PATCH` | `/api/v1/projects/{project_id}` | 更新项目设置 |
| `DELETE` | `/api/v1/projects/{project_id}` | 软删除项目，返回 204；删除后项目下运行和文件均返回 404 |
| `POST` | `/api/v1/projects/{project_id}/runs` | 创建并投递研究运行，快速返回 202 与 `run_id` |
| `GET` | `/api/v1/runs/{run_id}` | 运行状态、错误摘要、取消标记和时间戳 |
| `POST` | `/api/v1/runs/{run_id}/cancel` | 请求取消运行，返回 202 |
| `GET` | `/api/v1/runs/{run_id}/events` | 事件历史，支持 `after_id`、`limit` |
| `GET` | `/api/v1/runs/{run_id}/events/stream` | SSE 事件流，支持 `Last-Event-ID` 或 `after` |
| `GET` | `/api/v1/runs/{run_id}/approvals` | 列出审批版本，支持 `status=pending/approved/rejected/modified/cancelled` |
| `POST` | `/api/v1/runs/{run_id}/approvals/{approval_id}` | 提交批准、拒绝或修改后的计划/提纲，返回 202 并投递恢复任务 |
| `POST` | `/api/v1/projects/{project_id}/files` | 上传 PDF、HTML、Markdown 或纯文本，返回 201 并异步解析 |
| `GET` | `/api/v1/files/{file_id}` | 查询文件元数据、解析状态、解析器版本和错误 |
| `GET` | `/api/v1/files/{file_id}/download` | 受控下载，响应不包含存储路径 |

示例：

```bash
PROJECT_ID=$(curl -s -X POST http://localhost:8000/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"title":"示例研究","topic":"可追溯证据链","language":"zh"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

RUN_ID=$(curl -s -X POST "http://localhost:8000/api/v1/projects/$PROJECT_ID/runs" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-run-1' \
  -d '{}' | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

curl -s "http://localhost:8000/api/v1/runs/$RUN_ID"
curl -s "http://localhost:8000/api/v1/runs/$RUN_ID/events"
curl -N "http://localhost:8000/api/v1/runs/$RUN_ID/events/stream" -H 'Last-Event-ID: 0'
curl -F 'file=@paper.pdf;type=application/pdf' \
  "http://localhost:8000/api/v1/projects/$PROJECT_ID/files"
```

运行创建接口支持 `Idempotency-Key` 请求头或请求体中的 `idempotency_key`，长度上限 128。相同项目内重复提交同一个键会返回已有运行（HTTP 200），不会重复投递任务。若队列不可用，运行会被标记为 `failed` 并返回 HTTP 503 和 `run_id`，不会留下永远排队的假象。

### 运行状态与取消

完整状态迁移：

```text
queued → planning ⇄ awaiting_approval（研究计划）
             │
             ▼
         searching → processing_sources → building_evidence
             ▲                                   │
             └──── revise_search_plan（发现缺口）◀┘
                                                 │（无缺口或预算耗尽）
                                                 ▼
                    awaiting_approval（提纲）→ writing → verifying → completed

任意非终态 → failed / cancelled
```

`awaiting_approval` 可以因计划拒绝回到 `planning`、因计划批准进入 `searching`、因缺口循环带着新证据再次进入（等待提纲审批）或在提纲批准后进入 `writing`；`building_evidence` 可以回到 `searching` 执行补充检索。终态为 `completed`、`failed`、`cancelled`，不可再迁移。非法迁移由服务层拒绝并返回 HTTP 409。

取消语义：

- `queued` 或 `awaiting_approval`：请求取消时立即写入 `cancel_requested_at` 并终止为 `cancelled`。
- 执行中的状态：写入取消标记和 `cancel_requested` 事件；Worker 在下一个安全检查点调用同一终止逻辑，确保工作流真正停止，而不只是修改数据库状态。
- Celery revoke 以 `terminate=False` 发送，仅用于让尚未开始的任务尽快失效；正确性由数据库标记与 Worker 检查保证。
- 已终止的运行再次取消会幂等返回 `cancelled`；已失败或已完成的运行返回 409。

### 事件与 SSE

`run_events` 是持久化业务事件，包含自增 bigint `id`、事件类型、阶段、消息和 JSON payload。研究运行时产生的事件包括：

- 生命周期：`run_created`、`phase_started`、`phase_completed`、`cancel_requested`、`run_cancelled`、`retry_scheduled`、`phase_failed`
- 工作流进度：`progress`、`source_fetched`、`source_parsed`、`evidence_extracted`、`section_generated`、`citation_audit_failed`
- 审批：`awaiting_approval`、`approval_decided`（payload 包含审批 ID、类型、版本与决定）

SSE 使用数据库轮询，每页最多 500 条，按事件 `id` 严格递增输出；格式为 `id`、`event`、`data` 三个字段，并首先发送 `retry: 3000`。客户端断线后携带 `Last-Event-ID`（或 `after` 查询参数）重连，只接收该 ID 之后的事件。事件有去重键约束，重复任务投递不会重复产生同一业务事件。运行进入终态后，服务端在发送完剩余事件后关闭连接；空闲时每 15 秒发送注释行保活。SSE、Celery 结果和未来 LangGraph checkpoint 是三种不同机制，不能互相替代。

### Celery 任务

- 入口：`services/worker/app/celery_app.py`，任务注册在 `app.tasks.*`。
- 任务名：`research.execute_run`、`documents.parse`、`debug.ping`、`storage.smoke`。
- 队列：`research`（常规研究）与 `parse`（重型解析），由 `CELERY_TASK_DEFAULT_QUEUE`、`CELERY_PARSE_QUEUE` 配置。
- 任务消息只包含运行 ID、来源版本 ID、项目 ID 和必要的布尔开关，不包含主题正文、文献全文或 API Key。
- 研究任务 ID 等于运行 ID；审批恢复任务 ID 为 `{run_id}:resume:{approval_id}`，消息只额外携带审批 ID，不携带计划或正文。重复投递分别由运行状态机、审批状态、事件去重键和 checkpoint 保证幂等。
- 暂时性失败（节点超时、数据库/存储瞬时错误、提供商暂时不可用）抛出 `TransientRunError`，记录 `retry_scheduled` 事件后按 `1, 2, 4, 8…` 秒退避重试，单次上限 30 秒，默认最多 3 次；达到上限后标记 `failed`。
- 永久性失败抛出 `PermanentRunError`，立即标记 `failed` 并写入可读 `error_summary`；未预期异常同样按失败处理。
- 任务配置 `acks_late`、`reject_on_worker_lost`、prefetch=1、软/硬超时，避免 Worker 异常退出导致任务被静默确认。
- 业务状态以 PostgreSQL 为准；Redis 只保存队列消息和可过期的任务结果。Worker 重启后从数据库重新读取运行状态，LangGraph 从 `workflow_thread_id` 对应的最近 checkpoint 继续；已提交的阶段不会重复创建业务记录。
- `GET /api/v1/dev/tasks/{task_id}` 可查询 Celery 结果，仅用于开发验收。

研究任务现在执行完整 LangGraph 工作流：首次投递从 `parse_brief` 开始，遇到审批节点暂停并向 API 返回 `approval_id`；审批决定后由独立恢复任务以 `Command(resume=...)` 继续。运行终态、待审批和取消状态都先写入数据库，再反映到 Celery 返回值，避免队列结果与业务状态不一致。

### 文件接口规则

- 允许类型：`application/pdf`、`text/html`、`application/xhtml+xml`、`text/markdown`、`text/x-markdown`、`text/plain`。
- PDF 必须包含 `%PDF-` 文件头；文本类文件必须是 UTF-8 且不含 NUL 字节；空文件拒绝。
- 默认大小上限 5 MiB（`MAX_UPLOAD_BYTES`）；请求使用流式读取，超限返回 413。
- 原始文件名只用于展示和下载头，去除目录、控制字符和首尾点；实际存储键由服务端生成，形如 `projects/{project_id}/sources/{uuid}/original.pdf`。
- 上传先写文件卷，再写数据库；数据库失败时会清理刚写入的文件，避免孤儿文件。
- 上传成功后投递 `documents.parse`，Worker 在 `parse` 队列解析；`GET /api/v1/files/{file_id}` 可轮询 `pending`、`parsing`、`parsed`、`failed` 及 `parse_error`。
- 解析失败只影响该文件，不删除原始文件，也不影响其他来源继续解析。
- 项目不存在或已软删除时，上传、元数据查询和下载均返回 404；下载响应设置 `Content-Disposition: attachment`、`X-Content-Type-Options: nosniff` 和 `Cache-Control: private, no-store`。

## 外部研究工具层与文档解析

提供商代码位于 `packages/core/src/dr_core/providers/`，FastAPI 路由和 Celery 任务不直接拼接第三方 API 调用：

| 能力 | 接口 | 实现 |
|---|---|---|
| 网页搜索 | `SearchProvider` | Tavily、Exa、`MockSearchProvider` |
| 学术检索 | `AcademicSearchProvider` | Crossref、Semantic Scholar、`MockAcademicSearchProvider` |
| 网页正文 | `WebContentProvider` | Firecrawl、`MockWebContentProvider` |
| 文档解析 | `DocumentParserProvider` | 本地 pypdf/trafilatura/BeautifulSoup、`MockDocumentParser` |
| 向量 | `EmbeddingProvider` | 现有协议与 `MockEmbeddingProvider` |
| 文本生成 | `LLMProvider` | 协议与 `MockLLMProvider`，真实模型在后续阶段接入 |

### 提供商开关与限制

所有真实提供商默认关闭；未配置 Key 时不会发起外部请求，系统仍可启动和使用 Mock 数据测试：

| 提供商 | 开关 | 密钥 | 说明 |
|---|---|---|---|
| Tavily | `TAVILY_ENABLED` | `TAVILY_API_KEY` | 最多 20 条/次，`offset` 通过请求更多结果后切页模拟 |
| Exa | `EXA_ENABLED` | `EXA_API_KEY` | 最多 100 条/次，使用 `numResults` |
| Crossref | `CROSSREF_ENABLED` | 无必填 | 可选 `CROSSREF_MAILTO` 加入 polite pool |
| Semantic Scholar | `SEMANTIC_SCHOLAR_ENABLED` | `SEMANTIC_SCHOLAR_API_KEY`（可选） | 标题/DOI 检索、参考文献与被引列表 |
| Firecrawl | `FIRECRAWL_ENABLED` | `FIRECRAWL_API_KEY` | 抓取经筛选 URL 的正文，限制并发与体积 |

- Crossref 不提供被引论文列表，`supports_citations=false`，`get_citations` 返回空列表；Semantic Scholar 支持 references 与 citations。
- 学术结果按 DOI 优先、否则按规范化标题 + 年份去重；网页结果按规范化 URL 去重并保留较高评分。
- 聚合层 `collect_web_search`、`collect_academic_search`、`collect_references`、`collect_citations` 和 `fetch_web_content` 会隔离单个提供商异常，返回成功结果和结构化的 `ProviderFailure`，不会因一个提供商故障无条件中断研究任务。
- 单条无法校验的结果（例如缺少标题或非法 URL）会被跳过，不伪造字段。

### 超时、重试与缓存

- 连接超时、读取超时分别由 `PROVIDER_CONNECT_TIMEOUT_SECONDS`、`PROVIDER_REQUEST_TIMEOUT_SECONDS` 控制。
- `PROVIDER_MAX_RETRIES` 控制重试次数；408、425、429、5xx、连接错误和超时可以重试，指数退避从 `PROVIDER_RETRY_BACKOFF_SECONDS` 开始，最大 30 秒。
- 429 优先遵守 `Retry-After`；最终仍失败时抛出统一的 `ProviderRateLimited`，聚合层记录失败并继续其他提供商。
- 查询缓存键包含提供商、操作、查询语句、分页和关键参数；默认 TTL 900 秒、最多 512 条、进程内线程安全。缓存只保存成功结果，不缓存失败；Worker 重启后缓存自然失效，不影响数据库业务状态。
- HTTP 错误消息不包含请求头、API Key 或完整响应体；Firecrawl 返回的错误文本会先脱敏再截断。

### Firecrawl 与抓取状态

- 抓取前校验 URL 的协议、端口、凭据、主机名和解析后的 IP；拒绝 localhost、单标签主机、私网/回环/链路本地/保留地址及非 80/443 端口。
- Firecrawl 负责实际重定向；返回的最终 URL 会再次做安全检查，最终地址不安全时标记为 `blocked`。
- 抓取结果区分 `success`、`abstract_only`、`paywalled`、`failed`、`blocked`、`too_large`；正文超过 `WEB_FETCH_MAX_BYTES` 时不落库，短正文但有描述时标记 `abstract_only`。
- 成功抓取的规范化 Markdown 正文写入 `/app/data/files/projects/{project_id}/sources/{source_id}/fetched/{hash}.md`，并登记为 `source_versions.content_kind=full_text`；原始 URL、最终 URL、抓取时间、内容哈希、HTTP 状态和提供商写入版本元数据。
- 失败、付费墙和无全文的结果不创建全文版本，而是在 `sources.metadata.last_fetch` 中保留状态、时间和错误；`abstract_only` 的可用摘要登记为 `search_snippet`。
- `FIRECRAWL_MAX_CONCURRENCY` 限制单进程并发抓取数。

### 本地文档解析

- 接受与上传接口一致的 PDF、HTML/XHTML、Markdown 和纯文本；解析前再次校验 MIME 与文件魔数、UTF-8/NUL、空文件和输入大小。
- PDF 使用 pypdf 按页提取文本；扫描版或加密 PDF 无法可靠提取时明确失败，不生成占位正文。
- HTML 使用 trafilatura 提取主正文，并用 BeautifulSoup 补充标题、章节和段落结构；Markdown 解析标题层级和段落；纯文本不虚构标题。
- 表格仅做尽力提取，HTML 表格和 Markdown 表格标记为 `best_effort`，PDF 标记为 `not_available`。
- `PARSER_MAX_PDF_PAGES`、`PARSER_MAX_OUTPUT_CHARS`、`PARSER_MAX_INPUT_BYTES` 限制资源消耗；截断时 `truncated=true`、状态为 `partial`，并丢弃不可靠的结构。
- 解析器版本为 `local-1.0`；成功后写入 `parser_status=parsed`、`parser_version` 和标题/页数/段落数/章节数/表格状态等摘要。失败写入 `parser_status=failed`、`parse_error`，不覆盖已成功解析的版本。
- 解析任务 `documents.parse` 只接收 `source_version_id` 和 `project_id`，不携带原文；读取共享文件卷后解析，失败不会影响其他来源。

### 来源内容分层

| 形态 | 数据库表示 | 是否有文件 |
|---|---|---|
| 检索结果摘要 | `source_versions.content_kind=search_snippet`，元数据含 `snippet`、provider、score | 否 |
| 实际抓取全文 | `source_versions.content_kind=full_text`，`fetched_url`、`content_hash` | 是，Markdown 正文 |
| 解析成功文档 | 同样的来源版本上 `parser_status=parsed`，元数据含解析摘要 | 原文件保留 |

所有缓存、重试和解析结果都经过 Pydantic 模型校验；Mock Provider 提供确定性的网页、学术、抓取、解析、Embedding 和 LLM 结果，测试完全离线。

## LangGraph 研究工作流与审批

工作流代码位于 `packages/core/src/dr_core/research/`：

| 模块 | 职责 |
|---|---|
| `models.py` | `ResearchBrief`、`ResearchPlan`、`ResearchQuestion`、`SearchQuery`、`SourceRecord`、`EvidenceRecord`、`ResearchGap`、`EvidenceMatrix`、`Outline`、`CitationAudit` 等领域模型及约束 |
| `state.py` | LangGraph 状态：简报、计划、查询、轮次、来源、证据 ID、矩阵、缺口、提纲、章节、引用核验、artifact、反馈与局限 |
| `context.py` | 数据库/存储/提供商上下文、取消与终态检查、节点超时、预算读取、事件写入 |
| `nodes.py` | 全部节点、条件路由和报告/参考文献生成 |
| `graph.py` | 图构建、PostgreSQL checkpointer、首次执行/恢复/重试入口 |
| `generation.py` | 确定性的计划、查询、缺口查询、提纲与章节生成 |
| `text.py` | 中英文分句/停用词、证据句提取、引用标记解析和 APA/IEEE/Chicago/MLA 参考文献格式化 |

### 节点与条件路由

规范中的 16 个研究节点全部实现，并额外提供两个显式审批节点：

| 节点 | 行为 |
|---|---|
| `parse_brief` | 校验研究简报，建立主问题与子问题，确定语言、深度、引用风格与预算 |
| `plan_research` | 生成版本化研究计划；被拒绝后根据反馈生成新版本 |
| `await_plan_approval` | 写入待审批记录并 `interrupt`；批准进入检索，拒绝回到规划，修改内容直接生效 |
| `generate_queries` | 按轮次及研究问题生成网页/学术查询，跳过重复语句 |
| `search_sources` | 调用已启用的搜索与学术提供商，记录查询执行状态、来源和所属研究问题 |
| `normalize_sources` | DOI/URL/标题去重、元数据合并、来源类型与问题关联归一化 |
| `rank_sources` | 按相关性、来源类型与可获取全文能力做确定性排序，写入 `rank_score` |
| `fetch_full_text` | 受控并发抓取候选来源全文；失败、付费墙、仅摘要等状态保留，不伪造全文 |
| `parse_documents` | 使用本地解析器解析已抓取或上传文档，持久化解析器版本、状态和失败原因 |
| `extract_evidence` | 从全文或摘要中抽取与问题相关的证据句，写入可追溯证据记录并按指纹幂等 |
| `analyze_evidence` | 构建证据矩阵，统计研究问题覆盖、支持/反驳数量与来源分布 |
| `analyze_gaps` | 识别未覆盖问题、证据不足和缺少反方证据等缺口，生成定向补充查询 |
| `revise_search_plan` | 轮次加一，记录补充检索原因；达到最大轮次或预算上限时停止循环 |
| `generate_outline` | 生成结构化提纲；被拒绝后根据反馈生成新版本并加入反馈章节 |
| `await_outline_approval` | 写入待审批记录并 `interrupt`；批准进入写作，拒绝重新生成提纲，修改后提纲生效 |
| `write_sections` | 按提纲和证据矩阵逐节生成正文，只引用数据库中已有证据，并记录引用映射 |
| `verify_citations` | 校验正文引用标记、来源存在性和引用映射，写入 `citation_audits` 记录 |
| `finalize_report` | 生成报告、参考文献列表与 Markdown artifact，写入完成事件并终止运行 |

关键条件边：

- `analyze_gaps → revise_search_plan → search_sources`：存在缺口时进入下一轮检索；该循环由数据库中的证据覆盖情况驱动，不是固定轮数。
- `analyze_gaps → generate_outline`：无缺口、达到 `RESEARCH_MAX_SEARCH_ROUNDS` 或预算耗尽时继续写作；预算停止会写入 `limitations` 并在报告中明确说明。
- `await_plan_approval` 与 `await_outline_approval`：批准/拒绝走不同分支；修改后的计划或提纲替换 LangGraph 状态并由 API 同步到数据库。

### Checkpoint、恢复与幂等

- `research_runs.workflow_thread_id` 在创建运行时生成且永不改变；`research_runs.checkpoint_id` 在每次图执行后更新为最近的 LangGraph checkpoint。
- checkpoint 使用官方 `PostgresSaver`，保存在 PostgreSQL 中；四个 checkpoint 表由 LangGraph 创建和迁移，Alembic 通过 `include_object` 排除它们，避免误删或重复创建。
- 首次执行从 `START` 开始；审批决定后使用 `Command(resume=payload)` 恢复；任务重试或 Worker 重启时若存在 checkpoint 则调用 `graph.invoke(None, config)` 继续。若运行已不再等待审批，则忽略过期 resume。
- 节点副作用以数据库唯一约束为基础：研究问题、检索查询、来源、来源版本、证据、提纲版本、章节版本、事件、审批和 artifact 都有幂等键或唯一约束；`RunApprovalRepository.request` 按 `(run_id, kind, version)` 去重，因此恢复时重新执行审批前置代码不会产生重复记录。
- 工作流状态只保存 ID 和紧凑数据，全文留在共享文件卷，证据正文留在数据库；因此 checkpoint 不会因全文而膨胀。
- 每个节点边界都检查取消标记和运行终态；取消后不再启动新节点，已写入的事件和记录保留。

### 审批 API

待审批版本保存在 `run_approvals`：`kind`（`plan`/`outline`）、`status`（`pending`/`approved`/`rejected`/`modified`/`cancelled`）、`version`、`payload`、`feedback`、`decision_payload` 和 `decided_at`。同一运行的同一类型可以保存多个版本，但 `(run_id, kind, version)` 唯一。

```bash
# 查询待审批项
curl -s "http://localhost:8000/api/v1/runs/$RUN_ID/approvals?status=pending"

# 批准研究计划
curl -s -X POST "http://localhost:8000/api/v1/runs/$RUN_ID/approvals/$APPROVAL_ID" \
  -H 'Content-Type: application/json' \
  -d '{"decision":"approved"}'

# 拒绝提纲并给出修改意见
curl -s -X POST "http://localhost:8000/api/v1/runs/$RUN_ID/approvals/$APPROVAL_ID" \
  -H 'Content-Type: application/json' \
  -d '{"decision":"rejected","feedback":"Add a section on enforcement costs"}'

# 提交修改后的计划或提纲（必须符合对应领域模型）
curl -s -X POST "http://localhost:8000/api/v1/runs/$RUN_ID/approvals/$APPROVAL_ID" \
  -H 'Content-Type: application/json' \
  -d '{"decision":"modified","content":{...}}'
```

- 只有 `awaiting_approval` 运行上的 `pending` 审批可以被决定；重复决定返回 409。
- `modified` 请求的 `content` 会先按 `ResearchPlan` 或 `Outline` 校验，非法内容返回 422；提纲修改会同步到 `outlines` 表。
- 决定成功后写入 `approval_decided` 事件并投递恢复任务；队列不可用时审批会被重置为 `pending`，接口返回 503，避免出现“已批准但工作流没有恢复”的状态。
- 取消运行会把待审批记录标记为 `cancelled`；已取消的审批不能恢复。
- `RESEARCH_PLAN_APPROVAL_REQUIRED=false` 或 `RESEARCH_OUTLINE_APPROVAL_REQUIRED=false` 时对应节点直接继续，适用于全自动运行。

### 预算、超时与失败语义

| 限制 | 配置 | 行为 |
|---|---|---|
| 最大检索轮次 | `RESEARCH_MAX_SEARCH_ROUNDS` | 达到后停止补充检索，继续提纲与写作 |
| 每轮查询数 | `RESEARCH_MAX_QUERIES_PER_ROUND` | 限制生成的查询数量 |
| 最大来源数 | `RESEARCH_MAX_SOURCES` | 排名后截断参与抓取和证据抽取的来源 |
| 每轮抓取数 / 并发 | `RESEARCH_MAX_FETCHES_PER_ROUND`、`RESEARCH_MAX_FETCH_CONCURRENCY` | 控制外部抓取压力 |
| 最大证据数 | `RESEARCH_MAX_EVIDENCE` | 达到后停止继续抽取 |
| 单节点超时 | `RESEARCH_NODE_TIMEOUT_SECONDS` | 超时抛出 `TransientRunError` 并进入重试 |
| token / 成本预算 | `RESEARCH_TOKEN_BUDGET`、`RESEARCH_COST_BUDGET_USD`、项目 `budget_usd` | 从运行累计用量读取；达到任一上限时停止新一轮检索，写入局限说明 |

- 预算停止不等于运行失败：报告仍会生成，`limitations` 中记录触发的预算类型，帮助读者判断证据完整性。
- 暂时性错误（节点超时、数据库/存储瞬时故障）交给 Celery 重试；永久性错误（状态/计划/审批数据不合法）立即标记 `failed`。
- 搜索、抓取和解析的单个提供商/单条来源失败不会终止整个研究；失败状态保留在来源元数据和事件中。

### 提供商模式

`RESEARCH_PROVIDER_MODE` 控制工作流使用的提供商：

- `auto`（默认）：优先使用已启用并配置密钥的真实提供商；非生产环境下若没有任何真实提供商，自动回退到确定性 Mock。
- `real`：只使用真实提供商；未配置时搜索为空，但工作流仍会以明确的局限说明完成。
- `mock`：强制使用 Mock，便于离线验收和测试。

报告正文与 Mock Provider 的内容生成是确定性的，但引用只能来自实际写入数据库的来源；当没有可引用证据时，章节会明确说明缺少证据，而不是编造引文。

## 数据库与迁移

### 数据模型

16 张表通过 `project_id` 归属到研究项目，并在删除项目时由数据库级联清理：

| 表 | 职责 |
|---|---|
| `research_projects` | 标题、主题、范围、语言、目标读者、研究深度、引用风格、预算、软删除时间 |
| `research_runs` | 运行状态、取消请求时间、起止时间、错误摘要、成本与 token 统计、`workflow_thread_id`、checkpoint、幂等键 |
| `research_questions` | 主问题/子问题、优先级、覆盖状态；同一运行内按规范化问题去重 |
| `search_queries` | 检索语句、提供商、轮次、结果数量、执行状态；按运行 + 轮次 + 提供商 + 语句指纹去重 |
| `sources` | 规范化文献/网页身份、标题、作者、发布日期、DOI、规范 URL、来源类型和元数据 |
| `source_versions` | 搜索摘要、全文或用户上传文档的版本、内容哈希、抓取 URL、可选的相对存储键、解析状态 |
| `document_chunks` | 章节、页码、段落、文本哈希与 `vector(1536)` 向量字段 |
| `evidence_items` | 证据摘要、原文引用、上下文、定位信息、核验状态；强制关联来源与具体来源版本 |
| `claims` | 待核验的事实/论证主张及核验状态 |
| `claim_evidence` | 主张与证据的多对多关系：支持、反驳、部分支持、相关 |
| `outlines` | 提纲 JSON、版本、审批状态和审批时间 |
| `draft_sections` | 章节正文、引用映射、使用过的证据 ID、版本与生成状态 |
| `citation_audits` | 引用核验结果、问题类型、审查方式、修正说明与修正版本 |
| `run_events` | 持久化进度/阶段/审批事件，单调递增 `id` 可直接作为 SSE `Last-Event-ID` 游标 |
| `run_approvals` | 计划/提纲审批的版本、状态、快照、反馈、修改后内容和决定时间；`(run_id, kind, version)` 唯一 |
| `artifacts` | 研究报告、参考文献、证据矩阵、Markdown/PDF/DOCX 导出的相对存储键、MIME、大小、哈希与版本 |

关键约束和行为：

- DOI 在写入前规范化（去掉 `https://doi.org/`、`doi:` 等前缀并转小写），同一项目内唯一，不同项目可各自保存同一文献。
- URL 规范化会移除片段和跟踪参数、排序查询参数、统一主机名和默认端口，并用 SHA-256 指纹保证同一项目内 URL 唯一；无法规范化的 URL 会被拒绝。
- `source_versions.storage_key` 与 `artifacts.storage_key` 只能是相对路径，数据库 CHECK 约束禁止绝对路径、反斜杠、盘符、`.`/`..` 段和非 ASCII 路径段。
- `source_versions` 通过 `content_kind` 明确区分 `search_snippet`、`full_text`、`uploaded_document`；全文和上传文档必须有存储键。
- 证据按 `(run_id, evidence_fingerprint)` 唯一，主张按 `(run_id, claim_fingerprint)` 唯一，避免任务重试重复写入；证据的原文引用保留前后上下文和 JSON 定位信息。
- 搜索查询、提纲版本、章节版本、导出文件版本和阶段事件均设置了与业务语义匹配的唯一约束或幂等键。
- 数据库中的枚举以业务值存储（例如 `queued`、`full_text`），由 CHECK 约束限制合法取值。
- 所有时间戳使用带时区的 `timestamptz`，主键为服务端 UUID（事件表为 bigint identity）。

### pgvector

- 迁移会执行 `CREATE EXTENSION IF NOT EXISTS vector`。
- `document_chunks.embedding` 的初始维度为 `1536`，由 `EMBEDDING_DIMENSION` 配置；当前同时建立 `vector_cosine_ops` HNSW 索引。
- 向量模型通过 `EmbeddingProvider` 协议与具体提供商解耦，切片的 `embedding_model` 单独记录模型标识。
- 写入和检索前会校验向量维度；不一致时抛出明确的 `EmbeddingDimensionMismatch`。
- 没有 Embedding API Key 时，数据模型、迁移和测试仍可运行；向量字段允许为空，非向量业务不受影响。
- 修改向量维度需要同时修改配置并生成新的 Alembic 迁移，不能直接改动已有数据库列。

### 常用命令

```bash
# 手动执行迁移（通常由 migrate 服务自动完成）
docker compose run --rm migrate

# 查看当前版本和历史
docker compose exec api alembic current
docker compose exec api alembic history

# 检查模型与迁移是否存在漂移
docker compose exec api alembic check
```

修改模型后生成新迁移（Windows Git Bash）：

```bash
MSYS_NO_PATHCONV=1 docker compose run --rm \
  -v "$(cygpath -m "$PWD/migrations"):/app/migrations" \
  api alembic revision --autogenerate -m "describe the change"
docker compose up -d --force-recreate migrate api worker
```

Linux/macOS 将卷参数替换为 `-v "$PWD/migrations:/app/migrations"`，并去掉 `MSYS_NO_PATHCONV=1`。迁移文件已通过挂载进入 `api` 和 `migrate` 容器，因此修改迁移脚本后无需重建镜像；如果同时修改了模型代码，仍需重建 `api`、`worker` 和 `test` 镜像。

当前迁移链：`c991dd5b285d`（阶段 2 完整架构）→ `4f6d1c8a2b90`（来源版本内容类型去重）→ `8d2a7c4e9f10`（运行取消时间与事件类型扩展）→ `c4e8f1a2b7d3`（增加 Mock 搜索提供商枚举值）→ `e5b9c3d7a1f2`（阶段 5 审批表）。阶段 5 已实际验证：

- 在已有数据库上执行 `alembic upgrade head` 成功创建 `run_approvals`、枚举 CHECK 约束和索引。
- `alembic check` 返回 `No new upgrade operations detected`；LangGraph 的四个 checkpoint 表被正确排除，不会被识别为待删除表。
- 在全新空数据库上执行完整迁移链成功创建全部 16 张业务表，LangGraph checkpoint 表按需由 `PostgresSaver.setup()` 创建。
- 在空库上执行 `e5b9c3d7a1f2 → c4e8f1a2b7d3` 降级和重新升级均成功。
- `/ready` 的 `schema` 检查在迁移完成后返回 `ok`。

## 仓储层与文件对账

数据访问逻辑位于 `packages/core/src/dr_core/db/repositories/`，与 FastAPI 路由完全分离。仓储方法不自行提交事务，由调用方通过 `Database.session_scope()` 控制提交和回滚；任何数据库异常都会向上抛出，不会静默返回伪造数据。

- `ProjectRepository`：创建、查询、分页列表、更新、软删除和恢复。
- `RunRepository`：幂等创建运行、查询、状态更新、checkpoint 记录。
- `ResearchQuestionRepository` / `SearchQueryRepository`：去重创建、列表、覆盖状态和检索执行状态。
- `SourceRepository` / `SourceVersionRepository`：DOI/URL 去重、内容哈希去重、版本递增、解析状态。
- `DocumentChunkRepository`：批量写入切片、HNSW 余弦相似度检索和维度校验。
- `EvidenceRepository` / `ClaimRepository` / `ClaimEvidenceRepository`：证据溯源、幂等抽取、主张核验状态和多对多关系。
- `OutlineRepository` / `DraftSectionRepository` / `CitationAuditRepository`：提纲审批版本、章节版本和引用核验记录。
- `RunEventRepository`：去重事件追加和按事件 ID 增量读取。
- `ArtifactRepository`：导出文件版本登记和相对存储键冲突检查。

示例：在容器内创建一个项目并登记一次运行。

```bash
docker compose exec api python - <<'PY'
from dr_core.db import get_database
from dr_core.db.repositories import ProjectRepository, RunRepository
from dr_core.schemas import ProjectCreate, RunCreate

with get_database().session_scope() as session:
    project = ProjectRepository(session).create(
        ProjectCreate(title="示例研究", topic="可追溯证据链")
    )
    run = RunRepository(session).create(project.id, RunCreate())
    print(project.id, run.id, run.status.value)
PY
```

### 文件记录对账

对账工具会查询 `source_versions.storage_key` 和 `artifacts.storage_key`，并扫描文件存储根目录：

- **缺失文件**：数据库有记录，但文件卷中不存在。
- **孤儿文件**：文件卷中存在，但两个表都没有对应记录。

`_dev/`、`_health/`、`_tmp/` 下的临时文件默认忽略。工具只报告，不会自动删除文件。

```bash
docker compose exec api python -m dr_core.maintenance
```

输出为 JSON；没有问题时退出码为 0，存在缺失或孤儿文件时退出码为 1。只读检查，可安全地在运行中的环境执行。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_NAME` | `Deep Research Writing Agent` | API 展示名称 |
| `APP_VERSION` | `0.1.0` | API 版本 |
| `ENVIRONMENT` | `development` | 运行环境 |
| `API_PORT` | `8000` | 宿主机 API 端口 |
| `WEB_PORT` | `3000` | 宿主机前端端口 |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | 浏览器访问 API 的公开地址，构建时写入前端，修改后需重建 `web` |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | 允许的浏览器来源，逗号分隔 |
| `POSTGRES_USER` | `enforge` | PostgreSQL 用户 |
| `POSTGRES_PASSWORD` | `enforge_dev_password` | 开发默认密码，非本地环境必须修改 |
| `POSTGRES_DB` | `enforge` | 数据库名 |
| `REDIS_URL` | `redis://redis:6379/0` | API 缓存/健康检查连接 |
| `CELERY_BROKER_URL` | `redis://redis:6379/1` | Celery Broker |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/2` | Celery 结果后端 |
| `CELERY_TASK_DEFAULT_QUEUE` | `research` | 常规研究任务队列 |
| `CELERY_PARSE_QUEUE` | `parse` | 重型文档解析任务队列 |
| `RUN_TASK_MAX_RETRIES` | `3` | 暂时性失败的最大重试次数 |
| `RUN_TASK_SOFT_TIME_LIMIT_SECONDS` | `300` | Celery 软超时，超时任务可被中断并标记失败 |
| `RUN_TASK_HARD_TIME_LIMIT_SECONDS` | `360` | Celery 硬超时，必须大于软超时 |
| `RESEARCH_PROVIDER_MODE` | `auto` | `auto`/`real`/`mock`；auto 在无真实提供商时回退 Mock |
| `RESEARCH_MAX_SEARCH_ROUNDS` | `3` | 最大检索轮次（含首轮） |
| `RESEARCH_MAX_QUERIES_PER_ROUND` | `6` | 每轮生成的检索查询上限 |
| `RESEARCH_MAX_SOURCES` | `20` | 参与抓取与证据抽取的来源上限 |
| `RESEARCH_MAX_FETCHES_PER_ROUND` | `8` | 每轮抓取全文的来源上限 |
| `RESEARCH_MAX_EVIDENCE` | `40` | 单次运行的最大证据数 |
| `RESEARCH_MAX_FETCH_CONCURRENCY` | `2` | 抓取全文的并发数 |
| `RESEARCH_NODE_TIMEOUT_SECONDS` | `90` | 单个工作流节点的超时；超时按暂时性错误重试 |
| `RESEARCH_TOKEN_BUDGET` | `0` | token 预算；0 表示不限制 |
| `RESEARCH_COST_BUDGET_USD` | `0` | 成本预算（美元）；0 表示不限制，与项目预算取较小正值 |
| `RESEARCH_PLAN_APPROVAL_REQUIRED` | `true` | 是否在研究计划节点暂停等待人工审批 |
| `RESEARCH_OUTLINE_APPROVAL_REQUIRED` | `true` | 是否在提纲节点暂停等待人工审批 |
| `SSE_POLL_INTERVAL_SECONDS` | `1.0` | SSE 轮询数据库事件的间隔 |
| `SSE_KEEPALIVE_SECONDS` | `15.0` | SSE 空闲保活注释间隔 |
| `FILE_STORAGE_ROOT` | `/app/data/files` | 容器内文件根目录；Docker 环境下不要修改，否则会脱离共享卷 |
| `MAX_UPLOAD_BYTES` | `5242880` | 文件 API 上传大小上限（5 MiB） |
| `TAVILY_ENABLED` / `TAVILY_API_KEY` | `false` / 空 | Tavily 搜索开关与密钥 |
| `EXA_ENABLED` / `EXA_API_KEY` | `false` / 空 | Exa 搜索开关与密钥 |
| `FIRECRAWL_ENABLED` / `FIRECRAWL_API_KEY` | `false` / 空 | Firecrawl 抓取开关与密钥 |
| `CROSSREF_ENABLED` / `CROSSREF_MAILTO` | `false` / 空 | Crossref 开关与可选联系邮箱 |
| `SEMANTIC_SCHOLAR_ENABLED` / `SEMANTIC_SCHOLAR_API_KEY` | `false` / 空 | Semantic Scholar 开关与可选密钥 |
| `PROVIDER_REQUEST_TIMEOUT_SECONDS` | `20` | 提供商读取超时 |
| `PROVIDER_CONNECT_TIMEOUT_SECONDS` | `5` | 提供商连接超时 |
| `PROVIDER_MAX_RETRIES` | `3` | 429/5xx/网络错误的最大重试次数 |
| `PROVIDER_RETRY_BACKOFF_SECONDS` | `0.5` | 指数退避初始值，单次等待上限 30 秒 |
| `PROVIDER_CACHE_TTL_SECONDS` | `900` | 查询结果缓存 TTL；0 表示禁用 |
| `FIRECRAWL_MAX_CONCURRENCY` | `2` | Firecrawl 单进程并发抓取上限 |
| `WEB_FETCH_MAX_BYTES` | `2000000` | 单个抓取正文大小上限 |
| `WEB_FETCH_MIN_CONTENT_CHARS` | `400` | 低于该长度且有描述时标记为仅摘要 |
| `PARSER_MAX_INPUT_BYTES` | `5242880` | 本地解析输入上限 |
| `PARSER_MAX_OUTPUT_CHARS` | `2000000` | 解析文本上限，超出时截断并标记 partial |
| `PARSER_MAX_PDF_PAGES` | `200` | 单文档 PDF 最大解析页数 |
| `PARSE_TASK_SOFT_TIME_LIMIT_SECONDS` | `240` | 解析任务软超时 |
| `PARSE_TASK_HARD_TIME_LIMIT_SECONDS` | `300` | 解析任务硬超时 |
| `EMBEDDING_DIMENSION` | `1536` | pgvector 向量维度；修改后必须生成新的迁移 |
| `ENABLE_DEV_ENDPOINTS` | `true` | 是否启用临时存储/任务冒烟接口；生产环境必须设为 `false` |

`.env.example` 中没有真实密钥。真实搜索/抓取提供商默认关闭，未配置任何第三方 Key 时系统仍能启动和运行全部测试；测试使用确定性的 Mock Provider，不访问网络。真实提供商调用只发生在显式启用并配置 Key 之后。

## 文件存储

实现位置：

- 接口：`packages/core/src/dr_core/storage/base.py`
- 本地实现：`packages/core/src/dr_core/storage/local.py`
- 存储键校验：`packages/core/src/dr_core/storage/keys.py`
- 实例工厂：`packages/core/src/dr_core/storage/factory.py`

约束：

- 所有文件只通过 `FileStorage` 接口读写，业务代码不自行拼接文件系统路径。
- 数据库只保存相对存储键，例如 `projects/{project_id}/sources/{file_id}/original.pdf`、`projects/{project_id}/sources/{source_id}/fetched/{hash}.md`、`projects/{project_id}/exports/{artifact_id}/report.md`，绝不保存宿主机绝对路径。
- `LocalFileStorage` 拒绝绝对路径、`..`、反斜杠、盘符、空路径段、空字节和越界符号链接。
- 写入使用临时文件 + `os.replace` 原子替换，避免半写入文件；删除后清理空目录。
- API、Worker 均以 UID 10001 运行，共享 Named Volume `enforge_research_files`。
- 正式上传/下载接口见“业务 API 与任务系统”；测试容器也挂载同一文件卷，用于验证跨容器读写和清理上传文件。

开发端点（仅在 `ENABLE_DEV_ENDPOINTS=true` 时注册）：

- `POST /api/v1/dev/storage-smoke`：上传文件，API 写入共享卷，并投递 Worker 任务。
- `GET /api/v1/dev/storage-smoke/{file_id}`：读取 Worker 生成的结果；未完成时返回 202。
- `GET /api/v1/dev/storage-smoke/{file_id}/original`：读回 API 上传的原始文件。
- `DELETE /api/v1/dev/storage-smoke/{file_id}`：删除冒烟文件。
- `POST /api/v1/dev/ping`：投递 Celery 测试任务。
- `GET /api/v1/dev/tasks/{task_id}`：查询任务状态与结果，仅用于开发验收。
- `POST /api/v1/dev/runs/{run_id}/fail`：投递一次强制失败任务，用于验证失败状态、错误摘要和 `phase_failed` 事件。

## 测试

容器内单元测试和集成测试（需要核心服务已启动）：

```bash
docker compose --profile test run --build --rm test
```

阶段 5 测试内容：

- 研究计划与提纲的 Pydantic 约束；深度感知的计划/提纲生成与反馈版本递增；条件路由在批准、拒绝、缺口与预算耗尽时的目标。
- 预算合并规则（配置与项目预算取最小正值）、来源排序分数上限、中英文停用词与 Mock URL 校验。
- 端到端 Mock 研究：计划审批、两轮条件检索、抓取、解析、证据抽取、提纲审批、写作、引用核验、报告与参考文献 artifact。
- 从数据库中读取 LangGraph checkpoint，并用新的 `PostgresSaver` 和独立执行入口恢复；验证线程 ID 与 checkpoint ID。
- 计划拒绝后重新规划、修改后的计划/提纲生效、提纲审批通过、取消后待审批记录终止，以及重复审批返回 409。
- 成本预算耗尽时停止补充检索，报告包含明确的预算限制说明；证据、检索轮次与报告内容一致。
- 审批 API 的缺失/非法修改内容返回 422，队列不可用时审批回滚为待审批并返回 503。

阶段 4 测试内容：

- 提供商 Pydantic 模型、URL/DOI 规范化、内容哈希一致性和失败对象转换。
- TTL 缓存命中、过期、容量淘汰；HTTP 500/429 退避、`Retry-After`、超时、4xx 不重试、响应大小和 404 策略。
- SSRF 防护：localhost、单标签主机、私网/回环/链路本地/保留 IP、危险端口、凭据和 DNS 失败。
- Tavily/Exa 查询转换、分页模拟、评分与日期归一化、缓存复用、密钥缺失和禁用行为。
- Crossref 标题/DOI 检索、作者、摘要清洗、参考文献和“不支持被引列表”的明确能力标识。
- Semantic Scholar 标题/DOI、references/citations、404 和可选密钥路径。
- Firecrawl 成功、仅摘要、付费墙、超大正文、不安全初始 URL 与不安全最终 URL；错误文本中的 Key 脱敏。
- 多提供商去重、单提供商故障隔离、学术元数据合并和抓取降级。
- PDF/HTML/Markdown/纯文本解析；MIME/魔数不匹配、加密/损坏/无文本 PDF、NUL/非法 UTF-8、输入与输出限制、表格最佳努力。
- 检索摘要、抓取全文、解析成功文档的数据库分层；抓取失败/付费墙状态持久化；解析失败不覆盖已成功版本。
- 上传后自动解析、损坏 PDF 失败、重复解析任务幂等、解析完成后文件元数据可查询。
- 文件对账覆盖抓取全文存储键，Mock Provider 全程离线。

阶段 3 测试内容：

- 项目创建、分页列表、详情、更新、软删除，以及删除后运行和文件返回 404。
- 运行创建快速返回 202、`Idempotency-Key` 重复提交返回同一运行、规划流水线最终进入 `awaiting_approval`。
- 重复投递同一运行任务不会重复创建研究问题或事件。
- 事件历史严格递增；SSE 使用 `Last-Event-ID` 和 `after` 重放时不丢失、不重复事件；终态运行关闭事件流。
- 排队、待审批和执行中三种取消路径；取消后不再启动新阶段，最后一个事件为 `run_cancelled`；已失败运行取消返回 409。
- 强制失败任务写入可读 `error_summary` 和单条 `phase_failed` 事件。
- 重试事件去重、指数退避上限、dispatch 参数只包含 ID、revoke 不终止 Worker。
- PDF/HTML/Markdown/纯文本上传下载一致性；MIME、PDF 文件头、UTF-8、NUL、空文件、大小上限、路径穿越和不安全文件名校验。
- 存储键为服务端相对路径；下载头包含安全策略；软删除项目后文件不可访问。

阶段 1 与阶段 2 测试内容：

- DOI、URL、问题、查询、主张和证据指纹的规范化。
- Pydantic 输入约束、相对存储键校验、向量维度异常。
- 16 张表的模型元数据、项目级外键级联和 pgvector 维度一致性。
- 项目创建、更新、软删除、恢复与项目隔离。
- 运行幂等键、状态与 checkpoint。
- 问题、检索、来源 DOI/URL、来源版本哈希和证据/主张的幂等去重。
- 证据到来源版本的溯源、主张与多证据关系、反驳证据保留。
- pgvector 余弦检索排序、维度不匹配和 HNSW 索引存在性。
- 提纲审批版本替代、章节版本、引用核验记录。
- 事件游标分页、去重，导出文件版本和存储键冲突。
- 删除项目后全部研究记录级联清理。
- 缺失文件与孤儿文件识别。
- 原有健康检查、CORS、文件跨容器往返和 Celery 测试任务。

当前结果：`188 passed`（阶段 5 新增 12 项；测试全程不需要网络或真实 API Key）。

前端静态检查：

```bash
cd apps/web
npm ci
npm run lint
npm run typecheck
npm run build
```

当前结果：ESLint 通过、TypeScript 通过、Next.js 16.4 生产构建通过。生产依赖的 `npm audit --omit=dev` 为 0 个漏洞；开发工具链仍有审计告警，不进入运行时镜像。

## 持久化验证

数据库重启持久化：

```bash
docker compose exec -T postgres psql -U enforge -d enforge -c \
  "CREATE TABLE IF NOT EXISTS persistence_marker (id int primary key, note text); \
   INSERT INTO persistence_marker VALUES (1, 'survives-restart') ON CONFLICT (id) DO NOTHING;"
docker compose restart postgres
docker compose exec -T postgres psql -U enforge -d enforge -c \
  "SELECT * FROM persistence_marker;"
```

文件卷重建持久化步骤：

1. 通过 `POST /api/v1/dev/storage-smoke` 上传测试文件，记录 `file_id` 和 SHA-256。
2. 执行 `docker compose up -d --build --force-recreate api worker`。
3. 等 `api`、`worker` 恢复 healthy 后，读取 `GET /api/v1/dev/storage-smoke/{file_id}/original` 和结果接口。
4. 对比下载内容与上传内容，二者应完全一致。

阶段 1 已实际执行以上两类验证，均通过。阶段 3 还验证了运行状态与事件在服务重启后的可恢复性：等待运行进入 `awaiting_approval` 后重启 `api`、`worker`，再查询运行与事件历史，结果保持一致；任务状态不依赖 Redis 中的临时结果。阶段 4 的解析状态、解析器版本和抓取全文同样保存在 PostgreSQL 与共享文件卷中，Worker 重启后可重新读取。

阶段 5 的 checkpoint 恢复已通过测试验证：运行停在计划或提纲审批时，`research_runs.checkpoint_id` 指向 PostgreSQL 中的 checkpoint；测试使用新的 `PostgresSaver` 读取同一 `workflow_thread_id`，并通过恢复任务继续到 `completed`。Worker 重启不改变线程 ID，也不会重复创建研究问题、来源、证据或审批记录。

## 备份与恢复

> Docker Named Volume 不是自动备份。以下命令需要在停机或至少停止 Worker 写入的窗口内执行，以保证 PostgreSQL 与文件卷尽量一致。备份完成后可运行文件对账工具检查缺失/孤儿文件。

卷名（Compose 项目名为 `enforge`）：

- `enforge_postgres_data`
- `enforge_research_files`
- `enforge_redis_data`

### 备份

```bash
mkdir -p backups

# PostgreSQL
docker compose exec -T postgres pg_dump -U enforge -d enforge -Fc > backups/enforge-db.dump

# 研究文件卷（Windows Git Bash）
BACKUP_DIR="$(cygpath -m "$PWD")/backups"
MSYS_NO_PATHCONV=1 docker run --rm \
  -v enforge_research_files:/data:ro \
  -v "$BACKUP_DIR:/backup" \
  alpine sh -c "tar -czf /backup/research_files.tar.gz -C /data ."
```

Linux/macOS 将第二个卷参数替换为 `-v "$PWD/backups:/backup"`，并去掉 `MSYS_NO_PATHCONV=1`。`backups/` 已在 `.gitignore` 中忽略，备份文件可能包含研究数据，请妥善保管。

Redis 只保存队列、结果和缓存，不作为业务真相来源，不纳入备份。恢复后未执行的 Celery 任务会丢失，业务状态由数据库记录负责恢复。

### 恢复

```bash
# 1. 停止应用写入
docker compose stop api worker web

# 2. 恢复 PostgreSQL
docker compose exec -T postgres pg_restore -U enforge -d enforge \
  --clean --if-exists < backups/enforge-db.dump

# 3. 恢复文件卷并修复容器用户权限（Windows Git Bash）
BACKUP_DIR="$(cygpath -m "$PWD")/backups"
MSYS_NO_PATHCONV=1 docker run --rm \
  -v enforge_research_files:/data \
  -v "$BACKUP_DIR:/backup" \
  alpine sh -c "find /data -mindepth 1 -delete && tar -xzf /backup/research_files.tar.gz -C /data && chown -R 10001:10001 /data"

# 4. 启动（migrate 会检查并补齐迁移）
docker compose up -d
```

恢复后检查：

```bash
curl http://localhost:8000/ready
docker compose exec -T postgres psql -U enforge -d enforge -c "\dt"
docker compose exec api python -m dr_core.maintenance
```

Linux/macOS 的恢复命令同样将第二个卷参数改为 `-v "$PWD/backups:/backup"`。数据库恢复后若迁移版本落后于应用，`migrate` 服务会升级到当前迁移头。

## 安全与已知限制

- 当前为单用户本地开发骨架，没有用户登录和项目级用户权限；“权限检查”目前指项目存在且未软删除。多用户访问控制将在后续阶段实现。
- Phase 1 冒烟接口和 `POST /dev/runs/{run_id}/fail` 仅用于验收，必须在非本地环境设置 `ENABLE_DEV_ENDPOINTS=false`。
- 阶段 5 的计划、提纲和章节正文由确定性生成器基于数据库中的研究问题与证据组织，尚未接入真实 LLM 改写与长文润色；这保证离线可测和引用可追溯，但行文风格和论证深度有限。
- 阶段 4 的提供商默认全部关闭；只有同时设置 `*_ENABLED=true` 和后端密钥才会启用。启用但缺少密钥时会记录警告并跳过，不影响服务启动。未配置任何第三方 API Key 时，系统仍可迁移、启动并通过全部测试（测试使用 Mock 提供商，不访问网络）。
- Tavily、Exa、Firecrawl、Crossref、Semantic Scholar 均在后端调用，密钥不会下发浏览器；错误信息、日志和缓存键不包含密钥。Firecrawl 只接受通过 SSRF 校验的 HTTP/HTTPS 公网地址，并在重定向后再次校验最终 URL。
- 网页全文依赖 Firecrawl；未启用时来源只保留搜索摘要。付费墙、仅摘要、屏蔽和超限响应会记录为不同抓取状态，不会当作全文入库；Crossref 不提供被引列表，因此不伪造引用关系。
- 本地解析器支持 PDF、HTML、Markdown 和纯文本，扫描版 PDF、加密或损坏文件会明确失败并记录解析错误；表格提取为尽力而为。不接入 MinerU，也不做 OCR 或版面还原。
- `documents.parse` 已注册并在 `parse` 队列执行；Compose 中单个 Worker 默认同时消费 `research,parse`，可通过队列与部署配置拆分为独立解析 Worker。
- SSE 通过数据库轮询实现，默认 1 秒间隔；对当前 MVP 足够，后续可按需改为 PostgreSQL `LISTEN/NOTIFY` 或消息总线。
- 取消是协作式的，在每个节点边界、抓取/解析/证据循环内检查；已经发出的单个提供商 HTTP 请求不会被强制中断，但会受连接/读取超时约束，且其后续节点不会启动。
- 节点超时使用 Unix 主线程的 `SIGALRM`；在非主线程或非 Unix 环境下退化为依赖提供商与数据库自身的超时。LangGraph checkpoint 的首次建表使用进程锁 + PostgreSQL advisory lock，避免多个 Worker 并发执行 `setup()`。
- Celery revoke 是尽力而为，正确性由数据库取消标记保证；Redis 结果过期后仍可从运行与事件 API 读取业务状态。
- 向量检索的仓储层与 `EmbeddingProvider` 接口/模拟实现已就绪，但尚未接入真实 Embedding 服务和 pgvector 相似度检索；当前证据抽取使用确定性的文本/问题匹配。计划在阶段 6 完成。
- token 与成本预算读取运行累计用量；Mock 提供商不上报真实费用，因此成本预算验证使用项目预算和已记录成本。真实提供商的费用统计精度取决于其结果中的 usage 字段。
- 对账工具只读，不自动删除孤儿文件；上传失败会自动清理本次写入的文件。
- PostgreSQL、Redis 不暴露宿主机端口；API 与前端端口默认暴露。
- 本地开发密码仅用于开发环境，生产部署必须更换并保管在未提交的 `.env` 中。

## 下一阶段

阶段 6 将补全真实 Embedding 服务与 pgvector 语义检索、深化证据抽取与缺口分析的召回质量，并在此基础上接入真实 LLM 长文写作与主张核验增强。在获得确认前不会提前实现后续阶段。
