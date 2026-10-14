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
