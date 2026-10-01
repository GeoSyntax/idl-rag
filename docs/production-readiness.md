# 资料生产就绪标准

这份标准回答一个容易被 Demo 掩盖的问题：**资料能被检索，不等于资料可以支撑生产研究结论。**

平台现在会在概览页和 `GET /api/dashboard/corpus-readiness` 显示同一套硬门槛。只要任一门槛不满足，状态就是 `blocked`，不会把知识库标成“生产可用”。

## 当前盘点（本地工作区，2026-10-01）

| 资料类别 | 当前情况 | 可以做什么 | 还不能声称什么 |
| --- | --- | --- | --- |
| ENVI/IDL 官方资料 | 415 份已索引文档 | 查找语法、API、处理流程 | 不能替代具体版本的本地 ENVI/IDL 编译验收 |
| IDL/ENVI 方法资料 | 18 份方法与代码资料 | 支撑常见读写、指数和处理样例 | 不能覆盖全部传感器、投影、质量控制与异常数据 |
| 遥感算法知识库 | 1,251 份文档：1,000 条 OpenAlex 候选记录、173 篇本地 OA PDF、50 条理论资料、24 份官方产品资料、5 份工具规范，以及新增论文解析资料 | 支撑产品约定、指数/温度/分类/变化检测方法和工程验证 | OpenAlex 候选记录主要是元数据/摘要；OA PDF 仍需逐条核对再分发许可 |
| 扫描版 IDL 实验资料 | 1 份文档，已用 `pdf-ocr-v1` 完成 OCR 和索引 | 可按 OCR 文本检索并引用 | OCR 仍需人工抽查公式、表格和代码，不能自动视为无误 |

当前本地工作区已经完成一次生产门禁：6 个 owner=1 知识库共 1,700 份资料全部为 `ready`，1,700 份资料均使用 `bge-m3 / 1024` 真实向量，fallback 为 0；知识库 5 的 10 条遥感产品/工具 golden cases 在资料增量前命中率为 1.0，增量导入后必须重新评测才能恢复发布状态。这个结果说明本地资料链路可投入受控试用，但不等于所有研究问题都已覆盖，也不替代论文许可、真实数据运行和教师复核。

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

## 生产门槛

每个知识库都必须同时满足：

1. 所有文档状态为 `ready`；不存在 `queued`、`processing`、`stale` 或 `failed`。
2. 所有文档使用真实可用的 embedding 服务；`embedding_is_fallback` 必须为 0，并在切换模型后重新索引。
3. 至少完成一次本地检索评测，保留命中率、引用覆盖率和失败样例。没有评测记录时，平台仍然可以对话，但状态必须是 `blocked`。
4. 对论文、数据和代码保留来源、许可、版本或 SHA-256；没有来源边界的文本只能标为候选资料。
5. 对 OCR 文档抽查公式、单位、代码和页码，并在研究运行的证据包中记录抽查结论。

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

备份脚本使用 SQLite online backup 快照数据库，并为归档中的每个文件写入 SHA-256；校验命令会读取归档内容重新计算哈希。当前工作区已实际生成并校验一份 13,866 个文件、约 1.69 GB 的备份归档。归档包含私有资料和加密配置，必须放入受控备份存储，不能上传到公开仓库。

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
