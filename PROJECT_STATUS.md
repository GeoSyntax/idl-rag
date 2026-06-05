# IDL RAG Panel 项目状态

## 当前定位

IDL RAG Panel 当前是一个可本地运行、可用于展示的 ENVI/IDL 专用 RAG 工作台。它不是通用聊天壳，也不是公开 SaaS；当前重点是把用户自有 ENVI/IDL 文档、代码和课程材料整理为私有知识库，并提供可解释的检索、问答、代码辅助和评测能力。

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

## 当前验证方式

后端关键测试：

```powershell
uv run --project backend pytest backend/tests/test_retrieval_strategies.py backend/tests/test_agent_service.py
uv run --project backend pytest backend/tests/test_eval_golden_qa.py backend/tests/test_eval_metrics.py backend/tests/test_evaluation_api.py
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
- 当前还未引入 CI/CD、对象存储、生产级任务队列和容器化部署。

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
