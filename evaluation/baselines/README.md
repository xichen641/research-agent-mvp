# ReID 当前体验基线与复测协议

T0.4 使用 [T0.3 固定任务集](../datasets/reid_research_tasks.jsonl)执行真实应用请求。
采集器默认逐题串行执行，每个单轮任务新建专用会话，并只关联该题的固定论文。
连续任务只在自己的会话中接续。运行报告、问题、回答、引用和事件保存在 `.tmp/`，
不提交 PDF、凭据或模型响应到远端。本目录保存操作方法，不放实际模型运行日志。

## 启动与运行

在仓库根目录更新 API、Worker 和迁移镜像并启动服务：

```powershell
docker compose -p research-agent-mvp -f infra/compose.yaml build api worker migrate
docker compose -p research-agent-mvp -f infra/compose.yaml up -d
```

先通过已验收的导入工具确认 6 篇固定材料处理完成。上传按 SHA-256 去重，不会
因复测再创建重复论文；失败或质量不可用的材料应先处理，基线不会凭标题猜测版本。

```powershell
.\scripts\import-reid-corpus.ps1 -WaitSeconds 600 -OutputPath .tmp/reid-baseline-import.json
.\scripts\run-reid-baseline.ps1 -Docker -ImportReport .tmp/reid-baseline-import.json
```

第二条命令启动一次性 Docker 采集器，通过 Docker 内网调用真实 API/Worker，
不载入 `.env`。题库、清单、PDF 与导入回执只读挂载；报告父目录可写。API 与
Worker 源码指纹不一致时拒绝运行。默认输出为新的 `.tmp/reid-baselines/时间-随机ID/`。
需要指定结果目录时用 `-OutputDirectory`，已有目录会受保护，不覆盖或重复提交请求。

开发时可只执行一题，明确记录样本范围：

```powershell
.\scripts\run-reid-baseline.ps1 -Docker -ImportReport .tmp/reid-baseline-import.json -TaskId REID-M01
```

不传 `-TaskId` 才执行全套 20 个任务。`-RetrievalMode` 默认 `auto`，可明确选择
`on`/`off` 做独立试验，不能把不同模式的结果混成同配置基线。单题试运行不代替
全套基线。宿主机依赖已就绪时也可不传 `-Docker`，或用 `-Python` 指定环境；
`-ApiUrl` 指定该模式的 API 地址。Docker 模式默认使用 `research-agent-mvp-api`
镜像和 `research-agent-mvp` 项目的内网，可通过 `-Image` / `-ProjectName` 修改。

采集脚本退出码 0 表示记录流程结束，报告中仍可能有执行失败或能力缺失；不表示
全部科研题通过。参数、文件或无法继续的 API 错误返回 2，已保存记录继续保留。

## 测量定义

计时起点是提交该回合消息之前的单调时钟，创建会话和导入材料不计入回合等待。
客户端采用“POST 取得运行 ID，再 GET SSE”的固定协议，包含提交、排队和传输。
它测量接口的接收体验，不测浏览器布局/绘制，也不测模型原生流式首 Token。

| 字段 | 判定依据 |
| --- | --- |
| `first_progress_ms` | 首个收到的已完成 `data-workflow` 事件；接受回执、`start`、心跳及失败标记不算有效进度 |
| `first_body_ms` | 收到非空 `text-delta`，且运行最终成功并取得该运行的持久 assistant 消息；失败兜底文字不算答案正文 |
| `completion_ms` | 成功回复的 SSE `[DONE]` 接收时间；失败不写成成功完成耗时 |
| `terminal_ms` | 终态或本地观察结束时间，包括失败；与成功完成时间分开 |
| `tool_calls` | `retrieval.retrieve` 的实际服务调用起止事件；属于工作流服务动作，不是模型原生工具调用 |
| `diagnostics.model_calls` | 每次已观测 LLM HTTP 尝试的操作、模型名称、耗时、状态、Token 与请求指纹，含重试、引用修复和记忆压缩 |
| `runtime_configuration` | Worker 本次运行采用的白名单配置；必须与运行前 API 配置一致 |

当前正文经引用核对后统一返回，因此正文接收常接近完成时间。规划/检索进度不能
替代首段正文。`rerank_completed` 是现有工作流标记，不能额外计成另一次独立工具
调用或精确的重排耗时；检索服务内部 embedding/reranking 分段用量尚未暴露。

模型记录同时保留配置名称和供应商响应中的 `model`。独立权重修订号未暴露时
保持缺失，不能把别名当作不可变版本。Token 只累加已记录的 LLM 请求，不凭字符
数补估；已观测调用缺任一用量字段时，对应总量留空，并保存缺失调用数。费用与
带币种账单目前未收集，保持 `null`，不猜测费率或把缺失写成免费。

## 场景执行与缺失能力

单轮任务只发送 `prompt`。R01 按 T01、T02、固定追问循环、T03 执行；追问每次
完整循环依次讨论竞争解释、最小对照、混淆因素和下一步/停止条件，最多 10 次。
只有从持久化 `context_assembled.memory_compacted=true` 观察到两次真实压缩，
才发送最终 T03。不能以模型口述或对话轮数代替压缩证据，也不改变上下文预算来
强行触发预期结果。

R01 目前可以用单一持久会话执行，其逻辑空间标签只用于本题定位；这不代表已经
实现研究空间。R02–R04 需要空间归属、跨会话研究状态及可更正/撤回的研究记录，
当前不支持时记录 `capability_missing`，不模拟保存或撤销成功。未来补齐相应能力
后，应同步扩展执行器的空间/状态适配，再复测这些场景，不能只翻转能力标记。

单回合默认观察上限 600 秒，连续场景默认预算 1800 秒，可用
`-TurnTimeoutSeconds` / `-ConversationTimeoutSeconds` 调整。预算在循环边界
检查，已经开始的回合仍使用单回合上限。超时记录本地观察失败与最后服务状态，
不声称已经取消后台任务。POST 不自动重试：响应丢失后保留“提交结果未知”，
先用运行/会话 ID 核对，不能直接再次提交假定前一次没有执行。

## 报告与暂定门槛

每次输出包括：

- `report.json`：独立运行 ID、UTC 时间、题库/清单哈希、每题指纹、真实文档/版本
  与解析身份、模型与配置、镜像/源码身份、逐题结果、测量及缺失值。
- `report.md`：测量汇总与各题状态，不表示人工评分通过。
- `REID-任务ID/回合ID.json`：原始 SSE 接收记录、服务诊断、运行/持久消息 ID、
  完整回答与引用。R01 的补充回合编号为 `C01-1` 等，不冒充固定 T03。
- `tasks[].thresholds`：按成功回合最大观测值×1.25、向上取整到秒生成的暂定
  复测门槛；保存样本数，未测指标留空。此 25% 是初始容差政策，不是统计置信度。

全局时延统计只覆盖成功回合，连续场景可能只完成其中部分回合；结合逐题状态
和缺测原因读取，不能由一个样本或全局缺失计数推断全套通过。暂定门槛不是正式
SLA 或稳定 p95。R02–R04 的缺测门槛不会被填成 0。

保存的运行可单独生成警告核对报告，不调用模型或改变原始测量。在后端开发
环境执行（路径换成实际输出目录）：

```powershell
python -m research_agent.evaluation.baseline_report --report ../.tmp/reid-baselines/运行目录/report.json
```

输出 `measurement-review.md`，标明原始报告哈希、回复类别、服务警告、失败原因、
模型调用数和压缩次数。保留“完成请求”与“答好科研问题”的区别：证据不足或
引用核对失败的回复可能很快，不能把其正文时间作为后续完整论文回答的同类目标；
无服务警告也不自动证明事实正确。重新生成时指定新的 `--output` 文件。

也可直接用 Docker 核对已有报告（目录和输出文件名换成实际值；不会调用模型）：

```powershell
$baselinePath = (Resolve-Path .tmp/reid-baselines/运行目录).Path
docker run --rm --network none --mount "type=bind,source=$baselinePath,target=/reports" --entrypoint /app/.venv/bin/python research-agent-mvp-api -m research_agent.evaluation.baseline_report --report /reports/report.json --output /reports/measurement-review-new.md
```

## 复测与本地验收

1. 确认材料 SHA-256、文档/版本与解析身份仍匹配清单；执行固定题库原文校验。
2. 保持题库/问题指纹、检索模式、模型、证据上限和记忆预算一致。记录改动代码
   版本、源码/镜像身份、运行日期及采集环境；底层模型别名可能变化，不能忽略。
3. 使用新目录重新执行，逐题比较同类回复的进度、正文和完成耗时及 Token。
   回复类别变化、参数/模型变化和缺失值单列，不能将质量改进产生的完整回答与
   原来的澄清回复混成直接时延退化。样本不足时多次复测后再收紧门槛。
4. 核对报告中的真实 run/session/message ID 和持久回复；警告及失败原因照实保存。
   原始评分参考、`fact_checks`、原文片段和评分要求不注入模型输入。
5. 按 [人工评审标准](../rubrics/reid_research_review.md)另行评答案质量，留存来源
   和理由。时延采集完成、HTTP 成功、引用形式核对与科研任务质量是不同证据层。

测量接口为 `GET /api/v1/chat/evaluation/config` 与
`GET /api/v1/chat/runs/{run_id}/diagnostics`，采用白名单，不返回凭据、提示、模型
回答、对象存储键或供应商原始错误。实际回答通过已有会话历史与 SSE 保存到本地
报告。旧运行没有新增测量事件时保留缺失，不补写成新版本实测。
