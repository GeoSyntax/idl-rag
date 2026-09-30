# IDL RAG Panel 项目状态

## 当前定位

IDL RAG Panel 当前是一个本地优先、可用于展示和科研试验的遥感研究工作流平台；ENVI/IDL RAG 是其基础能力，而不是产品上限。它不是通用聊天壳，也不是公开 SaaS；平台围绕论文/方法、IDL 代码、Python/GDAL 资料、Data Catalog、公式实验、阶段影像和可复现证据包组织工作。

## 已完成能力

### 1. 用户与权限

- 用户注册、登录和 token 鉴权。
- 管理员用户管理、重置密码和账号状态控制。
- 知识库、文档、对话会话和 artifact 按 owner 隔离。
- 密码使用 PBKDF2-SHA256 哈希存储。

### 2. 配置与密钥管理

- 后端通过 `backend/app/core/config.py` 读取 `IDLRAG_` 环境变量。
- 设置页可配置 provider、API base URL、chat model、embedding model、rerank 和 LangSmith 参数。
- `api_key`、`rerank_api_key`、`langsmith_api_key` 由 `backend/app/services/settings_service.py` 加密后写入 SQLite。
- `backend/app/core/security.py` 支持 Fernet 加密和旧密钥轮转解密。
- 生产环境不应使用默认 `IDLRAG_AUTH_SECRET`。

### 3. 文档摄入与索引

- 支持 `.pdf`、`.md`、`.markdown`、`.txt`、`.pro`、`.idl`。
- 支持文件上传和本地路径导入。
- 使用 SHA256 去重。
- 后台 index worker 处理队列任务。
- 普通文本按段落分块，IDL/ENVI 代码按符号边界分块。
- 写入 SQLite chunks、SQLite FTS5 和 LanceDB 向量索引。
- embedding provider 不可用时有 hash fallback，页面会提示语义/向量质量下降。

### 4. 检索与 RAG

- 支持 SQLite FTS5 关键词检索。
- 支持 LanceDB 向量检索。
- 支持 Hybrid RRF 融合、rerank 对照、multi-query、parent-child、dependency GraphRAG 和工具型代码检索。
- 当前默认策略为 `hybrid_rrf_no_rerank`，它在已有综合 benchmark 中与 rerank 版本效果接近但速度更好。
- Citation 保留 rank、strategy、score、line range 和 metadata，便于解释回答来源。

### 5. Chat 与 Agent

- 普通 Chat 支持多知识库、流式回答、会话保存、历史会话加载/重命名/删除。
- Agent 模式支持工具步骤展示。
- 支持上传临时附件参与当前问题。
- 支持生成 `.pro` 文件并作为 chat artifact 下载。
- 支持基于已生成 artifact 进入修复模式。

### 6. RetrievalLab 与评测

- RetrievalLab 可选择知识库、策略、top_k 并查看候选结果。
- Settings 页面可运行本地评测、LangSmith dataset sync 和 LangSmith evaluate。
- 评测报告支持列表和详情 Drawer。
- 默认本地策略对比包含 `hybrid_rrf_no_rerank` 与 `hybrid_rrf`。
- 工具型 code RAG case 与自然语言 QA case 分开评估，避免用普通 QA 指标误判 symbol/code 工具。

### 7. 前端工作台

- Dashboard 展示资料状态、索引状态和使用情况。
- KnowledgeBases 管理默认 retrieval strategy、top_k 和 rerank 配置。
- Documents 管理文档导入、状态、重试、删除和 chunk 查看。
- Chat 展示检索策略、fallback 状态、回答、citation 和 artifact。
- RetrievalLab 用于检索策略调试。
- Settings 用于 provider/key/model 配置和评测报告查看。

### 8. 遥感研究工作流

- 教师和学生都可以注册、登录、创建开放研究或模板项目；当前协作按项目成员共享，不强制教师/学生角色分级。
- 项目支持私有 GeoTIFF、受控 GEE/STAC 数据登记、快照冻结、公式/证据卡和研究协议版本。
- 提供 PythonRunner（默认）与可选项目级 IDLRunner；IDL 缺失不会阻断 Python 工作流。
- 本机 ENVI/IDL 8.8 的 `envi_idl.exe` 已被发现为 Workbench 启动器；它可启动但严格项目探针未生成声明的预测 GeoTIFF。Runner 现在会拒绝 `envi_idl`/`idlde`/`idlrt` 作为 `.pro` 入口；候选 `idl.exe` 现场运行返回 `Failed to initialize IDL instance`，被归类为 `unavailable`。正式研究仍需配置有效授权的 `idl.exe` 或 ENVI batch/SAV 节点，并完成带空间参考的输出约定。
- 每个空间步骤可保存 PNG/GeoTIFF、manifest、验证指标、误差图和 review-first 报告；正式运行可以导出 Research Evidence Package。
- 项目 RAG 可显式绑定 Method、IDL Code 和 Python Code 知识库；外部 Crossref/OpenAlex/Semantic Scholar 搜索需要人工确认，摘要导入会保留 `abstract_only` 标记。
- 研究 Agent 可在独立授权下检索项目 RAG/公开文献，读取安全 Data Catalog、运行指标/阶段图件描述并校验证据包；在确认后可基于已有公式与快照创建、排队 Python preview，仍不能执行 formal/IDL、修改公式/协议或读取原始路径。Ollama/LM Studio 等本机 OpenAI-compatible endpoint 在 localhost/host.docker.internal 上可免 API Key 进入 Agent 工具循环。
- 已建立版本化 Agent 科研任务集 `AR-001`–`AR-010`：覆盖项目审计、论文/RAG、MNDWI 基线、改进公式、参数 sweep 交接、SAR/fusion 对照、证据包审计、文献到代码和越权拒绝；任务定义与教师演示顺序见 [`docs/agent-research-task-suite.md`](docs/agent-research-task-suite.md)。
- 已使用本地 `gemin2api`（`127.0.0.1:8081/v1`，`gemini-3.6-flash`）真实运行全部 10 个 Agent 科研任务；隔离案例中 MNDWI 阈值 `0.0` 与 `0.15` 的 Python preview 已由本地 worker 执行完成并生成指数图、水体分类图、GeoTIFF 和 run manifest。最新汇总为 8 个任务通过工具契约、2 个 `needs_review`、0 个程序异常；结果与科学边界见 [`docs/agent-demo-result.md`](docs/agent-demo-result.md)。
- Compose 已提供前端、API、独立索引/研究 worker 和持久化卷；API `/api/health` 是 liveness，`/api/ready` 检查数据库与 worker readiness。
- 已完成第一条真实公开数据研究案例：Planetary Computer 的 Sentinel-2 B03/B11（2024-10-05）、Sentinel-1 RTC VV（线性功率转 dB）与 ESA WorldCover 2021 class 80 代理参考，在鄱阳湖小 ROI 上完成 10 m 同网格对齐、MNDWI 与 SAR formal PythonRunner、阶段影像、验证指标、formal comparison 和两份证据包；两次 Run 返回 `completed`，证据包均 `verified=true`，比较返回 `comparable=true`，输入资产 provenance metadata 记录了单位、转换公式、重采样和目标网格。结果仅表示与跨年份代理层的一致性，不是同期现场真值或湖泊级结论，详见 [`docs/real-research-case-poyang.md`](docs/real-research-case-poyang.md)。
- 真实案例曾暴露 EPSG:4326 COG 的 `target_resolution=10` 被误当成 10 度的问题；现已按地理 CRS 的纬度换算米制分辨率，并由 `test_geographic_target_resolution_is_interpreted_as_metres` 回归覆盖。问题和科学边界记录在 [`docs/real-case-issues.md`](docs/real-case-issues.md)。

## 当前验证方式

后端关键测试：

```powershell
uv run --project backend pytest backend/tests/test_retrieval_strategies.py backend/tests/test_agent_service.py
uv run --project backend pytest backend/tests/test_eval_golden_qa.py backend/tests/test_eval_metrics.py backend/tests/test_evaluation_api.py
uv run --project backend pytest backend/tests -q
uv run --project backend ruff check backend/app backend/tests
```

前端构建：

```powershell
npm run build --prefix frontend
```

手动展示路径：

1. 启动后端和前端。
2. 登录 admin 或注册新用户。
3. 配置模型 provider。
4. 创建知识库并导入文档。
5. 等待文档 ready。
6. 在 Chat 中提问并查看 citation。
7. 在 RetrievalLab 中运行默认策略并打开候选详情。
8. 在 Settings 中查看或运行评测报告。

## 当前限制

- 当前存储是本地 SQLite + LanceDB，适合本地和小团队部署，不是高并发分布式架构。
- 向量质量依赖 embedding provider、embedding model 和索引是否重建。
- fallback embedding 可保证流程可运行，但语义检索质量会下降。
- 本地 `data/` 中包含数据库、索引、日志、解析文本、生成 artifact 和用户文档，不能上传到 GitHub。
- 私有 PDF、简历、课程材料和未授权数据源默认不作为公开展示材料。
- Docker Compose 的配置、Dockerfile 和 health/readiness 契约已实现；本机 Docker Desktop Linux daemon 未启动，因此镜像构建、容器重启持久化和真实多容器启动仍需在 Docker 可用的干净环境现场验收。
- 当前仍未引入 CI/CD、对象存储、生产级分布式任务队列、学校/企业 SSO 和组织级目录；这些不应先于单机真实科研闭环。

## GitHub 发布状态

本轮整理后的仓库发布策略：

1. 先提交 `.gitignore` 和 `.env.example`，防止误传 key 与运行数据。
2. 再提交 README、架构图和专项文档。
3. 再提交后端源码与测试。
4. 再提交前端源码与构建配置。
5. 最后只在人工确认后提交脱敏、可公开的演示材料。

推送到 GitHub 前必须检查：

```powershell
git status
git diff --cached
```

确认没有 `.env`、真实 key、`data/app.db`、索引、日志、私有 PDF、简历、source documents、`node_modules`、`dist` 或 `.venv` 进入暂存区。
