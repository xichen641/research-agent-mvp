# 科研任务验收材料

## T0.1 固定 ReID 语料

`corpora/reid-v1.json` 是首批 6 篇论文的固定清单。每篇包含稳定论文 ID、
本地文件名、SHA-256、字节数、PDF 页数、正文标题和作者、材料版本、来源定位及
元数据核对说明。后续任务与运行报告可以引用 `corpus_id`、`corpus_version`、
`paper_id` 和 `sha256`；SHA-256 固定本地材料字节，不能用文件名或最新下载地址替代。

| 论文 ID | 材料版本 | PDF 页数 |
| --- | --- | ---: |
| `clip-reid` | arXiv:2211.13977v4，2023-01-01 | 11 |
| `promptsg` | CVPR 2024 CVF Open Access，印刷页 17343-17353 | 11 |
| `mp-reid` | arXiv:2312.16797v1，2023-12-28 | 9 |
| `clip-scgi` | arXiv:2410.09382v1，2024-10-12 | 11 |
| `idea` | arXiv:2503.10324v1，2025-03-13 | 19 |
| `next` | arXiv:2505.20001v4，2025-08-10 | 21 |
| 合计 | 6 篇 | 82 |

标题、作者及 arXiv 版本来自各自 PDF 首页正文和水印，并已核对首页渲染。
PromptSG 的出版信息另经 [CVF 官方目录](https://openaccess.thecvf.com/content/CVPR2024/html/Yang_A_Pedestrian_is_Worth_One_Prompt_Towards_Language_Guidance_Person_CVPR_2024_paper.html)
确认。其余论文的出版信息未独立核实，保存为 `not_independently_verified`。
MP-ReID 的预印本日期为 2023 年，首页版权标记为 2024 年；NEXT 的预印本日期为
2025 年，首页版权标记为 2026 年。版权标记单独保存，不作为出版年份结论。

`source.url` 是从版本水印或官方目录得到的来源定位，未验证远端 PDF 与本地文件
字节一致。历史实际下载地址无法确认，`original_download_url` 保持 `null`。
PDF 页数包含本地文件中的所有页；后续证据页码应使用从 1 开始的 PDF 物理页码，
印刷页码作为补充信息。

### 本地端到端验收

在仓库根目录使用已安装后端依赖的 Python 执行：

```powershell
.\scripts\check-reid-corpus.ps1 -OutputPath .tmp/reid-corpus-check.json
```

脚本默认使用当前 `python`。需要指定后端虚拟环境时：

```powershell
.\scripts\check-reid-corpus.ps1 -Python .\backend\.venv\Scripts\python.exe
```

脚本复用后端已声明的 `pydantic` 和 `pypdfium2`，不需要模型、数据库或 API 服务。
也可在后端开发环境直接执行 `python -m research_agent.evaluation.corpus`。
默认路径根据源码位置确定，校验不依赖当前工作目录。

验收步骤：

1. 检查清单中的标题、作者、版本与本地 PDF 首页一致。
2. 执行校验，预期 `status=passed`、`matched_papers=6`、`matched_pages=82`，
   `unlisted_files=[]`，每篇状态为 `matched`，退出码为 0。
3. 重复执行，结果保持一致，PDF 内容与清单不被修改。
4. 如需验证失败路径，复制材料到临时目录并用 `-Root` 指定副本的根目录，保留
   `datasets/Reid` 相对层级。在副本中移走文件、替换内容或增加 PDF，预期报告
   `missing`、`changed` / `unreadable` 或 `unlisted_files`，退出码为 1。
   无效清单或读写报告错误退出码为 2。无需修改原材料。

校验只核对文件完整性和页数，不代表摄取、章节/图表解析或真实模型评测通过。
T0.2 负责导入与解析质量，T0.3/T0.4 负责科研任务集和模型体验基线。

### 材料更新规则

校验发现文件变化时报告差异，不自动接受新哈希，也不修改论文。
确认需要换版后，创建新的 `corpus_version` 和清单文件，重新核对正文元数据；
保留旧清单供历史评测追溯。PDF 保持在已忽略的 `datasets/` 中，运行报告建议
保存到 `.tmp/`。源码只提交元数据、工具与测试。

## T0.2 本地批量导入与质量报告

API、Worker 和基础设施启动后，在仓库根目录执行：

```powershell
.\scripts\import-reid-corpus.ps1 -WaitSeconds 600
```

使用已安装后端依赖的 Python；也可通过 `-Python` 显式指定虚拟环境。
`-ApiUrl` 默认为 `http://127.0.0.1:8000`，自定义端口时传入对应 API 地址。
脚本读取本地文件并通过已有 `POST /api/v1/documents` 上传，复用 SHA-256 去重、
事务 Outbox 和 Worker 摄取。服务端未新增读取宿主机路径的接口。

默认报告保存在 `.tmp/reid-import-report.json`。每次执行生成独立 `run_id`；
需要保留多次运行时指定不同输出文件：

```powershell
.\scripts\import-reid-corpus.ps1 -WaitSeconds 600 -OutputPath .tmp/reid-import-first.json
.\scripts\import-reid-corpus.ps1 -WaitSeconds 600 -OutputPath .tmp/reid-import-repeat.json
```

本地预检不符合清单的文件不会上传，其余文件继续处理；清单外 PDF 只列出，
不自动导入。上传前再次核对哈希，避免预检后的文件替换被当作固定版本接受。

报告为每篇保存以下两组结果，统计可重叠：

- `import_result`：`imported`（本次新导入）、`duplicate`（复用已有记录）、
  `not_imported` 或 `unknown`（未取得可信上传回执）。
- `state`：`ready`、`processing`、`failed`、`needs_review`、`local_error`
  或 `unknown`。`duplicate` 不代表论文已经可用，仍须检查处理状态。

报告包含文档/版本/任务 ID、任务阶段与进度、解析器版本、实际解析页数、
文本量、空页、章节/表格/图片数量及质量警告。质量来自摄取流程持久化的数据，
通过新增的 `GET /api/v1/documents/{document_id}/quality` 返回白名单字段；
不返回对象存储键或原文。旧资料尚无有效质量报告时保留 `pending` / `unavailable`。
任务失败码为 `PARSE_QUALITY_REVIEW` 时归为 `needs_review`。

默认 `-WaitSeconds 0`：上传并查询一次当前状态后返回；大于 0 时，每隔
`-PollInterval` 秒（默认 2 秒）刷新未完成项，等待窗口从上传结束后开始。
超过等待窗口时保留 `processing`，不将其写成摄取失败。轮询只读取状态，不重新上传。
报告随进度原子更新；Ctrl+C 只结束本地等待，已经接受的后台摄取仍继续。
重跑通过服务端去重恢复既有 ID；上传响应丢失或服务端 5xx 时标记结果未知，
不凭客户端异常推断服务端没有接受文件。
已有失败任务通过现有页面或任务重试接口处理；批量脚本再次执行会复用原记录并
刷新状态，不自动重试失败的摄取。

退出码：0 = 全部处理完成且质量可用；1 = 有失败、需检查、未知、缺失/变化或清单外文件；
2 = 参数、清单或报告读写错误；3 = 尚有处理中项；130 = 用户中断。

端到端验收：

1. 首次执行后，6 篇都有明确结果；页面可见新导入的论文，报告 ID 对应服务端资料与任务。
2. 再次执行，6 篇均为 `duplicate`，两份报告的文档、版本、任务 ID 完全一致；
   页面中的论文数量不增加。
3. 处理完成的文件报告 `ready` 并有质量信息；失败显示阶段与错误；低质量解析
   显示 `needs_review`。这些状态按实际结果验收，不要求把所有论文强行标为成功。
4. 在临时语料副本中移走或替换一个 PDF，该文件不上传，其他匹配文件继续处理。
5. 网络中断后重新执行可找回已有记录；本地停止等待后，已有后台任务仍可在页面查看。

本地自动化验证使用合成 PDF、隔离 PostgreSQL、内存对象存储和 Fake Embedding Provider；
覆盖真实 HTTP 路由、上传去重、Outbox、解析/切片/向量持久化与重复导入。
真实语料、真实模型和 Celery Worker 的端到端验收由上述操作单独完成。
