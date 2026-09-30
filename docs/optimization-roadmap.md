# 工作台持续优化任务

这份清单用于持续改进 IDL RAG Panel 的真实使用体验。每项任务都必须有可复现的验收证据（测试、运行记录或人工操作路径），不以“页面能打开”作为完成标准。

## 优先级与验收标准

### P0 · 流式请求生命周期

- [x] 同一条请求只生成一条用户消息和一条助手消息。
- [x] SSE 必须收到 `done` 或 `error` 才结束；连接中途关闭要明确提示。
- [x] 切换会话、知识库或点击停止后，旧请求不能回写新会话。
- [x] 模型错误要在对话流中留下可识别的失败状态，而不是只显示一次性 toast。

### P0 · Agent 执行可观察性

- [x] 工具调用、工具结果和最终回答在同一个助手气泡内展示。
- [x] Agent 失败时保留失败步骤、错误原因和请求状态。
- [x] 长工具输出在卡片内部滚动，不推动输入区出屏。
- [x] 生成 `.pro`、运行 IDL、生成预览等产物都能回到当前会话；研究运行摘要会在 Agent 步骤中保留状态和阶段图预览。
- [x] 无知识库来源时不显示孤立的 `[1]` 引用；SSE 完成后不把“思考中/生成回答”残留成第二个助手气泡。
- [x] 研究运行工具结果默认摘要优先，原始 JSON 按需展开；同一轮 Agent 中重复的运行摘要不重复渲染。

### P1 · 研究任务工作流

- [x] 从研究项目进入 Agent 时自动带上项目上下文和资料状态。
- [x] 外部文献、GEE、Python preview 的授权状态在发送前可见，执行后可追踪。
- [x] Agent 可按 `project_id` 查询最近运行；运行卡片显示状态、模式、参数/验证指标摘要、阶段影像文件与私有路径脱敏结果。

### P1 · 界面韧性

- [x] 768px 及以下宽度下工具栏、消息、抽屉和输入区不重叠。
- [x] 研究 Agent 授权开关在 960px 以下独立换行、640px 以下纵向排列，避免与知识库/会话选择器抢空间。
- [x] 长文件名、长引用、长代码和长错误信息可换行或局部滚动。
- [x] 生成中、停止、失败、会话切换等状态不出现重复按钮或重复回答；上下文选择器与副作用操作在流期间锁定，停止按钮保持可用。

### P2 · 验证与回归

- [x] 后端路由级 HTTP/SSE 回归覆盖异常终止；客户端取消通过路由 body iterator 验证取消落盘标记且不生成 fallback。
- [x] Agent 生成器收到取消信号后停止后续工具调用和助手消息落盘。
- [x] 后端单元测试覆盖跨 chunk 引用过滤和 Agent 降级路径。
- [x] 前端构建通过，并完成一次真实 `gemin2api` 普通对话与 Agent 对话。
- [x] 文档同步记录本地启动方式、测试账号和已知外部依赖（GEE/IDL）。

## 本轮实施范围

本轮聚焦研究证据可核查性和流式收束：运行摘要补充公式、参数、输入资产和数据快照；研究上下文栏显示本次 Agent 的文献、Preview、GEE 授权状态；长运行结果继续采用摘要优先和原始 JSON 按需展开；异常/取消 SSE 已有路由级回归，临时的“思考中/生成回答”不再渲染成第二条助手回答。

## 本轮任务拆解（Agent 使用体验）

1. 过程信息收束：保留工具调用和研究证据，但把长 JSON 改成按需展开，并且同一次请求只突出最后一份运行摘要。
2. 布局韧性：把研究 Agent 的三个授权开关从主选择器中分组，在中小屏独立换行，长中文标签允许换行。
3. 真实回归：用本地 `gemin2api` 查询项目运行，确认 Run 卡片、阶段影像、无来源引用和完成态都符合预期。
4. 流式一致性：普通 `ask-stream` 与 Agent SSE 都在跨 chunk 场景下清理无来源引用，且不牺牲 token 流式输出。
5. 质量门禁：前端构建、后端 Agent/研究工具测试、lint 和 diff 检查全部通过后再提交。

本轮已完成以上五项，并在本地测试项目中保留真实 Python preview：Sentinel-2 B03/B11 + WorldCover 参考标签，Run 2 已完成，输出 4 张阶段 PNG 与验证指标（F1、IoU 等）。

## 下一轮任务拆解（证据与运行控制）

1. 在真实 Agent SSE 中回归新的运行 provenance 卡片，确认参数、公式、输入资产不泄露路径且能与研究页 Run 对上。
2. 增加客户端断开/异常 SSE 的 HTTP 集成测试，验证取消不会继续落盘，异常会有唯一终止事件。
3. 检查 768px 以下长引用、长文件名和错误信息的滚动边界，补齐界面韧性验收。
4. [x] 研究页增加按时间排序的运行时间线，并与 Agent 卡片使用相同的 Run 标识。

## 下一轮任务拆解（流式协议与交互互斥）

这轮优先解决“用户看见重复回答/重复操作”的根因，而不是继续堆叠展示组件：

1. [x] SSE 服务端只允许一个 `done`/`error` 终止事件；provider 在终止后迟到的事件被丢弃，provider 提前结束但没有终止事件时由服务端补发可识别的 `error`。
2. [x] 前端生成期间锁定知识库、研究项目、历史会话、GEE 获取和 `.pro` 开关；停止按钮仍可用，避免上下文在请求中途改变。
3. [x] GEE 抽屉在生成或获取期间禁用表单，并在提交函数保留竞态保护，避免返回的数据写入新会话。
4. [x] 工具栏与输入操作补齐可访问名称，页面暴露 `aria-busy`，便于键盘和辅助技术识别当前状态。
5. [x] 用路由级测试覆盖“完成后迟到异常”和“无终止事件的截断流”，前端生产构建通过。
6. [x] 完成态把工具折叠轨迹附在最后一条助手回答内，不再额外渲染第二个机器人气泡；浏览器回放确认完成后只保留一个助手头像。
7. [x] DevTools 实际回放“发送 → 停止 → 切换历史会话 → 重新发送”：停止请求只留下明确的未保存提示，旧流没有回写；重新发送后项目上下文仍为 project 1，旧 token/工具轨迹没有串入。
8. [x] 页面质量门禁：补齐 Select 的可访问名称、提高状态/引用元信息的对比度，并提供本地应用的 `robots.txt` 与 `llms.txt`；Lighthouse 快照最终 32/32 通过（Accessibility/Best Practices/SEO/Agentic Browsing 均 100）。

本轮验收证据：`backend/tests/test_chat_stream_routes.py`、前端 `npm run build --prefix frontend`，以及本地页面实际回放（发送 Agent 请求→生成中锁定上下文→完成后单一助手气泡→停止/切会话/重新发送）。后续再把这条操作序列固化为可重复的浏览器测试脚本。

## 当前轮次进度（研究运行连续性）

- [x] 研究页运行列表下方展示按 `created_at` 倒序的 Run 时间线，沿用 Run ID、状态、执行器、产物数、F1/IoU 和失败原因。
- [x] 使用本地账号实际读取项目 1、实验 2、Run 2：接口返回 `completed`，可供时间线与 Agent provenance 卡片共同核对。
- [x] 代码侧补强 768px 以下边界：页面栈、聊天工具栏、研究上下文、消息区和输入区阻断横向溢出；长引用、候选标题和产物名称允许换行或截断。
- [x] 浏览器级 768px/长文本验收已在 DevTools 实际窗口完成：768px、640px 和浏览器 500px 下 document `scrollWidth === clientWidth`，聊天工具栏/授权区/研究上下文/消息区/输入区均无横向溢出；长工具输出与错误区域保持局部滚动。
- [x] App 页面改为按路由按需加载；构建产物由单个约 1.36MB 主 JS 拆成多个页面 chunk，Chat/Research 不再随首屏同时加载。
- [x] Chat 普通流和 Agent 流的 token 更新改为按浏览器帧批量刷新；取消、切会话、错误和卸载都会清空待刷新缓冲，长回答不会为每个 token 触发一次渲染。
- [x] SSE 客户端在正常结束、Abort、异常回调和截断响应后显式 cancel reader，再释放锁，避免连续 Agent 会话残留流读取器。
- [x] Agent 模型异常不再静默降级为“未配置模型”：SSE 返回可识别错误，路由记录 `has_error`，失败请求不落盘空助手消息。
- [x] 重启当前本地后端后，用 `teacher_review_0930` + Gemini2API 真实执行研究 Run 查询：HTTP 200、SSE 终止事件唯一且为 `done`，无 `error` 事件。
- [x] 真实运行任务集 AR-001（只读项目审计）和 AR-010（越权拒绝）：两项均无异常；AR-001 工具契约 `passed`，AR-010 的 5 项拒绝边界经重新评估全部 `passed`。
- [x] 任务集 runner 捕获 Agent SSE `error` 并标记失败，补全回合复用原 session，避免错误被算作成功或产生孤立会话。
- [x] 真实运行任务集 AR-003（确认的 MNDWI Python preview）：Gemini2API 完成 `create_preview → queue_preview → run_summary`，Run 3/4 产生阶段 PNG；同一 Agent 回合重复排队会被幂等拦截。
- [x] 完整 `agent-research-poyang-v1` 任务集 10/10 通过本地 Gemini2API 契约：AR-001..AR-010 均有 trace，`passed_contract=10`、`needs_review=0`、`failed_with_exception=0`。
- [x] Agent 工具结果携带服务器生成的受控下一步提示；确认 preview 创建成功后会继续排队并查询摘要，不再停在 planned。
- [x] 证据审计/正式可比性请求现在强制实际调用 `research_verify_run` / `research_compare_runs`；前置条件不足时由工具返回 `not_available` 或 formal 拒绝，不再用模型文字冒充检查。
- [x] 用 `teacher_review_0930` 对 `/api/chat/agent-stream` 做端到端回归：普通 Agent 为 57 个 token、1 个 `done`、0 个 `error`；研究项目 Agent 为 182 个 token、1 个 `done`、1 个工具调用、0 个 `error`，无引用时返回空 citations。
- [x] 配置层读取 `.env` 并兼容 `IDLRAG_BASE_DIR=./data`，避免任务 runner 与 Uvicorn 使用不同密钥加密 Gemini2API 配置而造成 SSE 401。
- [x] `AppLayout` 改为按认证后懒加载；前端生产构建的最大共享 chunk 从约 632KB（gzip 205KB）降到 476KB（gzip 153KB），并消除了 Vite 500KB warning。

## 当前轮次进度（运行可观测性）

- [x] 普通问答与 Agent SSE 为每次请求生成 12 位受控 `stream_id`；仅用于关联前端气泡、服务端日志和测试结果，不包含用户、项目、路径、提示词或密钥。
- [x] `done`/`error` 终止事件携带服务端总耗时与首 token 耗时；服务端继续保证一轮请求只有一个终止事件，异常与截断流不会伪装成成功。
- [x] 完成态 Agent 气泡显示“服务端耗时 / 首 token / 流 ID”摘要；切换会话、停止、失败和重新发送会清除旧摘要，避免旧运行信息串到新回答。
- [x] 用本地 Gemini2API 做真实 Agent SSE 回归：HTTP 200、39 个事件、1 个 `done`、0 个 `error`，`stream_id` 长度 12，服务端耗时 48786.8ms、首 token 48677.0ms，未发现私有信息泄露。
- [x] 后端目标测试 28 项通过，ruff 通过，前端生产构建通过；Lighthouse 快照 32/32 通过（Accessibility / Best Practices / SEO / Agentic Browsing 均 100）。

## 当前轮次进度（无 embedding 服务时的检索降级）

- [x] 默认 `hybrid_rrf` / `hybrid_rrf_no_rerank` 检测最近一次查询是否使用 hash fallback embedding；检测到 fallback 时只使用 FTS + 规则排序，不再把伪向量混入默认候选融合。
- [x] `vector_only` 仍保留为显式诊断策略，用来验证 embedding 服务是否真正可用；系统不会把诊断结果冒充为默认语义检索质量。
- [x] 新增回归测试覆盖“fallback 向量不进入默认 hybrid”边界；15 项检索策略测试和 17 项 Agent/SSE 测试通过。
- [x] 使用本地运行中的 Gemini2API/工作台真实调用 `/api/chat/retrieve-debug`：`hybrid_rrf_no_rerank` 返回的 3 个候选均标记 `source_strategy=fts`，未混入 fallback vector 结果。
- [x] 配置读取边界收紧：`AppSettings(...)` 直接实例化不再隐式读取项目 `.env`，应用入口 `get_app_settings()` 仍显式加载 `.env`；全量后端回归恢复为 148 passed、1 skipped。

## 当前轮次进度（聊天与 Embedding 通道解耦）

- [x] 系统设置新增独立 `embedding_api_base_url` / `embedding_api_key`，留空时向后兼容复用聊天通道；Gemini2API 可继续负责聊天，Embedding 可接入本地或其他 OpenAI-compatible 服务。
- [x] 文档重建条件覆盖 embedding endpoint 或模型变化，避免旧向量索引被错误复用；敏感 embedding key 与聊天 key 一样加密存储、接口响应只返回 `has_*` 标志。
- [x] 设置页增加独立“测试 Embedding”按钮，真实请求 `/embeddings` 并校验返回向量维度；聊天 `/models` 成功不再被误认为 embedding 服务可用。
- [x] 独立通道的保存/加密/保留和连接测试回归通过；前端构建通过。

## 当前轮次进度（无项目绑定的公开文献助手）

- [x] Agent 新增 `public_literature_search`：仅在用户明确提出外部文献需求且打开开关后执行，支持 Crossref、OpenAlex、Semantic Scholar 的公开元数据查询。
- [x] 公开搜索不要求研究项目，不创建审计记录、证据卡或 RAG 文档；返回结果明确标记为候选，避免把搜索结果冒充已核验方法。
- [x] 未绑定项目时不发送影像、私有路径、项目资产或凭据；需要保存、导入或进入可比性审计时，仍须回到项目绑定的研究文献流程。
- [x] 对外部搜索的拒绝边界、无项目工具调用和真实 Crossref 响应均有回归覆盖；后端全量测试、前端构建和本地 SSE 回放作为提交门禁。

## 当前轮次进度（Agent SSE 可观测性）

- [x] 同步的 Gemini2API Agent 决策调用在等待期间每 8 秒发送一次受控 `waiting` 状态，避免浏览器和反向代理把长首 token 误判为断流。
- [x] 前端将 `waiting` 状态更新为同一个助手气泡内的等待文案，不新增消息、不进入最终工具轨迹，也不改变 `done/error` 唯一终止协议。
- [x] 客户端取消时仍设置取消事件；同步 provider 返回后会在落盘、工具调用和最终回答前再次检查，避免取消后的迟到结果污染会话。
- [x] 新增异步包装回归测试，并用本地 Gemini2API 实际回放验证 Agent SSE 返回单个 `done`。

## 当前轮次进度（Agent 最终文本流与网关兼容）

- [x] 支持工具兼容网关在 `stream=true` 下增量返回最终纯文本；工具调用 JSON 仍完整缓冲后再校验和执行。
- [x] 增加引用标记流过滤，只有已经存在的知识库来源才允许 `[n]` 进入 SSE；JSON 兼容响应不会把结构化决策泄露到回答气泡。
- [x] 对 `gemin2api` 做 Provider 能力判断：它的普通 Chat 支持流式，但当前工具流返回 502，Agent 自动使用稳定的非流式工具决策，避免重复等待和重复回答。
- [x] 未知兼容网关在尚未发出 token 时遇到明确的 4xx/5xx 流能力错误可回退到非流式；已经发出 token 后不重试，防止答案重复。
- [x] Gemini2API 两阶段模式已实测：JSON 工具决策完成后，第二阶段无 `tools` 的 Chat SSE 输出最终答案，回放收到 `token` 事件和唯一 `done`，没有 `error` 或第二个助手回答。

## 当前轮次进度（简单问题快速路径）

- [x] 未选择知识库、未绑定研究项目、没有外部文献/代码/实验/GEE/artifact 意图的 Agent 纯解释问题，直接复用普通 Chat SSE，跳过不必要的 tools JSON 决策。
- [x] 选择知识库或出现工具意图时不走快速路径，保留 Agent 检索、工具校验和引用链；取消、会话落盘和单一终止事件契约不变。
- [x] 同步普通流补齐跨 chunk 引用过滤，避免快速路径在最终收尾前短暂显示不存在的 `[n]` 来源标记。
- [x] 本地 Gemini2API 实测简单 Agent 首 token 从约 39 秒降至约 6.7 秒，事件序列为 `direct_stream → token → done`，无 `error`。
