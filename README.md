# Enforge · Deep Research Writing Agent

阶段 3：FastAPI 业务 API、Celery 长任务、持久化事件与 SSE。本阶段在阶段 2 的数据模型与仓储层之上，提供项目、运行、取消、事件流和项目文件的正式接口；异步研究任务由 Celery 执行，状态与事件全部落库，支持幂等投递、重试分类、队列隔离和协作式取消。LangGraph 工作流、搜索/解析提供商与写作流程仍留待后续阶段实现。

## 当前已实现

- Next.js 16.4 + React 19 + TypeScript + Tailwind CSS 3，按 Orkest UI 设计体系搭建的状态首页，支持中英文与明暗主题切换。
- FastAPI 健康检查：`/health`、`/ready`、`/api/v1/health`；`/ready` 同时检查 PostgreSQL、数据库迁移版本、Redis 和共享文件卷。
- 项目 API：创建、分页列表、详情、更新和软删除；软删除项目及其运行、文件对外立即不可见。
- 研究运行 API：快速返回 `run_id` 的创建接口、状态查询、取消、事件历史和 SSE 实时事件流；SSE 支持 `Last-Event-ID` 与 `after` 游标重连。
- 文件 API：项目级上传、类型/大小/内容校验、受控下载；存储键由服务端生成，响应不暴露宿主机路径。
- Celery 独立入口与任务：任务 ID 等于运行 ID，重试采用指数退避，永久失败与暂时性失败分类处理，`research` 与 `parse` 队列隔离。
- 协作式取消：排队和待审批运行立即终止；执行中的任务通过数据库取消标记在安全检查点停止。Celery revoke 仅作加速，不承担正确性。
- 持久化事件系统：阶段开始/完成/失败、取消请求、重试调度和待审批事件写入 `run_events`，SSE 以数据库事件为唯一真相来源。
- 阶段 3 运行流程为规划脚手架：创建研究问题后停在 `awaiting_approval`，为阶段 5 的人工审批与完整工作流预留状态；审批接口在阶段 5 提供。
- 阶段 2 完整数据模型：15 张研究业务表、项目级外键、DOI/URL 去重、幂等唯一约束、相对存储键约束和 HNSW 向量索引。
- SQLAlchemy 2.0 仓储层，覆盖项目、运行、研究问题、检索、来源、切片、证据、主张、提纲、章节、引用核验、事件和导出文件。
- Alembic 从空数据库初始化、重复升级、降级/再升级和 `alembic check` 均通过；Compose 中的 `migrate` 服务在 `api`、`worker` 启动前完成迁移。
- 文件记录与实际文件对账工具，能够报告缺失文件和孤儿文件。
- 容器内单元测试和集成测试，当前 `135 passed`。
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
- `research` 承载常规研究运行，`parse` 预留给重型文档解析任务；当前单个 Worker 同时消费两个队列，部署时可以拆分为独立 Worker 进程，队列名已通过路由隔离。
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
│       ├── services/               运行状态机、取消/失败/重试、规划流水线
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
| `POST` | `/api/v1/projects/{project_id}/files` | 上传 PDF、HTML、Markdown 或纯文本，返回 201 |
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

阶段 3 已使用的状态迁移：

```text
queued → planning → awaiting_approval
   │         │              │
   ├─────────┴──────────────┴──▶ failed / cancelled
   └──▶ cancelled
```

`searching`、`processing_sources`、`building_evidence`、`writing`、`verifying`、`completed` 已定义合法迁移，留给阶段 5 的 LangGraph 工作流；终态为 `completed`、`failed`、`cancelled`，不可再迁移。非法迁移由服务层拒绝并返回 HTTP 409。

取消语义：

- `queued` 或 `awaiting_approval`：请求取消时立即写入 `cancel_requested_at` 并终止为 `cancelled`。
- 执行中的状态：写入取消标记和 `cancel_requested` 事件；Worker 在下一个安全检查点调用同一终止逻辑，确保工作流真正停止，而不只是修改数据库状态。
- Celery revoke 以 `terminate=False` 发送，仅用于让尚未开始的任务尽快失效；正确性由数据库标记与 Worker 检查保证。
- 已终止的运行再次取消会幂等返回 `cancelled`；已失败或已完成的运行返回 409。

### 事件与 SSE

`run_events` 是持久化业务事件，包含自增 bigint `id`、事件类型、阶段、消息和 JSON payload。阶段 3 产生的事件包括：

- `run_created`、`phase_started`、`phase_completed`、`awaiting_approval`
- `cancel_requested`、`run_cancelled`
- `retry_scheduled`、`phase_failed`

SSE 使用数据库轮询，每页最多 500 条，按事件 `id` 严格递增输出；格式为 `id`、`event`、`data` 三个字段，并首先发送 `retry: 3000`。客户端断线后携带 `Last-Event-ID`（或 `after` 查询参数）重连，只接收该 ID 之后的事件。事件有去重键约束，重复任务投递不会重复产生同一业务事件。运行进入终态后，服务端在发送完剩余事件后关闭连接；空闲时每 15 秒发送注释行保活。SSE、Celery 结果和未来 LangGraph checkpoint 是三种不同机制，不能互相替代。

### Celery 任务

- 入口：`services/worker/app/celery_app.py`，任务注册在 `app.tasks.*`。
- 任务名：`research.execute_run`；`documents.parse` 已保留给阶段 4 的解析任务。
- 队列：`research`（常规研究）与 `parse`（重型解析），由 `CELERY_TASK_DEFAULT_QUEUE`、`CELERY_PARSE_QUEUE` 配置。
- 任务消息只包含 `run_id`、`project_id` 和必要的布尔开关，不包含主题正文、文献全文或 API Key。
- Celery task ID 等于运行 ID，重复投递由运行状态机和事件去重键保证幂等。
- 暂时性失败抛出 `TransientRunError`，记录 `retry_scheduled` 事件后按 `1, 2, 4, 8…` 秒退避重试，单次上限 30 秒，默认最多 3 次；达到上限后标记 `failed`。
- 永久性失败抛出 `PermanentRunError`，立即标记 `failed` 并写入可读 `error_summary`；未预期异常同样按失败处理。
- 任务配置 `acks_late`、`reject_on_worker_lost`、prefetch=1、软/硬超时，避免 Worker 异常退出导致任务被静默确认。
- 业务状态以 PostgreSQL 为准；Redis 只保存队列消息和可过期的任务结果。Worker 重启后从数据库重新读取运行状态，已提交的阶段不会重复创建业务记录。
- `GET /api/v1/dev/tasks/{task_id}` 可查询 Celery 结果，仅用于开发验收。

阶段 3 的规划流水线会创建一条研究问题并停在 `awaiting_approval`，用于验证异步执行、事件、幂等和取消链路；搜索、解析、写作和审批将在后续阶段接入。

### 文件接口规则

- 允许类型：`application/pdf`、`text/html`、`application/xhtml+xml`、`text/markdown`、`text/x-markdown`、`text/plain`。
- PDF 必须包含 `%PDF-` 文件头；文本类文件必须是 UTF-8 且不含 NUL 字节；空文件拒绝。
- 默认大小上限 5 MiB（`MAX_UPLOAD_BYTES`）；请求使用流式读取，超限返回 413。
- 原始文件名只用于展示和下载头，去除目录、控制字符和首尾点；实际存储键由服务端生成，形如 `projects/{project_id}/sources/{uuid}/original.pdf`。
- 上传先写文件卷，再写数据库；数据库失败时会清理刚写入的文件，避免孤儿文件。
- 项目不存在或已软删除时，上传和下载均返回 404；下载响应设置 `Content-Disposition: attachment`、`X-Content-Type-Options: nosniff` 和 `Cache-Control: private, no-store`。

## 数据库与迁移

### 数据模型

15 张表通过 `project_id` 归属到研究项目，并在删除项目时由数据库级联清理：

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

当前迁移链：`c991dd5b285d`（阶段 2 完整架构）→ `4f6d1c8a2b90`（来源版本内容类型去重）→ `8d2a7c4e9f10`（运行取消时间与事件类型扩展）。阶段 3 已实际验证：

- 在已有阶段 2 数据上执行 `alembic upgrade head` 成功添加 `cancel_requested_at` 并扩展事件类型 CHECK 约束。
- `alembic check` 返回 `No new upgrade operations detected`。
- 在全新空数据库上执行完整迁移链成功创建全部 15 张业务表。
- 在空库上执行目标版本降级与重新升级均成功。
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
| `RUN_PHASE_PAUSE_SECONDS` | `0.35` | 规划阶段演示暂停秒数；便于本地观察取消与 SSE，生产可设为 0 |
| `RUN_TASK_MAX_RETRIES` | `3` | 暂时性失败的最大重试次数 |
| `RUN_TASK_SOFT_TIME_LIMIT_SECONDS` | `300` | Celery 软超时，超时任务可被中断并标记失败 |
| `RUN_TASK_HARD_TIME_LIMIT_SECONDS` | `360` | Celery 硬超时，必须大于软超时 |
| `SSE_POLL_INTERVAL_SECONDS` | `1.0` | SSE 轮询数据库事件的间隔 |
| `SSE_KEEPALIVE_SECONDS` | `15.0` | SSE 空闲保活注释间隔 |
| `FILE_STORAGE_ROOT` | `/app/data/files` | 容器内文件根目录；Docker 环境下不要修改，否则会脱离共享卷 |
| `MAX_UPLOAD_BYTES` | `5242880` | 文件 API 上传大小上限（5 MiB） |
| `EMBEDDING_DIMENSION` | `1536` | pgvector 向量维度；修改后必须生成新的迁移 |
| `ENABLE_DEV_ENDPOINTS` | `true` | 是否启用临时存储/任务冒烟接口；生产环境必须设为 `false` |

`.env.example` 中没有真实密钥。后续阶段的模型、搜索、抓取服务 Key 预留为注释项；未配置任何第三方 Key 时，系统仍能启动和运行测试。

## 文件存储

实现位置：

- 接口：`packages/core/src/dr_core/storage/base.py`
- 本地实现：`packages/core/src/dr_core/storage/local.py`
- 存储键校验：`packages/core/src/dr_core/storage/keys.py`
- 实例工厂：`packages/core/src/dr_core/storage/factory.py`

约束：

- 所有文件只通过 `FileStorage` 接口读写，业务代码不自行拼接文件系统路径。
- 数据库只保存相对存储键，例如 `projects/{project_id}/sources/{file_id}/original.pdf`、`projects/{project_id}/exports/{artifact_id}/report.md`，绝不保存宿主机绝对路径。
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

阶段 3 测试内容：

- 项目创建、分页列表、详情、更新、软删除，以及删除后运行和文件返回 404。
- 运行创建快速返回 202、`Idempotency-Key` 重复提交返回同一运行、规划流水线最终进入 `awaiting_approval`。
- 重复投递同一运行任务不会重复创建研究问题或事件。
- 事件历史严格递增；SSE 使用 `Last-Event-ID` 和 `after` 重放时不丢失、不重复事件；终态运行关闭事件流。
- 排队、待审批和执行中三种取消路径；取消后不再产生阶段完成事件或业务记录；已失败运行取消返回 409。
- 强制失败任务写入可读 `error_summary` 和单条 `phase_failed` 事件。
- 重试事件去重、指数退避上限、dispatch 参数只包含 ID、revoke 不终止 Worker。
- PDF/HTML/Markdown/纯文本上传下载一致性；MIME、PDF 文件头、UTF-8、NUL、空文件、大小上限、路径穿越和不安全文件名校验。
- 存储键为服务端相对路径；下载头包含安全策略；软删除项目后文件不可访问。

阶段 1 与阶段 2 测试内容：

- DOI、URL、问题、查询、主张和证据指纹的规范化。
- Pydantic 输入约束、相对存储键校验、向量维度异常。
- 15 张表的模型元数据、项目级外键级联和 pgvector 维度一致性。
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

当前结果：`135 passed`（包含 26 项阶段 3 单元测试和 12 项阶段 3 集成测试）。

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

阶段 1 已实际执行以上两类验证，均通过。阶段 3 还验证了运行状态与事件在服务重启后的可恢复性：等待运行进入 `awaiting_approval` 后重启 `api`、`worker`，再查询运行与事件历史，结果保持一致；任务状态不依赖 Redis 中的临时结果。

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
- 阶段 3 的运行流水线是规划脚手架，会创建研究问题并停在 `awaiting_approval`；审批接口、搜索、解析、证据构建、写作和引用核验在阶段 4 至 6 实现。
- 当前单个 Worker 同时消费 `research` 和 `parse`；`documents.parse` 任务尚未注册，拆分为独立解析 Worker 属于部署优化。
- SSE 通过数据库轮询实现，默认 1 秒间隔；对当前 MVP 足够，后续可按需改为 PostgreSQL `LISTEN/NOTIFY` 或消息总线。
- 取消是协作式的，只在流水线安全检查点生效；未来接入长时间外部调用时，需要在每个提供商调用边界增加取消检查。
- Celery revoke 是尽力而为，正确性由数据库取消标记保证；Redis 结果过期后仍可从运行与事件 API 读取业务状态。
- 向量检索能力已在仓储层实现，但尚未通过 HTTP API 暴露，也未接入 Embedding Provider；阶段 4/6 完成。
- 未配置任何第三方 API Key 时，系统可启动、迁移和运行全部测试。
- 后端日志不会记录第三方密钥；当前阶段不调用外部服务。
- 对账工具只读，不自动删除孤儿文件；上传失败会自动清理本次写入的文件。
- PostgreSQL、Redis 不暴露宿主机端口；API 与前端端口默认暴露。
- 本地开发密码仅用于开发环境，生产部署必须更换并保管在未提交的 `.env` 中。

## 下一阶段

阶段 4 将实现可替换、可测试的外部研究工具层：`SearchProvider`、`AcademicSearchProvider`、`WebContentProvider`、`DocumentParserProvider`、`EmbeddingProvider` 与 `LLMProvider`，接入 Tavily、Exa、Crossref、Semantic Scholar 和文档解析，并统一超时、重试、限流、缓存、异常与降级逻辑。在获得确认前不会提前实现后续阶段。
