# ResearchAgent 论文知识库 MVP 详细实施计划（中文伴随版）

> **面向执行代理：** 实施本计划时，必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，严格按任务顺序执行，并用 `- [ ]` 跟踪步骤。

> **规范源：** 本文与[英文详细实施计划](./2026-07-14-research-agent-mvp-implementation.md)同步。中文版本负责解释目标、顺序、接口、验收和风险；英文版本中的完整代码块、SQL、配置与命令是精确实现规范。修改任何任务时，必须同时更新两份文档的任务编号、接口和验收条件。

**目标：** 构建一个本地部署、单用户使用的论文知识库：支持上传中英文可复制文本 PDF，异步解析和索引，基于论文证据回答问题并提供页码级引用，同时允许用户按需生成、编辑和保留结构化论文笔记。

**架构：** 后端采用 FastAPI 模块化单体，Celery Worker 承担耗时任务，PostgreSQL + pgvector 保存持久数据，Redis 作为 Celery Broker，MinIO 保存 PDF 和解析产物；前端采用 Next.js。LangChain 只存在于模型适配器内部，LangGraph 用于显式问答工作流。

**技术栈：** Python 3.12、uv、FastAPI、Pydantic、SQLAlchemy 2、Alembic、PostgreSQL、pgvector、Redis、Celery 5.6、MinIO、Docling CPU、LangChain、LangGraph、Langfuse、pytest、Ruff、Pyright、Next.js、TypeScript、pnpm、Vercel AI SDK、Vitest、Playwright、Docker Compose。

## 一、全局约束

- 本计划只实现论文知识库 MVP。
- 研究脉络、相关论文推荐、创新探索 Agent、Deep Agents、Mem0、Neo4j、Elasticsearch、多用户权限、计费、OCR、自动实验执行和全文写作不在本阶段实现。
- 支持规模不超过 500 篇可复制文本的中英文 PDF。
- 上传论文后自动解析和建立索引，但绝不自动生成论文笔记。
- 基于论文的事实必须能追溯到文档版本、切片、页码和原文引用。
- 模型自身知识只能出现在单独标记的“模型补充知识”区块中。
- PostgreSQL 是持久状态的唯一事实源；Redis 丢失不能导致论文、任务、聊天或笔记状态丢失。
- API、Worker 和迁移共享同一个 Python 包，但 FastAPI、Celery、LangChain、LangGraph 等框架类型不能进入领域接口。
- 耗时任务必须幂等，只接收 ID，不在队列中传递 PDF 字节。
- 默认服务只监听 `127.0.0.1`，MinIO Bucket 必须保持私有。
- 自动化测试使用 Fake Provider；真实模型测试只能作为显式 Smoke Test 或评测命令执行。
- 自产品任务 2 起，用户持续授权 Agent 在所需验证全部通过且独立任务审查无阻断问题后，暂存精确的任务范围文件并执行 `git commit`。Agent 必须报告已提交范围、提交消息和验证证据。该持续授权不包含 `git push`、合并、移除工作树或被忽略的 SDD 产物；每项集成或清理操作仍需用户分别明确授权。

## 二、阶段检查点

1. **基础设施检查点：** Web、API、PostgreSQL、Redis、MinIO 和健康检查可运行。
2. **入库检查点：** PDF 能经过上传、解析、切片和向量化，最终持久化为 `READY`。
3. **检索检查点：** 混合检索通过合成数据与初始标注数据评测。
4. **问答检查点：** 问题能返回经过校验、明确标记且带页码引用的答案。
5. **笔记检查点：** 用户可以主动生成、编辑和保留结构化论文笔记。
6. **发布检查点：** 真实 ReID 论文集通过所有质量门槛。

发布门槛：至少 30 篇真实论文、50 个标注问题、解析成功率不低于 95%、`Recall@10` 不低于 85%、引用正确率不低于 90%、模型知识标记合规率为 100%。

## 三、设计覆盖关系

| 技术设计内容 | 实施任务 | 验证方式 |
|---|---|---|
| 框架不侵入领域模型、模块边界 | 任务 2—5、9—13、15 | Protocol 测试、静态类型检查 |
| 论文入库、解析、切片、索引 | 任务 3—9 | 检查点 1—2、幂等和重启测试 |
| 混合检索、工作流、引用和流式回答 | 任务 10—14 | 检查点 3—4、检索评测和浏览器测试 |
| 用户可选结构化论文笔记 | 任务 15 | 检查点 5、笔记版本测试 |
| 可观测性、评测、安全和部署 | 任务 16—17 | 安全测试、恢复演练和检查点 6 |
| 研究 Agent 与后续功能 | MVP 后单独制定计划 | 本计划明确排除 |

---

## 四、详细实施任务

### 任务 1：建立可执行的仓库基础

**目标：** 创建后端、前端、容器和最小健康检查，使项目从空仓库进入可运行状态。

**主要文件：** `.gitignore`、`.env.example`、`README.md`、`backend/pyproject.toml`、`backend/Dockerfile`、`frontend/Dockerfile`、`infra/compose.yaml`、`infra/redis.conf`、`backend/src/research_agent/main.py`、`config.py`。

**输出接口：** `research_agent.main:create_app`、`Settings`、`GET /health/live`，以及 `web`、`api`、`postgres`、`redis`、`minio` 服务。

- [ ] 步骤 1：使用 `uv init --package --name research-agent --python 3.12` 初始化后端，使用 `create-next-app` 初始化 TypeScript 前端。
- [ ] 步骤 2：先编写 `/health/live` 失败测试。
- [ ] 步骤 3：运行测试，确认因 `create_app` 尚不存在而失败。
- [ ] 步骤 4：实现 `Settings` 和最小 FastAPI 应用。
- [ ] 步骤 5：运行 pytest、Ruff 和 Pyright。
- [ ] 步骤 6：添加忽略规则、环境变量示例、Dockerfile 和 README。
- [ ] 步骤 7：添加 PostgreSQL、Redis、MinIO、API 和 Web Compose 服务。
- [ ] 步骤 8：运行 Compose，完成检查点 1。
- [ ] 步骤 9：准备由用户执行的提交命令。

**验收：** `/health/live` 返回 `{"status":"ok"}`；Web 可访问；PostgreSQL 和 Redis 健康；配置校验、测试和静态检查全部通过。

### 任务 2：定义不依赖框架的领域与模型 Provider 接口

**目标：** 把 LLM、Embedding 和 Reranker 隔离在项目自有 Protocol 后面，为自动化测试提供确定性 Fake。

**主要文件：** `shared/ids.py`、`shared/errors.py`、`providers/protocols.py`、`providers/fake.py`、`tests/unit/providers/test_fake_providers.py`。

**输出接口：** `new_id`、`AppError`、`ChatTurn`、`LLMProvider`、`EmbeddingProvider`、`RerankerProvider` 以及三个 Fake Provider。

- [ ] 步骤 1：编写 Fake Provider 契约失败测试。
- [ ] 步骤 2：运行测试，确认模块导入失败。
- [ ] 步骤 3：实现统一 ID 和应用错误类型。
- [ ] 步骤 4：用 `typing.Protocol` 定义 Provider 接口。
- [ ] 步骤 5：实现确定性的 Fake LLM、Embedding 和 Reranker；Fake Embedding 拒绝非正维度，并用带域分隔的 SHA-256 块支持任意正维度。
- [ ] 步骤 6：运行单元测试和静态检查。
- [ ] 步骤 7：准备用户提交检查点。

**验收：** 测试不依赖真实模型或网络；Fake Embedding 结果稳定；任何 LangChain 类型都没有进入这些接口。

### 任务 3：建立 PostgreSQL、pgvector、迁移和论文持久化

**目标：** 建立论文、版本、任务和 Outbox 的持久数据模型。

**主要文件：** `shared/db.py`、`papers/domain.py`、`papers/models.py`、`papers/repository.py`、`tasks/domain.py`、`tasks/repository.py`、`migrations/versions/0001_papers_tasks.py`。

**输出接口：** `DocumentStatus`、`VersionStatus`、`TaskStage`、`TaskStatus`、`DocumentRecord`、`PaperRepository`、`TaskRepository`、`async_session_factory`。

- [ ] 步骤 1：安装 SQLAlchemy、asyncpg、Alembic、pgvector 和 Testcontainers。
- [ ] 步骤 2：编写创建论文和版本的失败集成测试。
- [ ] 步骤 3：运行测试，确认领域模型和仓储尚不存在。
- [ ] 步骤 4：定义论文与任务状态枚举和记录类型。
- [ ] 步骤 5：建立 SQLAlchemy Model、`vector` 扩展、唯一索引和 Outbox 索引。
- [ ] 步骤 6：实现论文与任务仓储方法。
- [ ] 步骤 7：执行迁移并运行仓储测试。
- [ ] 步骤 8：准备用户提交检查点。

**关键约束：** 活跃论文的 SHA-256 唯一；同一版本同一任务阶段唯一；当前版本和最近任务必须可以从 `DocumentRecord` 得到。

### 任务 4：实现私有对象存储和幂等 PDF 上传

**目标：** 校验 PDF、计算哈希、去重、保存原文件，并在同一事务中创建任务和 Outbox 事件。

**主要文件：** `papers/storage.py`、`papers/service.py`、`papers/routes.py`、上传单元测试和 API 集成测试。

**输出接口：** `ObjectStorage`、`MinioObjectStorage`、`UploadPaperCommand`、`UploadReceipt`、`PaperService.upload_pdf`、`POST /api/v1/documents`。

- [ ] 步骤 1：安装 MinIO 和 multipart 依赖。
- [ ] 步骤 2：编写非 PDF 与重复上传失败测试。
- [ ] 步骤 3：运行测试，确认上传服务尚不存在。
- [ ] 步骤 4：定义对象存储、上传 ID、命令和结果接口。
- [ ] 步骤 5：实现 MIME、文件签名、SHA-256、MinIO 清理、数据库事务和 Outbox 创建。
- [ ] 步骤 6：添加上传路由和 `application/problem+json` 错误映射。
- [ ] 步骤 7：运行单元测试与 API 集成测试。
- [ ] 步骤 8：准备用户提交检查点。

**关键约束：** 并发上传相同 PDF 时，以数据库唯一约束为准；失败方回滚事务、删除自己的 MinIO 对象并返回已有论文，不向用户暴露 `IntegrityError`。

### 任务 5：通过 Redis 分发持久 Celery 任务

**目标：** 采用“PostgreSQL Outbox + Redis Broker + Celery Worker”完成至少一次投递，并依靠幂等任务抵御重复消息。

**主要文件：** `tasks/celery_app.py`、`tasks/outbox_relay.py`、`ingestion/celery_tasks.py`、Worker Compose 配置及测试。

**输出接口：** `celery_app`、`process_document(version_id: str)`、`OutboxRelay.run_once`。

- [ ] 步骤 1：安装 `celery[redis]`。
- [ ] 步骤 2：编写晚确认、单预取和 Visibility Timeout 配置测试。
- [ ] 步骤 3：运行测试并确认失败。
- [ ] 步骤 4：实现 Celery App、队列路由和任务导入。
- [ ] 步骤 5：实现事务 Outbox Relay。
- [ ] 步骤 6：在 Compose 中添加单并发 Worker。
- [ ] 步骤 7：测试发送失败保留 Outbox、重复投递不破坏状态。
- [ ] 步骤 8：准备用户提交检查点。

**验收：** API 进程不执行 PDF 解析；Redis 不保存永久业务状态；Worker 同时监听 `paper_ingestion` 和 `note_generation`。

### 任务 6：用 Docling 生成带版本的解析产物

**目标：** 将 Docling 隔离在解析适配器内，把 PDF 转换为项目自有的标准元素、Markdown、JSON 和质量报告。

**主要文件：** `ingestion/contracts.py`、`ingestion/docling_parser.py`、`ingestion/pipeline.py`、解析契约与集成测试。

**输出接口：** `ParsedElement`、`ParsedDocument`、`ParseQualityReport`、`DocumentParser`、`DoclingDocumentParser`、`IngestionPipeline.parse`。

- [ ] 步骤 1：配置官方 CPU PyTorch Index，安装 Docling 和 PDF 测试依赖。
- [ ] 步骤 2：编写中英文 born-digital PDF 解析契约失败测试。
- [ ] 步骤 3：运行测试并确认解析接口缺失。
- [ ] 步骤 4：定义不包含 Docling 类型的标准解析契约。
- [ ] 步骤 5：实现 Docling 到标准元素的转换。
- [ ] 步骤 6：保存 Markdown、JSON 和质量报告，执行页面和文本质量规则。
- [ ] 步骤 7：运行单元测试和一个真实 Docling 集成测试。
- [ ] 步骤 8：准备用户提交检查点。

**关键约束：** MVP 不做 OCR；扫描件和低质量解析进入 `NEEDS_REVIEW` 或 `FAILED`，不能伪装成成功。

### 任务 7：实现结构感知切片并串联入库流程

**目标：** 按章节、段落、表格和页面信息切片，保证可检索性与引用可追溯性。

**主要文件：** `ingestion/chunker.py`、`ingestion/pipeline.py`、`migrations/versions/0002_chunks.py` 及切片测试。

**输出接口：** `ChunkingConfig`、`ChunkDraft`、`StructuralChunker.chunk`、`document_chunk`、`process_document(version_id)`。

- [ ] 步骤 1：编写标题继承、页面范围、重叠和表格保护测试。
- [ ] 步骤 2：运行测试并确认切片器缺失。
- [ ] 步骤 3：实现结构切片契约和算法。
- [ ] 步骤 4：添加切片表、唯一索引和中英文检索文本字段。
- [ ] 步骤 5：串联 `VALIDATING → PARSING → NORMALIZING → CHUNKING → EMBEDDING`。
- [ ] 步骤 6：重复执行同一版本，验证不会生成重复切片。
- [ ] 步骤 7：准备用户提交检查点。

### 任务 8：实现论文库、上传和持久进度界面

**目标：** 用户可以上传 PDF、查看论文列表和持久任务进度，刷新或服务重启后仍能恢复状态。

**主要文件：** 前端 `features/papers`、`app/papers/page.tsx`、`lib/api.ts`、后端论文与任务查询路由及 Playwright 测试。

**输出接口：** `UploadPanel`、`TaskProgress`、论文列表页、列表与任务响应 Schema。

- [ ] 步骤 1：编写上传组件失败测试。
- [ ] 步骤 2：运行组件测试并确认组件缺失。
- [ ] 步骤 3：实现类型安全的论文 API Client 和 `ApiProblem`。
- [ ] 步骤 4：实现 PDF 上传、重复提示和每两秒一次的任务轮询。
- [ ] 步骤 5：实现论文列表、详情、任务详情和失败任务重试 API。
- [ ] 步骤 6：运行前端测试、类型检查和 API 契约测试。
- [ ] 步骤 7：执行第一次垂直链路 Smoke Test。
- [ ] 步骤 8：重启 API/Worker，验证论文和任务状态仍存在。
- [ ] 步骤 9：准备用户提交检查点。

### 任务 9：建立版本化 Embedding Space 并完成 READY 切换

**目标：** 对切片分批向量化，记录模型和维度，只有全部索引成功后才原子切换当前版本为 `READY`。

**主要文件：** `providers/openai_embeddings.py`、`retrieval/models.py`、`ingestion/pipeline.py`、`migrations/versions/0003_embeddings.py` 及测试。

**输出接口：** `EmbeddingSpace`、`ChunkEmbeddingModel`、`OpenAICompatibleEmbeddingProvider`、`IngestionPipeline.embed_and_activate`。

- [ ] 步骤 1：添加 LangChain OpenAI Integration。
- [ ] 步骤 2：编写 Embedding 适配器失败测试。
- [ ] 步骤 3：运行测试并确认失败。
- [ ] 步骤 4：实现不泄漏 LangChain 类型的适配器。
- [ ] 步骤 5：创建 Embedding 表和 HNSW 索引。
- [ ] 步骤 6：按批次幂等写入向量并完成原子状态切换。
- [ ] 步骤 7：测试重启、重复执行和部分失败恢复。
- [ ] 步骤 8：完成检查点 2。
- [ ] 步骤 9：准备用户提交检查点。

**验收：** PDF、解析产物、切片和向量全部持久化；只有完整版本可成为当前 `READY` 版本。

### 任务 10：实现限定范围的全文与向量检索

**目标：** 支持全库或指定论文范围内的英文全文、中文分词全文和向量召回。

**主要文件：** `retrieval/domain.py`、`retrieval/repository.py` 及检索单元和集成测试。

**输出接口：** `RetrievalScope`、`RetrievalQuery`、`RetrievalCandidate`、`lexical_search`、`vector_search`。

- [ ] 步骤 1：编写检索范围重复 ID 失败测试。
- [ ] 步骤 2：运行测试并确认领域类型缺失。
- [ ] 步骤 3：定义查询、范围和候选结果类型。
- [ ] 步骤 4：实现中英文全文 SQL 与 pgvector SQL。
- [ ] 步骤 5：验证范围过滤和仅检索 `READY` 当前版本。
- [ ] 步骤 6：准备用户提交检查点。

### 任务 11：融合、重排并评测检索结果

**目标：** 使用 RRF 融合全文与向量召回，再通过独立 Reranker 重排，并计算可重复的检索指标。

**主要文件：** `retrieval/rrf.py`、`retrieval/service.py`、`providers/cohere_reranker.py`、`evaluation` 模块及测试数据。

**输出接口：** `reciprocal_rank_fusion`、`RetrievalService.retrieve`、`RetrievalMetrics`、评测 CLI。

- [ ] 步骤 1：编写 RRF 排名失败测试。
- [ ] 步骤 2：运行测试并确认失败。
- [ ] 步骤 3：按固定公式实现 RRF。
- [ ] 步骤 4：添加具体 Reranker 适配器。
- [ ] 步骤 5：实现召回、去重、融合和重排服务。
- [ ] 步骤 6：实现 `Recall@5`、`Recall@10` 和 MRR。
- [ ] 步骤 7：运行检索测试和合成数据评测。
- [ ] 步骤 8：完成检查点 3。
- [ ] 步骤 9：准备用户提交检查点。

### 任务 12：持久化聊天并建立显式 LangGraph 查询工作流

**目标：** 把查询规划、召回、证据判断和回答生成建模为可持久、可恢复的显式状态图。

**主要文件：** `chat/domain.py`、`chat/models.py`、`chat/repository.py`、`chat/graph.py`、`chat/service.py`、聊天迁移和测试。

**输出接口：** `QueryPlan`、`ChatGraphState`、`ChatService.ask`、`chat_session`、`chat_message`、`chat_run`、`chat_run_event`。

- [ ] 步骤 1：安装 LangChain、LangGraph 和 PostgreSQL Checkpointer。
- [ ] 步骤 2：编写证据不足路由失败测试。
- [ ] 步骤 3：运行测试并确认图模块缺失。
- [ ] 步骤 4：创建聊天、消息、运行和运行事件持久化表。
- [ ] 步骤 5：定义项目自有 Graph State。
- [ ] 步骤 6：建立查询计划、检索、证据充分性、生成、校验和完成节点。
- [ ] 步骤 7：添加 PostgreSQL Checkpoint，确保重启可恢复。
- [ ] 步骤 8：运行图路由和持久化测试。
- [ ] 步骤 9：准备用户提交检查点。

### 任务 13：生成分区答案并逐条校验引用

**目标：** 将论文证据和模型补充知识严格分开，并确定性校验每个论文引用。

**主要文件：** `chat/domain.py`、`chat/citations.py`、`chat/service.py` 和引用测试。

**输出接口：** `PaperEvidenceBlock`、`ModelKnowledgeBlock`、`CitationDraft`、`VerifiedCitation`、`VerifiedAnswer`、`CitationVerifier.verify`。

- [ ] 步骤 1：编写不存在的切片、错误页码、错误原文和无引用论断测试。
- [ ] 步骤 2：运行测试并确认失败。
- [ ] 步骤 3：定义结构化答案和引用类型。
- [ ] 步骤 4：实现切片、版本、页码和 Quote 的确定性校验。
- [ ] 步骤 5：允许一次受控修复；仍失败时明确返回证据不足。
- [ ] 步骤 6：运行答案与引用测试。
- [ ] 步骤 7：准备用户提交检查点。

**关键约束：** 未通过校验的论文论断不得发送给用户；模型知识必须使用独立类型和 UI 标签。

### 任务 14：把已校验答案流式发送到 Next.js

**目标：** 使用持久 SSE 事件向前端发送状态、答案和引用，支持断线重连。

**主要文件：** `chat/stream.py`、`chat/routes.py`、前端 `features/chat`、聊天页面、契约测试和 Playwright 测试。

**输出接口：** 创建会话、提交消息和运行流 API，以及 `ChatPanel`、`CitationPanel`。

- [ ] 步骤 1：编写 SSE 契约失败测试。
- [ ] 步骤 2：运行测试并确认路由缺失。
- [ ] 步骤 3：实现持久、单调递增的 SSE 事件和 `Last-Event-ID` 恢复。
- [ ] 步骤 4：实现提交消息后异步启动问答运行。
- [ ] 步骤 5：编写聊天组件失败测试。
- [ ] 步骤 6：实现论文证据、模型知识、引用面板和 PDF 页码跳转。
- [ ] 步骤 7：运行契约、组件和浏览器测试。
- [ ] 步骤 8：完成检查点 4。
- [ ] 步骤 9：准备用户提交检查点。

### 任务 15：实现用户可选、可编辑、带版本的论文笔记

**目标：** 系统提供固定结构化模板和生成能力，但只有用户主动请求时才生成。

**主要文件：** `notes/domain.py`、`notes/models.py`、`notes/repository.py`、`notes/service.py`、`notes/celery_tasks.py`、笔记 API、迁移、前端编辑器和测试。

**输出接口：** `PaperNoteContent`、`NoteField`、`NoteEvidence`、`NoteService.request_generation`、笔记查询与更新 API、`NoteEditor`。

- [ ] 步骤 1：编写“显式生成且必须有证据”的失败测试。
- [ ] 步骤 2：运行测试并确认笔记模块缺失。
- [ ] 步骤 3：创建笔记、修订版和字段证据表。
- [ ] 步骤 4：实现固定 MVP 模板，包括研究问题、定位、方法、创新点、数据集、实验、结果、消融、局限、相关论文、研究启发和用户评论。
- [ ] 步骤 5：实现主动生成与安全重新生成。
- [ ] 步骤 6：实现逐字段编辑、Diff、接受和拒绝草稿。
- [ ] 步骤 7：运行笔记单元、集成和组件测试。
- [ ] 步骤 8：完成检查点 5。
- [ ] 步骤 9：准备用户提交检查点。

**关键约束：** 上传和入库流程不能调用笔记生成；重新生成不能覆盖当前用户版本，只有用户接受后才更新 `current_revision_id`。

### 任务 16：加入可观测性、模型用量和可重复评测

**目标：** 为请求、Celery 任务、检索和模型调用建立 Trace，并把模型用量和评测结果持久化。

**主要文件：** `shared/logging.py`、`shared/observability.py`、`evaluation/answer_eval.py`、`evaluation/cli.py`、评测迁移、合成数据集和测试。

**输出接口：** `model_usage`、`evaluation_dataset`、`evaluation_case`、`evaluation_run`、`evaluation_score`、结构化日志和可选 Langfuse Trace。

- [ ] 步骤 1：安装 structlog 和 Langfuse。
- [ ] 步骤 2：编写引用 Schema 与模型知识标记指标失败测试。
- [ ] 步骤 3：运行指标测试并确认失败。
- [ ] 步骤 4：创建模型用量与评测持久化表。
- [ ] 步骤 5：贯穿传播 `trace_id`，默认删除完整论文文本、Prompt、密钥和 MinIO Secret。
- [ ] 步骤 6：实现数据导入、检索评测、回答评测和报告命令。
- [ ] 步骤 7：运行可观测性和评测测试。
- [ ] 步骤 8：准备用户提交检查点。

**关键约束：** Langfuse 默认关闭；只有 `LANGFUSE_ENABLED=true` 时启用，而且默认只发送元数据和采样片段。

### 任务 17：安全加固、备份恢复并通过 MVP 发布门槛

**目标：** 完成运行限制、安全回归、端到端测试、真实数据评测和恢复演练。

**主要文件：** 安全测试、后端和前端 E2E、`scripts/backup.ps1`、`restore.ps1`、`verify.ps1`、真实 `reid_mvp.jsonl` 数据集及 README。

**输出接口：** 可验证的本地发布、备份恢复命令和最终质量报告。

- [ ] 步骤 1：添加文件大小、页数、CORS、模型超时、任务软硬超时和 `/health/ready`。
- [ ] 步骤 2：编写伪 PDF、错误 MIME、超大文件、路径穿越和 Prompt Injection 测试。
- [ ] 步骤 3：实现 PostgreSQL Custom Dump、MinIO Mirror、计数清单和恢复脚本；聊天计数使用实际的 `chat_message` 与 `chat_run` 表，引用随消息 JSON 一起恢复。
- [ ] 步骤 4：由用户选择至少 30 篇论文并审核至少 50 个问题及证据切片。
- [ ] 步骤 5：编写上传到笔记保存的完整后端 E2E。
- [ ] 步骤 6：建立统一 Windows 验证入口 `scripts/verify.ps1`。
- [ ] 步骤 7：运行全部自动化验证。
- [ ] 步骤 8：使用用户显式提供的 Key 运行真实 Provider Smoke Test。
- [ ] 步骤 9：运行真实 ReID 检索和回答评测。
- [ ] 步骤 10：执行备份、清空运行状态、恢复并核对持久数据。
- [ ] 步骤 11：完成检查点 6 发布审核。
- [ ] 步骤 12：准备最终用户提交命令。

**拒绝发布条件：**

- 支持范围内 PDF 解析成功率低于 95%；
- `Recall@10` 低于 85%；
- 引用正确率低于 90%；
- 模型知识标记合规率低于 100%；
- 重启或恢复导致持久状态丢失；
- 论文论断缺少已校验 Quote 和页码；
- 用户未请求时系统生成了论文笔记。

---

## 五、MVP 后续边界

只有任务 17 的发布命令通过后，才按下列顺序分别制定新计划：

1. PostgreSQL 中的论文比较和研究脉络；
2. 持久化人机研究会话、分支、快照、证据卡和假设状态；
3. 轻量相关论文推荐；
4. 假设卡和实验卡；
5. 带工具、时间、Token 和成本限制的 Deep Agents 探索；
6. 实验结果分析；
7. 基于证据的科研写作辅助。

## 六、执行与同步规则

- 必须从任务 1 开始，不允许跳过失败测试直接写实现。
- 每个检查点都要暂停，由用户确认后再继续。
- 中文版本用于理解和跟踪；需要复制代码、SQL、Docker 配置或完整命令时，使用英文规范源中相同任务、相同步骤的代码块。
- 修改英文计划时，必须同步更新中文计划的全局约束、任务编号、接口、步骤标题、检查点和验收门槛。
- 修改中文解释但不改变实现含义时，不需要反向改动英文代码块。
- 自产品任务 2 起，用户持续授权 Agent 在所需验证全部通过且独立任务审查无阻断问题后，暂存精确的任务范围文件并执行 `git commit`。Agent 必须报告已提交范围、提交消息和验证证据。该持续授权不包含 `git push`、合并、移除工作树或被忽略的 SDD 产物；每项集成或清理操作仍需用户分别明确授权。

## 双语文档目录约定（自产品任务 2 起生效）

- 新的正式设计文档使用 `docs/superpowers/specs/EN/<相同文件名>.md` 和 `docs/superpowers/specs/CN/<相同文件名>.md`；新的正式实施计划使用 `docs/superpowers/plans/EN/<相同文件名>.md` 和 `docs/superpowers/plans/CN/<相同文件名>.md`。
- SDD 工作流产物使用 `.superpowers/sdd/EN/task-N-{brief,report,review}.md` 和 `.superpowers/sdd/CN/task-N-{brief,report,review}.md`；进度文件使用 `.superpowers/sdd/EN/progress.md` 和 `.superpowers/sdd/CN/progress.md`。对应的英文和中文文件使用相同文件名，不再使用 `.zh-CN` 后缀。
- 英文是规范源。中文配套文件必须忠实保持英文实现含义，并原样保留每个代码块、命令、路径、接口名称、依赖版本、哈希、预期结果、测试证据、原始日志和任务边界。
- 每份英文文档通过 `../CN/<相同文件名>` 链接中文配套文件；每份中文文档通过 `../EN/<相同文件名>` 链接英文规范源。
- 控制器负责每组双语 Brief 和双语 Progress；实现者负责每组双语 Report；审查者负责每组双语 Review。
- 同步是交接门槛：负责角色必须创建或更新两种语言的文件并验证语义一致后，才能把工作交给下一个角色。
- 每个产品任务完成后，控制器必须停在用户验收检查点。只有用户明确授权后，才能启动下一个产品任务。
- 已有正式文档和产品任务 1 的 SDD 产物保留在当前路径。不得重新运行、重新测试、重新审查、移动产品任务 1，也不得为其追补产物。
- 自产品任务 2 起，用户持续授权 Agent 在所需验证全部通过且独立任务审查无阻断问题后，暂存精确的任务范围文件并执行 `git commit`。Agent 必须报告已提交范围、提交消息和验证证据。该持续授权不包含 `git push`、合并、移除工作树或被忽略的 `.superpowers/sdd` 工作流产物；每项集成或清理操作仍需用户分别明确授权。

## 七、官方实现参考

- [Docling 安装说明](https://docling-project.github.io/docling/getting_started/installation/)
- [Celery Redis Broker](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)
- [pgvector](https://github.com/pgvector/pgvector)
- [LangGraph 概览](https://docs.langchain.com/oss/python/langgraph/overview/)
- [Vercel AI SDK Stream Protocol](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol)
- [Langfuse Scores](https://langfuse.com/docs/evaluation/scores/overview)
