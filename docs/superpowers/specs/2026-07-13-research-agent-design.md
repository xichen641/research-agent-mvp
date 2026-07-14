# ResearchAgent 技术设计

> 日期：2026-07-13
>
> 状态：已通过用户初步审核
> 面向版本：第一阶段个人单用户 MVP，并描述后续演进边界

## 1. 文档目的

本文档定义 ResearchAgent 的产品定位、阶段范围、总体架构、核心数据流、数据模型、接口边界、错误处理、安全策略、测试评测方法和部署方案。

本文档不是逐任务实施计划。设计获得书面审阅后，再基于本规范生成可执行的开发计划。

## 2. 背景与产品定位

用户是计算机视觉方向研究生，当前重点研究行人重识别。现有科研流程面临以下问题：

1. 论文数量多，文件夹和传统文献管理方式难以支持按研究问题组织材料。
2. 论文之间存在方法演进、引用、创新继承、实验设置和局限性关系，普通分类难以体现。
3. 相关论文检索分散且低效。
4. 论文精读和结构化笔记耗时。
5. 创新点需要结合大量论文证据，通过持续讨论、质疑和实验逐步形成。
6. 实验设计、结果分析和科研写作缺少统一的证据上下文。

ResearchAgent 的定位是：

> 由用户主导、AI 提供检索、比较、质疑、假设整理和实验设计能力的人机协同研究伙伴。

系统不替代研究者做创新判断，也不把一次模型生成包装成“创新发现”。创新点应由用户与 AI 在多轮、可追溯、证据驱动的讨论中逐渐形成。

## 3. 已确认的关键决策

| 决策项 | 结论 |
|---|---|
| 产品路线 | 完整产品蓝图，分阶段建设 |
| 首版用户 | 个人单用户 |
| MVP 核心价值 | 论文知识库 |
| 部署方式 | 本地应用与数据服务，在线模型 API |
| 文献入口 | 手动上传 PDF |
| PDF 范围 | 可检索的中英文论文 PDF，不保证扫描件 |
| 项目目标 | 技术学习、真实可用和求职展示并重 |
| 后端 | Python 模块化单体，FastAPI |
| 前端 | Next.js、TypeScript、Vercel AI SDK |
| 数据规模 | MVP 不超过 500 篇论文 |
| 问答边界 | 论文证据与模型通用知识并存，但必须明确区分 |
| 笔记生成 | 按需生成预设结构化笔记，支持编辑 |
| 第二阶段重点 | 论文关系与研究脉络 |
| 后续核心重点 | 人机协同创新探索与实验设计 |
| 验收方式 | 完整流程加量化 RAG 评测 |

## 4. 设计原则

### 4.1 证据优先

论文结论必须关联可核验的论文版本、分片、页码和原文。模型通用知识必须标注为模型补充，不能伪装成知识库证据。

### 4.2 用户主导

AI 可以提出、质疑和整理候选假设，但假设的固定、状态推进、实验执行和研究结论必须由用户确认。

### 4.3 分阶段引入复杂度

每个组件必须解决已经出现的问题。Elasticsearch、Neo4j、Mem0 和 Deep Agents 不因“技术栈完整”而提前进入 MVP 核心链路。

### 4.4 可评测

切片、检索、重排、Prompt、模型和引用策略的效果必须通过固定评测集比较，而不是只凭演示效果判断。

### 4.5 可恢复和可追溯

耗时任务、对话、研究会话、笔记、假设和实验方案必须持久化、可重试、可回溯。

### 4.6 框架不侵入领域模型

业务层使用项目自己的实体和接口。LangChain、LangGraph、Celery、MinIO 等框架类型不能成为核心领域对象的公开协议。

## 5. 分阶段产品范围

### 5.1 第一阶段：论文知识库 MVP

MVP 必须完成：

- 手动上传和管理 PDF。
- 异步解析论文，保留章节、页码、图片和原文定位信息。
- PostgreSQL 全文检索与 pgvector 语义检索。
- RRF 融合、Reranker 重排。
- 基于论文证据和模型通用知识的多轮问答。
- 论文、页码、原文片段级引用。
- 模型通用知识显式标注。
- 按需生成和编辑结构化论文笔记。
- 解析任务进度、失败原因和重试。
- 真实行人重识别论文上的检索与回答评测。
- 模型调用、耗时、Token 和费用记录。

MVP 明确不包含：

- 扫描件 OCR 和公式高保真还原。
- Zotero 同步。
- 大规模学术网站爬取和论文订阅中心。
- Neo4j 知识图谱。
- Mem0 长期用户记忆。
- Deep Agents 多 Agent 深度探索。
- 多用户、计费、复杂权限和云端高可用。
- 自动训练模型或在宿主机执行实验代码。
- 自动生成整篇论文。

### 5.2 第二阶段：研究脉络与论文关系

实现：

- 多论文方法对比。
- 方法演进和创新继承关系。
- 数据集、指标、实验设置和局限性关系。
- 引用关系和概念关系。
- 研究主题视图和关系查询。

第一版关系模型先存入 PostgreSQL。只有多跳关系查询在真实研究流程中证明有价值后，才引入 Neo4j 适配器。

### 5.3 轻量相关论文推荐

论文发现是创新探索的辅助能力，不建设成独立文献管理平台。

只实现：

- 根据研究主题、选中论文或对话生成检索条件。
- 从有限的学术数据源查询候选论文。
- 按主题相关性、时间、引用关系和知识库互补度排序。
- 用户确认后导入知识库。

不实现：

- 通用大规模爬虫。
- 复杂订阅中心。
- 未经确认的自动下载。
- Zotero 双向同步。

### 5.4 人机协同创新探索与实验设计

建立持久化研究会话。用户与 AI 围绕研究问题持续讨论，逐步沉淀证据、观点、候选假设和实验卡片。

核心能力：

- 澄清研究问题和约束。
- 检索支持证据、反对证据和相近工作。
- 区分已知事实、用户观点、AI 推测和实验结果。
- 维护讨论分支和阶段快照。
- 将有价值的方向固定为候选假设。
- 评估假设的新颖性、合理性、可验证性、成本和风险。
- 共同设计基线、变量、数据集、指标、消融实验和失败判据。
- 录入实验结果并继续讨论、修正或放弃假设。

Deep Agents 在此阶段作为可插拔的受控深度探索执行器，而不是主研究会话本身。

### 5.5 科研写作辅助

写作能力建立在已确认的笔记、假设、实验结果和引用之上：

- 生成论文提纲。
- 组织证据材料。
- 生成章节草稿和修改建议。
- 检查术语、逻辑和引用一致性。

系统不得虚构实验结果、论文或引用。

## 6. MVP 验收基线

首版使用至少 30 篇真实论文和 50 个带标准证据的问题建立评测集。

验收目标：

- 支持范围内 PDF 的成功解析率不低于 95%。
- Recall@10 不低于 85%。
- 人工抽查的引用正确率不低于 90%。
- 模型通用知识标注合规率为 100%。
- 失败任务可查看原因并安全重试。
- 应用重启后论文、笔记、对话和任务状态仍存在。
- 所有模型调用可追踪模型版本、耗时、Token 和估算费用。

这些数值是 MVP 的初始发布门槛。评测集扩大后可以提高门槛，但不能通过删除困难样本降低标准。

## 7. 总体架构

系统采用“模块化单体加独立后台 Worker”架构。FastAPI API 与 Celery Worker 使用同一套 Python 领域代码，以不同进程运行。

~~~mermaid
flowchart LR
    U[用户] --> W[Next.js Web]
    W --> A[FastAPI API]
    A --> LG[LangGraph 工作流]
    LG --> LC[LangChain 模型与工具集成]

    A --> R[Redis]
    A --> P[(PostgreSQL + pgvector)]
    A --> M[(MinIO)]

    R --> C[Celery Worker]
    C --> D[Docling PDF 解析]
    C --> P
    C --> M
    C --> LC

    LC --> X[LLM / Embedding / Reranker API]

    A -. 链路与评测 .-> F[Langfuse]
    C -. 链路与评测 .-> F
~~~

### 7.1 Next.js Web

负责：

- 论文库与上传。
- 任务进度和失败状态。
- PDF 阅读和引用定位。
- 流式问答。
- 结构化笔记编辑。
- 研究会话、证据卡、假设卡和实验卡交互。

前端不直接访问数据库、MinIO 或模型服务。

### 7.2 FastAPI API

系统唯一业务入口，负责：

- 请求校验。
- 论文、笔记、对话和研究对象的 CRUD。
- SSE 流式协议。
- 任务创建、查询和重试。
- LangGraph 工作流启动和恢复。
- 对象存储临时访问地址。

### 7.3 Celery Worker

运行耗时、可重试任务：

- PDF 解析。
- 图片和结构化产物保存。
- 切片。
- Embedding。
- 全文和向量索引。
- 结构化笔记生成。
- 后续深度探索后台任务。

API 与 Worker 共用领域代码和数据库，不形成独立业务微服务。

### 7.4 Redis

用途：

- Celery Broker。
- 可选缓存任务实时进度，便于前端快速获取执行状态。
- 缓存活跃会话等可以从 PostgreSQL 重建的临时数据。

Redis 不是最终事实来源。任务阶段、失败原因和重试次数等正式状态持久化到 PostgreSQL。MVP 可以先直接查询 PostgreSQL，只有进度更新频率或查询延迟产生实际问题时，才增加 Redis 进度缓存。Redis 丢失后，业务状态仍可以从 PostgreSQL 恢复。

### 7.5 PostgreSQL 与 pgvector

作为唯一核心数据库，保存：

- 论文与解析版本。
- 分片、全文索引和向量。
- 任务状态。
- 笔记和修订。
- 对话、摘要和引用。
- 研究会话、证据、观点、假设和实验。
- 评测数据和模型使用量。

### 7.6 MinIO

保存：

- 原始 PDF。
- 解析图片。
- Docling 结构化 JSON。
- Markdown 派生视图。
- 可重新生成的其他大对象。

数据库保存对象键。临时签名 URL 只在访问时生成，不能永久写入 Markdown。

### 7.7 LangChain

LangChain 只作为 AI 集成层：

- 不同厂商 Chat Model 和 Embedding 的统一调用。
- 消息、工具和结构化输出。
- 模型回调、重试和 Fallback。
- 向 LangGraph 节点提供模型与工具组件。

PDF 解析、领域实体、SQL 检索和任务模型不使用 LangChain 类型作为公共接口。

### 7.8 LangGraph

LangGraph 是核心状态工作流运行时：

- 显式节点和条件边。
- 流式事件。
- Checkpoint。
- 暂停、恢复和人工确认。
- 后续研究会话的分支与回溯。

LangGraph 不承担 Celery 的 PDF 后台任务职责。

### 7.9 Deep Agents

Deep Agents 不进入 MVP。创新探索阶段，用户主动发起“深度探索”时才使用。

约束：

- 最大步骤数、并行子 Agent 数、Token、费用和超时。
- 只使用白名单科研工具。
- 默认无宿主机 Shell。
- 默认无任意文件读写能力。
- 输出是证据卡、反证卡和待讨论问题，不直接成为研究结论。

### 7.10 Langfuse

记录：

- 检索、重排和模型调用链。
- Prompt 与模型版本。
- 耗时、Token 和费用。
- 评测数据集运行和分数。

完整论文片段和 Prompt 是否外发必须可配置。默认裁剪或脱敏敏感内容。

## 8. 后端模块边界

建议目录按业务领域划分：

~~~text
papers        论文元数据、文件与版本
ingestion     解析、切片、索引流水线
retrieval     全文、向量、融合与重排
chat          普通知识库问答
notes         结构化笔记生成与编辑
research      研究会话、证据、观点、假设与实验
evaluation    数据集、检索指标与回答评测
providers     LLM、Embedding、Reranker、存储适配
tasks         Celery 任务入口与任务状态
shared        配置、数据库、错误和可观测性基础设施
~~~

边界约束：

- chat 可以调用 retrieval 的应用服务，但不能直接拼写 pgvector SQL。
- research 可以复用 retrieval 和 chat，但不能修改其内部存储。
- providers 只提供外部系统适配，不包含业务判断。
- tasks 只负责调度应用服务，不复制领域逻辑。
- API 路由只做协议转换，不直接实现检索或解析流程。

## 9. 论文入库、解析与索引

### 9.1 总流程

~~~mermaid
flowchart LR
    A[上传 PDF] --> B[校验与 SHA-256]
    B --> C[原文件写入 MinIO]
    C --> D[创建文档版本与任务]
    D --> E[Celery Worker]
    E --> F[Docling 解析]
    F --> G[保存 JSON、Markdown 和图片]
    G --> H[结构化切片]
    H --> I[生成 Embedding]
    I --> J[建立全文与向量索引]
    J --> K[质量检查]
    K --> L[文档 READY]
~~~

### 9.2 上传与去重

FastAPI：

- 校验文件签名、MIME、大小和页数限制。
- 流式计算 SHA-256，不一次性把 PDF 读入内存。
- 使用哈希识别重复文件。
- 原始 PDF 先写入 MinIO，再创建数据库处理任务。
- 同一 PDF 默认提示已存在；用户主动要求时可以创建新解析版本。

对象键：

~~~text
papers/{document_id}/source/{version_id}.pdf
papers/{document_id}/parsed/{version_id}/document.json
papers/{document_id}/parsed/{version_id}/document.md
papers/{document_id}/images/{version_id}/{image_id}.png
~~~

### 9.3 版本与任务

对象关系：

~~~text
document
  └─ document_version
       ├─ source_object_key
       ├─ parser_name / parser_version
       ├─ pipeline_version
       └─ document_task
~~~

任务状态：

~~~text
QUEUED
→ VALIDATING
→ PARSING
→ NORMALIZING
→ CHUNKING
→ EMBEDDING
→ INDEXING
→ VERIFYING
→ READY
~~~

任意阶段可进入 FAILED 或 CANCELLED。任务记录当前阶段、进度、重试次数、错误码和可读错误信息。

重新切片、替换 Embedding 模型或升级解析器时创建新版本。旧版本保留到新版本成功，避免升级失败导致论文不可用。

### 9.4 Docling 解析

Docling JSON 是规范解析产物，Markdown 是阅读和调试视图。

解析元素尽可能保留：

- 类型。
- 章节层级。
- 原始文本。
- 页码和页面坐标。
- 表格、图片和图注关系。
- 原文顺序。

解析质量报告包含：

- PDF 总页数和成功解析页数。
- 正文字符数。
- 空白或异常页面。
- 章节、表格和图片数量。
- 乱码或文本覆盖率警告。

质量异常的文档进入需要检查状态，不能静默成为可检索文档。

### 9.5 结构化切片

切片优先遵循标题、段落、列表和表格边界。

初始参数：

- 目标长度 600 至 800 Token。
- 重叠 80 至 120 Token。
- 标题路径加入上下文。
- 图注和表格保留所属章节。
- 参考文献区域单独标记并默认降低召回权重。

参数由评测结果调整，不作为永久常量。

每个分片保存：

- 文档和文档版本。
- 章节路径。
- 内容与内容类型。
- 起止页码。
- 来源元素 ID。
- Token 数。
- 分片序号。
- 流水线版本。

### 9.6 双语全文与向量索引

- Embedding 采用支持中英文的模型。
- 英文全文检索使用 PostgreSQL 英文配置。
- 中文文本在应用层分词，再写入 simple tsvector。
- 中文问题查询英文论文时生成英文查询扩展，同时保留原问题。
- 全文与向量召回同时执行。

Embedding 使用独立的 embedding space 元数据记录模型、维度和版本。不同维度使用独立分区或索引空间。切换模型时先构建影子索引，通过评测后原子切换，不能把不同空间的向量直接比较。

### 9.7 一致性

- 每个阶段幂等。
- 唯一约束防止重复分片和重复任务。
- 重试从最后成功阶段继续。
- 所有索引和质量检查成功后才进入 READY。
- 检索只读取 READY 的当前版本。
- 新版本成功后通过事务切换。
- 删除论文使用后台清理任务。

## 10. 混合检索、问答与引用

### 10.1 工作流

~~~mermaid
flowchart LR
    A[用户问题] --> B[理解问题与检索范围]
    B --> C[查询改写和双语扩展]
    C --> D1[全文召回]
    C --> D2[向量召回]
    D1 --> E[RRF 融合]
    D2 --> E
    E --> F[Reranker]
    F --> G[证据充分性判断]
    G -->|不足| H[澄清或说明不足]
    G -->|充分| I[结构化回答草稿]
    I --> J[引用校验]
    J --> K[流式返回并持久化]
~~~

### 10.2 检索范围

每次提问带有明确范围：

- 整个论文库。
- 指定论文。
- 用户选中的多篇论文。
- 指定标签或集合。

阅读单篇论文时默认优先当前论文，但界面允许切换全库。

### 10.3 查询计划

问题理解节点产生结构化查询计划：

~~~json
{
  "intent": "compare_methods",
  "original_query": "这些方法如何处理遮挡问题？",
  "english_queries": [
    "person re-identification occlusion handling",
    "occluded person re-identification methods"
  ],
  "keywords": ["遮挡", "occlusion", "person re-identification"],
  "filters": {
    "document_ids": [],
    "year_range": null
  }
}
~~~

模型只生成查询建议。应用层强制执行检索范围和过滤，不执行模型生成的任意 SQL。

### 10.4 召回、融合与重排

初始配置：

- 全文召回 30 至 50 个分片。
- 向量召回 30 至 50 个分片。
- RRF 合并和去重。
- 前 20 至 30 个候选进入 Reranker。
- 最终 8 至 12 个证据分片进入生成上下文。

候选分片记录全文排名、向量排名、RRF 分数和 Reranker 分数。所有参数可配置并通过评测调整。

### 10.5 证据充分性

生成回答前检查：

- 是否存在直接相关内容。
- 证据是否足以支持比较或因果结论。
- 多论文比较是否覆盖目标论文。
- 是否存在矛盾证据。
- 是否只召回标题、参考文献或缺少上下文的内容。

证据不足时：

1. 请求用户澄清；
2. 明确说明知识库证据不足；
3. 使用模型通用知识补充，但单独标注。

### 10.6 结构化回答

模型先返回结构化回答块：

~~~json
{
  "answer_blocks": [
    {
      "type": "paper_evidence",
      "text": "方法 A 通过可见区域匹配缓解遮挡问题。",
      "citations": [
        {
          "chunk_id": "chunk-123",
          "quote": "对应原文片段"
        }
      ]
    },
    {
      "type": "model_knowledge",
      "text": "从一般视觉建模角度看……",
      "citations": []
    }
  ]
}
~~~

引用校验器验证：

- 分片属于本次检索结果。
- 论文版本有效。
- 引用原文存在。
- 页码可由来源信息确定。
- 引用内容能够支持回答主张。

### 10.7 两阶段流式输出

不能直接流式展示尚未验证的回答。

第一阶段推送高层状态：

~~~text
正在分析问题
正在检索候选片段
正在重排证据
正在检查引用
~~~

第二阶段在引用校验完成后流式输出最终回答。

引用失败时删除无支持主张或重新生成一次。仍失败则返回证据不足。

### 10.8 多轮状态

- 完整消息保存到 PostgreSQL。
- LangGraph Checkpoint 保存工作流状态。
- Prompt 使用最近消息、持久化摘要和当前问题。
- 摘要区分用户输入和论文确认事实。
- Redis 只缓存活跃会话。
- MVP 不使用 Mem0。

## 11. 结构化论文笔记

系统提供结构化笔记模板和生成能力，是否生成由用户决定。论文上传后不会自动生成笔记；用户可以按需触发 AI 生成，也可以完全手工编写。

默认笔记模板：

- 基本信息和研究问题。
- 相关工作与论文定位。
- 核心方法和整体流程。
- 主要创新点。
- 数据集、指标和实验设置。
- 主要实验结果。
- 消融实验。
- 局限性和适用条件。
- 与其他论文的关系。
- 对当前研究的启发。
- 用户批注。

关键字段附带论文证据和状态：

~~~text
创新点：提出基于可见区域匹配的遮挡处理方法
证据：Section 3.1, Page 4
状态：AI 生成 / 用户已确认
~~~

笔记保存为结构化数据并渲染为 Markdown。用户编辑后，重新生成创建新草稿并展示差异，不能覆盖用户内容。

## 12. 人机协同研究 Agent

### 12.1 研究对象

~~~text
research_session
├─ research_message
├─ evidence_card
├─ research_claim
├─ hypothesis
├─ experiment_plan
├─ experiment_result
├─ session_snapshot
└─ session_branch
~~~

语义：

- evidence_card：来自论文的不可随意修改的证据。
- research_claim：用户或 AI 提出的观点，可以尚未获得支持。
- hypothesis：用户固定的候选研究假设。
- experiment_plan：验证假设的实验方案。
- session_snapshot：某一时刻的研究状态。
- session_branch：从历史节点派生的另一条探索路线。

### 12.2 协同工作流

~~~mermaid
flowchart TD
    A[用户提出问题或想法] --> B[AI 澄清目标和约束]
    B --> C[检索论文与研究脉络]
    C --> D[展示支持证据、反证和相近工作]
    D --> E[用户补充判断或修改方向]
    E --> F{是否形成候选假设}
    F -->|否| B
    F -->|是| G[用户固定候选假设]
    G --> H[AI 质疑新颖性与可验证性]
    H --> I{是否值得实验}
    I -->|修改| E
    I -->|是| J[共同生成实验卡]
    J --> K[用户执行实验并录入结果]
    K --> L[共同分析结果]
    L --> M{是否支持假设}
    M -->|否| E
    M -->|部分| H
    M -->|支持| N[阶段性研究结论]
~~~

用户可以随时返回旧快照、建立分支或放弃假设。

### 12.3 AI 职责

AI 可以：

- 澄清研究问题。
- 检索相关论文。
- 比较方法前提和局限。
- 寻找反面证据。
- 指出证据不足的推断。
- 提出替代解释。
- 将宽泛想法改写为可证伪假设。
- 建议最小成本实验。
- 总结共识、争议和待验证问题。

AI 不得：

- 把用户猜想写成论文事实。
- 在未查证时声称方向从未被研究。
- 虚构论文、结果或引用。
- 未经用户确认推进假设状态。

### 12.4 假设状态

~~~text
IDEA
→ EVIDENCE_GATHERING
→ CANDIDATE
→ UNDER_CRITIQUE
→ EXPERIMENT_READY
→ TESTING
→ SUPPORTED / PARTIALLY_SUPPORTED / REJECTED
~~~

状态变化由用户触发，AI 只能建议。

### 12.5 假设卡

包含：

- 研究问题。
- 候选假设。
- 产生背景。
- 支持和反对证据。
- 与已有工作的差异。
- 潜在新颖性。
- 关键条件。
- 可证伪方式。
- 预期贡献。
- 主要风险。
- 当前状态。

### 12.6 实验卡

包含：

- 假设。
- 基线。
- 自变量、因变量和控制变量。
- 数据集与划分。
- 指标。
- 训练和推理配置。
- 主实验和消融实验。
- 预期现象。
- 失败判据。
- 混杂因素。
- 资源和时间估计。
- 设计依据。

第一版只负责设计、记录和分析实验。自动执行需要独立沙箱、资源限额和人工审批，属于后续能力。

### 12.7 Deep Agents 受控探索

用户主动发起深度探索后，主 Agent 可以协调：

- 文献证据检索子 Agent。
- 方法对比子 Agent。
- 反例与质疑子 Agent。
- 实验设计子 Agent。

子 Agent 隔离中间上下文，最终只向主会话返回结构化材料。所有材料继续由用户和主 Agent 讨论，不能自动成为结论。

### 12.8 防止研究会话失真

- 论文证据、用户观点、AI 推测和实验结果使用不同类型。
- 摘要引用对象 ID，不把无支持观点压缩为事实。
- 文档版本失效时，证据卡提示重新核验。
- 假设保留完整演化历史。
- 恢复会话时展示共识、争议、候选假设和下一步。

## 13. 核心数据模型

| 领域 | 核心表 | 用途 |
|---|---|---|
| 论文 | document、document_version、document_asset、document_task | 论文、解析版本、资源和任务 |
| 检索 | document_chunk、embedding_space、chunk_embedding | 分片、全文索引和向量空间 |
| 笔记 | paper_note、note_revision、note_evidence | 笔记、历史和证据 |
| 对话 | chat_session、chat_message、message_citation、chat_summary | 问答、引用和摘要 |
| 研究 | research_session、research_branch、research_message、research_snapshot | 研究讨论与分支 |
| 推理产物 | evidence_card、research_claim、hypothesis、hypothesis_evidence | 证据、观点和假设 |
| 实验 | experiment_plan、experiment_result | 实验方案和结果 |
| 评测 | evaluation_dataset、evaluation_case、evaluation_run、evaluation_score | RAG 评测 |
| 成本 | model_usage | 模型、Token、耗时和费用 |
| 可靠性 | outbox_event | 事务后任务投递 |

约束：

- 主键统一使用 UUID。
- 重要对象带创建时间、更新时间和版本号。
- JSONB 只保存可扩展元数据。
- 用户编辑内容保留修订历史。
- 引用绑定具体文档版本和分片。
- 旧引用不自动迁移到新解析版本。
- Embedding 通过 embedding space 隔离模型和维度。

## 14. API 设计

统一前缀为 /api/v1。

### 14.1 论文与任务

~~~text
POST   /documents
GET    /documents
GET    /documents/{id}
DELETE /documents/{id}

GET    /documents/{id}/versions
POST   /documents/{id}/reprocess
GET    /tasks/{task_id}
POST   /tasks/{task_id}/retry
GET    /tasks/{task_id}/events
~~~

### 14.2 笔记

~~~text
POST   /documents/{id}/notes/generate
GET    /documents/{id}/notes
PUT    /notes/{note_id}
GET    /notes/{note_id}/revisions
POST   /notes/{note_id}/regenerate
~~~

### 14.3 普通问答

~~~text
POST   /chat/sessions
GET    /chat/sessions/{id}
POST   /chat/sessions/{id}/messages
GET    /chat/runs/{run_id}/stream
~~~

POST messages 创建运行并直接返回兼容 Vercel AI SDK 的 SSE；run stream 用于断线重连。

### 14.4 研究会话

~~~text
POST   /research/sessions
GET    /research/sessions/{id}
POST   /research/sessions/{id}/messages
POST   /research/sessions/{id}/branches
POST   /research/sessions/{id}/snapshots

POST   /research/sessions/{id}/evidence
POST   /research/sessions/{id}/claims
POST   /research/sessions/{id}/hypotheses
POST   /hypotheses/{id}/transition

POST   /hypotheses/{id}/experiment-plans
POST   /experiment-plans/{id}/results
POST   /research/sessions/{id}/deep-explorations
~~~

### 14.5 评测

~~~text
POST   /evaluations/datasets
POST   /evaluations/runs
GET    /evaluations/runs/{id}
GET    /evaluations/runs/{id}/scores
~~~

### 14.6 SSE 事件

~~~text
workflow.started
workflow.stage
retrieval.completed
answer.started
answer.delta
citation.available
workflow.warning
workflow.failed
workflow.completed
~~~

事件包含 event_id。客户端通过 Last-Event-ID 重连。SSE 不暴露模型隐藏推理过程。

## 15. 错误处理与可靠性

### 15.1 错误格式

~~~json
{
  "code": "DOCUMENT_PARSE_FAILED",
  "message": "论文第 7 页无法解析",
  "retryable": true,
  "trace_id": "trace-uuid",
  "details": {
    "document_id": "uuid",
    "stage": "PARSING"
  }
}
~~~

错误类别：

- 用户输入错误。
- 文件或解析错误。
- 临时基础设施错误。
- 模型限流、超时或余额不足。
- 数据版本冲突。
- 检索无结果。
- 引用校验失败。
- 内部程序错误。

只有临时错误自动重试，并采用指数退避和随机抖动。永久错误不能无限重试。

### 15.2 幂等与一致性

- 上传支持 Idempotency-Key。
- 任务以文档版本、流水线版本和阶段作为幂等边界。
- Outbox 保证数据库提交后最终能够投递任务。
- 模型调用重试前检查结果是否已经落库。
- 假设状态迁移使用状态机和乐观锁。
- 删除论文先进入 DELETING，清理成功后进入 DELETED。
- Redis 中的状态始终可以由 PostgreSQL 重建。

## 16. 安全与隐私

### 16.1 本地部署

- 服务默认绑定 127.0.0.1。
- FastAPI 限制允许的 Origin。
- 可配置本地访问令牌。
- MinIO Bucket 私有。
- API Key 只存后端环境变量或本机密钥存储。
- 前端不能获得数据库、MinIO 或模型密钥。
- 日志不记录密钥、完整 PDF 或默认记录完整敏感 Prompt。

### 16.2 PDF 安全

- 校验文件头、扩展名、大小和页数。
- 解析 Worker 使用非 root 用户。
- 设置 CPU、内存和超时限制。
- 解析容器默认禁止不必要公网访问。
- 文件名不能用于构造真实存储路径。

### 16.3 Prompt Injection

- 论文文本始终作为不可信数据。
- 论文中的指令不能改变系统指令和工具权限。
- 检索内容使用明确的数据边界。
- 模型不能执行任意 SQL。
- Deep Agent 只使用白名单工具。

### 16.4 外部模型数据边界

- 首次配置时提示数据外发范围。
- LLM、Embedding 和 Reranker 分别配置。
- 只发送完成任务所需的最小文本。
- Langfuse 默认不记录完整论文片段。
- 保存模型和参数，支持结果复现。

## 17. 测试策略

### 17.1 单元测试

覆盖：

- 解析结果标准化。
- Chunk 边界和页码映射。
- RRF 融合。
- 查询过滤。
- 引用原文匹配。
- 假设状态迁移。
- 模型费用计算。
- Provider 错误和重试判断。

### 17.2 集成测试

使用真实依赖：

- PostgreSQL 与 pgvector。
- Redis 与 Celery。
- MinIO。
- 固定测试 PDF 上的 Docling。
- FastAPI 与 LangGraph Checkpoint。
- Next.js 与 FastAPI SSE 协议。

### 17.3 端到端测试

核心路径：

~~~text
上传 PDF
→ 查看进度
→ 文档 READY
→ 提问
→ 获得页码引用
→ 点击引用
→ 生成笔记
→ 编辑笔记
→ 重启后数据仍存在
~~~

自动化测试默认使用 Fake Provider。真实模型测试作为手动或定时评测，避免普通测试不稳定和产生费用。

## 18. RAG 评测

评测集包含：

- 至少 30 篇真实行人重识别论文。
- 至少 50 个问题。
- 相关论文、标准证据分片和页码。
- 问题类型、难度和语言标签。

覆盖：

- 单篇论文事实。
- 方法总结。
- 多论文比较。
- 实验结果。
- 局限性。
- 中问英检。
- 无答案问题。
- 容易产生错误引用的问题。

检索指标：

- Recall@5。
- Recall@10。
- MRR。
- Reranker 前后排序变化。
- 跨语言召回差异。

回答指标：

- 引用正确率。
- 论文忠实度。
- 相关性。
- 证据覆盖率。
- 模型知识标注合规率。
- 延迟、Token 和费用。

LLM Judge 只辅助人工评测。其结果必须与人工样本检查一致性。

## 19. 部署

### 19.1 基础 Docker Compose

~~~text
web          Next.js
api          FastAPI
worker       Celery Worker
postgres     PostgreSQL + pgvector
redis        Redis
minio        MinIO
~~~

Langfuse 不进入基础 Compose。可以连接云端，或通过独立 observability profile 使用官方自托管组件。

部署约束：

- 持久化服务使用命名 Volume。
- 所有服务带 Health Check。
- 数据库迁移在 API 接收请求前完成。
- API 与 Worker 使用相同后端镜像。
- 密钥由 .env 注入，仓库只保留 .env.example。
- 提供 PostgreSQL、MinIO 和配置的备份恢复说明。
- Python 和前端依赖使用锁文件固定。

### 19.2 开发工具建议

- Python 依赖与锁定：uv。
- Python 格式和静态检查：Ruff、Pyright。
- Python 测试：pytest。
- 数据库迁移：Alembic。
- 前端包管理：pnpm。
- 前端单元测试：Vitest。
- 浏览器端到端测试：Playwright。

具体版本在实施开始时选择相互兼容的稳定版本，并提交锁文件，不在设计文档中绑定未经验证的版本组合。

## 20. 可观测性

请求和后台任务共享 trace_id：

~~~text
Next.js
→ FastAPI
→ Celery
→ 检索
→ Reranker
→ LLM
→ Langfuse
~~~

记录：

- API 状态和耗时。
- 任务各阶段耗时。
- PDF 页数和分片数量。
- 各路召回、RRF 和重排结果。
- 模型和 Prompt 版本。
- Token 和费用。
- 引用校验失败原因。
- Celery 重试和最终失败。
- 评测运行和指标变化。

默认使用结构化 JSON 日志。MVP 不引入完整 ELK、Prometheus 和 Grafana。长期运行出现监控需求后再接入 OpenTelemetry、Prometheus 和 Grafana。

## 21. 高层开发里程碑

### 21.1 MVP

1. 工程基础：项目结构、Compose、迁移、配置、Provider 接口和 CI。
2. 论文入库：上传、MinIO、Docling、任务状态和前端进度。
3. 切片与检索：Embedding、全文、向量、RRF、Reranker 和检索评测。
4. 问答与引用：LangChain、LangGraph、双语查询、引用校验和 SSE。
5. 论文笔记：按需生成、字段证据、编辑、修订和差异。
6. 质量收口：完整评测集、安全、备份、日志和端到端测试。

第六项通过验收后，MVP 才完成。

### 21.2 MVP 后

1. 论文比较与研究脉络。
2. 持久化人机协同研究会话。
3. 轻量相关论文推荐。
4. 假设卡与实验卡。
5. Deep Agents 受控深度探索。
6. 实验结果分析。
7. 科研写作辅助。

## 22. 主要风险与应对

| 风险 | 应对 |
|---|---|
| 双栏 PDF 解析错误 | 固定真实论文基准集、质量报告、版本化重解析 |
| 中英文关键词检索不稳定 | 多语言向量、应用层中文分词、英文查询扩展 |
| 检索命中但证据不支持回答 | Reranker、充分性检查、结构化引用校验 |
| 模型把通用知识当论文结论 | 回答块类型分离、前端显式标签、自动合规检查 |
| 框架过度耦合 | 领域接口隔离 LangChain、LangGraph 和存储实现 |
| 基础设施过重 | MVP 只保留 PostgreSQL、Redis、MinIO 等必要组件 |
| Agent 自主性导致研究失真 | 用户控制状态、证据类型隔离、深度探索有预算和权限边界 |
| 在线模型成本不可控 | 模型路由、缓存、批量 Embedding、预算和使用量记录 |
| 解析或索引中途失败 | 幂等阶段、持久化任务状态、Outbox 和安全重试 |
| 文档更新导致引用失效 | 引用绑定版本，旧证据失效提示和重新核验 |

## 23. 技术选型依据

- [Docling Document](https://docling-project.github.io/docling/concepts/docling_document/)：结构、页面坐标和来源信息。
- [Docling DocumentConverter](https://docling-project.github.io/docling/reference/document_converter/)：本地 PDF 转换和结构化输出。
- [pgvector](https://github.com/pgvector/pgvector)：PostgreSQL 向量检索及与全文检索组合。
- [LangChain、LangGraph 与 Deep Agents 定位](https://docs.langchain.com/oss/python/concepts/products/)。
- [LangGraph Overview](https://docs.langchain.com/oss/python/langgraph/overview/)：有状态工作流、流式、持久化和人工介入。
- [Deep Agents Overview](https://docs.langchain.com/oss/python/deepagents/overview/)：规划、上下文管理和子 Agent。
- [Vercel AI SDK Stream Protocol](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol)：与 Python 自定义后端的 SSE 协议。
- [Langfuse Evaluation Scores](https://langfuse.com/docs/evaluation/scores/overview)：评测分数、实验和链路关联。
- [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)：重计算任务应使用独立任务队列。

## 24. 设计结论

ResearchAgent 的 MVP 是一个证据可靠、可量化评测的个人论文知识库，而不是一次性实现所有科研 Agent 能力。

模块化单体、独立 Worker、PostgreSQL 加 pgvector、MinIO、Redis、LangChain、LangGraph 和可替换模型 Provider 构成首版核心。Elasticsearch、Neo4j、Mem0 和 Deep Agents 按真实需求在后续阶段引入。

项目的长期差异化价值来自持久化的人机协同研究过程：用户与 AI 围绕论文证据不断讨论、质疑、形成候选假设、设计实验并分析结果。系统的职责是提高研究信息组织和决策效率，而不是替代用户做不可核验的创新判断。
