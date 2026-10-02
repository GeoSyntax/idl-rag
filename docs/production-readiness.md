# 资料生产就绪标准

这份标准回答一个容易被 Demo 掩盖的问题：**资料能被检索，不等于资料可以支撑生产研究结论。**

平台现在会在概览页和 `GET /api/dashboard/corpus-readiness` 显示同一套硬门槛。只要任一门槛不满足，状态就是 `blocked`，不会把知识库标成“生产可用”。

## 当前盘点（本地工作区，2026-10-02）

| 资料类别 | 当前情况 | 可以做什么 | 还不能声称什么 |
| --- | --- | --- | --- |
| ENVI/IDL 官方资料 | 415 份已索引文档 | 查找语法、API、处理流程 | 不能替代具体版本的本地 ENVI/IDL 编译验收 |
| IDL/ENVI 方法资料 | 18 份方法与代码资料 | 支撑常见读写、指数和处理样例 | 不能覆盖全部传感器、投影、质量控制与异常数据 |
| 遥感算法知识库 | 1,935 份文档、19,458 个 chunk：1,500 条 OpenAlex 候选记录、307 篇当前可访问的本地 OA PDF、50 条理论资料、74 个官方来源文件（57 份 PDF、14 份说明/工作流笔记及可复核 manifest；新增 USGS SSEBop、SMAP L3 Passive、SMAP Handbook、MOD15 LAI/FPAR 和四类 CEOS CARD4L PFS），5 份工具规范，以及本地论文解析资料 | 支撑产品约定、指数/温度/分类/变化检测、动态水体、火烧迹地、积雪覆盖、跨传感器 HLS、ARD/COG、Sentinel-2 产品基线、水体反射率、蒸散发/水分通量、土壤水分、LAI/FPAR 和 CARD4L 产品验收 | OpenAlex 候选记录主要是元数据/摘要；OA PDF 仍需逐条核对再分发许可 |
| 扫描版 IDL 实验资料 | 1 份文档，已用 `pdf-ocr-v1` 完成 OCR 和索引 | 可按 OCR 文本检索并引用 | OCR 仍需人工抽查公式、表格和代码，不能自动视为无误 |

当前本地工作区已经完成一次生产门禁：owner=1 的 5 个知识库共 2,370 份资料、22,120 个 chunk 全部为 `ready`，2,370 份资料均使用 `bge-m3 / 1024` 真实向量，fallback 为 0；知识库 5 在新增 CARD4L 官方规范后以正式评测服务登记了 43 条混合检索 Golden QA（报告 id=33），命中率 0.372、recall 0.372、MRR 0.329、Precision@6 0.186、答案相关性 0.686、faithfulness 0.417，平均延迟约 5.4 秒。其中 `remote_product` 与 `tooling` 题集仍保持完整命中；SMAP、MOD15 和 CARD4L 定向查询均能优先召回新增官方规范。这个结果说明本地资料链路可投入受控试用，但不等于所有研究问题都已覆盖，也不替代论文许可、真实数据运行和教师复核。

本次一致性审计还验证了 owner=1 的全部 2,370 条记录都能回溯到当前源文件，缺失源文件为 0；同时移除了 995 条历史失效路径记录、6 条重复 README 版本和 1 个仅剩失效样本的历史 benchmark 知识库，并同步清理 SQLite FTS、LanceDB 向量、chunk 和索引任务，避免“ready 但无法打开来源”的生产数据问题。测试题仍保留在 `backend/tests/eval/golden_qa.json`，不会随知识库清理删除。

本次本地运行时复核也已通过：Ollama `bge-m3` 返回 1024 维向量，并在 RTX 4060 笔记本上显示为 GPU 推理；Ollama `gemma3:4b` 的 OpenAI-compatible Chat 请求也已成功。当前运行时默认使用 Ollama：聊天为 `gemma3:4b`，Embedding 为 `bge-m3`；Gemini2API 仍保留在设置中，可作为需要更强长文本能力时的可选远程 provider。两者职责分离，不能把聊天网关误当成 embedding 服务。

307 篇本地 OA PDF 当前全部进入许可人工复核队列，尚未自动标记为可再分发。可用 `backend/scripts/license_review_report.py` 生成 JSON/Markdown 队列；审核人可以在 JSONL 记录中填写 `review_evidence`（许可证 URL、许可证名称、是否允许再分发、审核人、日期、证据 SHA-256 和备注），只有同时明确填写证据并将状态设为 `cleared_*` 后，公开资料包的 `--require-cleared-licenses` 闸门才会通过。报告会保留这些字段，不会把 OpenAlex 的 OA 标记推断成许可。内部受控部署可以检索这些资料，但教师/企业跨组织共享前仍需完成这一步。

当前还生成了独立的 Crossref 许可证候选队列：

```powershell
python backend/scripts/collect_license_candidates.py `
  --output data/logs/license_candidates.jsonl `
  --timeout-seconds 8 --retries 1 --workers 8
python backend/scripts/license_review_report.py `
  --candidates data/logs/license_candidates.jsonl
```

最近一次采集覆盖 307 篇论文，其中 183 条返回了 Crossref license URL，124 条没有许可证元数据，123 条因 TLS/429 等外部请求错误需要重试。这个队列只提供发现证据，不改变 `license_status`；审核人仍必须打开文章或许可证页面，确认当前版本的再分发条款，并把证据写回原始 manifest。

注意：43 条混合 golden question 不能直接用于评价每个知识库的整体质量；知识库 5 若混入 IDL 符号题会得到误导性的低命中率。因此评测必须按知识库/资料域分组，报告中同时保留快速检索评测和可选的 Gemini2API 端到端慢评测。

## Embedding 选择

当前服务已经支持 OpenAI-compatible 的 `/v1/embeddings`。最稳妥的本地方案是用 Hugging Face Text Embeddings Inference（TEI）部署 `BAAI/bge-m3`，把 `IDLRAG_EMBEDDING_API_BASE_URL` 指向 TEI 的 `/v1`，把 embedding model 设置为 TEI 接受的模型名，并把维度设为 1024。切换模型后必须重新索引全部文档，不能把 1536 维旧索引和 1024 维新索引混用。

如果只是单机试用，也可以用 Ollama 的 `bge-m3`。当前 Ollama 版本同时提供原生 `/api/embed` 和 OpenAI-compatible `/v1/embeddings`；本项目直接使用后者，因此不需要额外代理。Ollama 只需要一个非空占位 API key（例如 `ollama-local`），不会向本地服务校验该 key。切换模型后仍然必须重新索引全部文档。

### 本地 Ollama 配置示例

```dotenv
IDLRAG_EMBEDDING_API_BASE_URL=http://127.0.0.1:11434/v1
IDLRAG_EMBEDDING_API_KEY=ollama-local
IDLRAG_DEFAULT_EMBEDDING_MODEL=bge-m3
IDLRAG_EMBEDDING_DIMENSIONS=1024
```

启动 Ollama 后执行 `ollama pull bge-m3`，再在平台设置页进行连接测试和全量重建索引。RTX 4060 笔记本可以运行该模型，但应保留低显存模式和较小并发；生产部署仍建议把索引任务与在线对话分开，避免重建索引抢占显存。

OpenAlex 候选采集器 `backend/scripts/collect_remote_sensing_corpus.py` 具备指数退避和原子 checkpoint：代理 TLS 瞬断、429 限流或 5xx 时不会覆盖已经完成的记录；重启同一命令会从 `data/sources/collected_extended/.openalex_checkpoint.json` 继续。扩展候选池与正式 1,500 条生产候选分开保存，只有经过去重和质量抽查后才允许导入正式知识库。

超过单文档页数上限的权威 PDF 不会被静默截断。`backend/scripts/split_pdf_for_indexing.py` 会保留完整原文，并生成带原始 SHA-256、分片 SHA-256 和页码范围的索引分片；当前 825 页的 USGS LaSRC/校准文档已生成 4 个不超过 250 页的分片并完成索引。

## 生产门槛

每个知识库都必须同时满足：

1. 所有文档状态为 `ready`；不存在 `queued`、`processing`、`stale` 或 `failed`。
2. 所有文档使用真实可用的 embedding 服务；`embedding_is_fallback` 必须为 0，并在切换模型后重新索引。
3. 至少完成一次本地检索评测，保留命中率、引用覆盖率和失败样例。没有评测记录时，平台仍然可以对话，但状态必须是 `blocked`。
4. 对论文、数据和代码保留来源、许可、版本或 SHA-256；没有来源边界的文本只能标为候选资料。
5. 对 OCR 文档抽查公式、单位、代码和页码，并在研究运行的证据包中记录抽查结论。
6. 每条已索引文档的 `file_path` 必须仍然存在；来源丢失时门禁会阻塞，而不是继续把记录显示为可生产使用。

检查结果可以通过：

```text
GET /api/dashboard/corpus-readiness
```

返回结果按用户隔离，包含总体 `production_ready`、阻塞原因以及每个知识库的文档、chunk、fallback embedding、embedding 模型/维度/索引签名和评测状态。评测记录还会与最近一次文档索引时间比较；只要资料在评测后发生变化，知识库会自动变成 `evaluation_stale` 并阻塞发布，直到重新评测。历史失败任务如果没有被后续成功索引覆盖，也会阻塞发布。概览页显示的是同一结果，避免 UI 和 API 各自给出不同结论。

### 评测分层

平台保留两种评测方式：

1. **快速检索门禁**：使用本地 embedding、FTS/向量/混合检索和引用片段作为答案，验证资料是否能命中预期文件、符号、产品说明和工具规范；不依赖外部聊天模型，适合每次导入或重建索引后运行。
2. **端到端答案评测**：使用当前配置的 Gemini2API 生成答案，再评估关键词覆盖、引用支撑和回答相关性；它更接近用户体验，但响应慢、受模型服务可用性影响，不能作为唯一的发布健康检查。

每份报告都会记录知识库、题集、策略、top-k、评测模式和失败样例。遥感知识库至少要单独通过 `remote_product` 与 `tooling` 题集，不能只用 IDL 代码题的总分判断遥感能力。

### 备份与恢复验证

运行时资料、SQLite、解析结果、LanceDB 索引和研究产物属于私有数据，不提交到 GitHub。发布前可以生成并校验本地备份：

```powershell
python backend/scripts/backup_runtime.py data `
  --output data/backups/idl-rag-current.zip
python backend/scripts/backup_runtime.py data `
  --verify data/backups/idl-rag-current.zip
```

备份脚本使用 SQLite online backup 快照数据库，并为归档中的每个文件写入 SHA-256；校验命令会读取归档内容重新计算哈希。当前工作区已实际生成并校验一份 22,542 个文件、约 3.74 GB（未压缩内容）的备份归档。归档包含私有资料和加密配置，必须放入受控备份存储，不能上传到公开仓库。

## 补齐顺序

### P0：先解除错误的“可用”假象

- 为生产环境配置独立的 embedding endpoint（Gemini2API 只负责聊天并不自动代表提供 embedding）；连接测试通过后重建所有 fallback 文档。
- 将 OpenAlex 候选记录拆成“元数据候选库”和“已许可全文库”，全文必须记录 DOI、许可、获取日期和文件 SHA-256。
- 为扫描 PDF 建立人工抽查清单，至少覆盖公式页、IDL 代码页、表格页和 OCR 置信度最低的页面。

### P1：补成可复现实验资料

- 为 NDVI、NDWI/MNDWI、NDBI、地表温度、反射率/大气校正、分类和变化检测各准备一组“方法说明 + 输入数据说明 + 参考实现 + 独立验证样本”。
- 为 Sentinel-2、Landsat、国产卫星等数据写清波段映射、缩放因子、无效值、投影和 QA 掩膜规则。
- 每个方法至少添加一条 golden question 和一个负例（资料不足时应拒答或要求补充，而不是编造公式）。

### P2：再开放给教师和团队

- 运行本地检索评测和端到端研究任务集，记录引用正确率、公式一致性、影像产物和重跑一致性。
- 用真实 IDL/ENVI 运行时做编译与小样本执行验收；没有许可证时只能保留“静态检查/模拟执行”标签。
- 用 Docker、反向代理、备份恢复和长时间 SSE 测试验证部署，而不是只验证开发机上的 `127.0.0.1`。

## 现阶段的使用边界

可以用来：检索 IDL/ENVI 方法、整理论文候选、编写待验证的 Python/IDL 方案、执行本地小样本实验并生成带 provenance 的证据包。

不能用来：在没有评测、没有全文许可、使用 fallback embedding 或没有真实运行时验收的情况下，直接把 Agent 输出当作论文结论、工程交付或教学评分依据。
