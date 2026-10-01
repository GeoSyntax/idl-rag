# 工作台持续优化任务

这份清单用于持续改进 IDL RAG Panel 的真实使用体验。每项任务都必须有可复现的验收证据（测试、运行记录或人工操作路径），不以“页面能打开”作为完成标准。

## 当前迭代任务（错误可解释性与恢复）

这一轮先收紧“失败以后还能继续工作”的链路，避免用户在模型、权限、参数或文件服务出错时只能看到笼统的“请求失败”。每项完成后都要同时检查前端行为和 API 原始响应，不能只改一条提示文案。

- [x] 统一前端 API 错误解析：兼容字符串 `detail`、FastAPI 校验错误数组、`message/error` 载荷和 HTTP 状态码；不展示校验请求体中的原始输入。
- [x] 普通请求、SSE 建连、临时文件上传、artifact 下载和研究运行产物下载共用同一错误模型，并保留 `status` 供页面决定是否显示重试或重新登录。
- [x] 研究运行阶段图改为逐图加载；单个 PNG/图件失败不会清空整组影像，并可在原卡片中重试。
- [x] 为 Chat 会话恢复、研究运行详情、检索测试补充“重新加载/重试”而不是只依赖 toast；网络短暂失败时保留用户正在查看的旧结果。
- [x] 历史失败/取消运行的重试按钮保持 loading 到新 SSE 请求真正收束；附件读取失败或参数校验未通过时才立即恢复，避免慢网络下重复提交。
- [x] 真实浏览器回放已验证 Agent 成功请求在 1280px 和 500px 下只保留一个助手气泡；模拟网关 502 时只显示对话内错误卡片（无重复 Toast），点击重试后可恢复为一条完成回答。
- [x] Chat artifact 下载失败改为在原文件卡片内显示错误并保留再次下载入口；500px 浏览器回放确认没有重复 Toast 或横向溢出。
- [x] Chat 图片 artifact 的预览加载失败改为在当前图片占位区提供“重试加载”，重试只重新请求该图片，不清空同一回答中的其他产物。
- [x] 浏览器故障回放覆盖 422 参数校验、403、SSE 中途截断、网关 502 和 artifact 404；均只保留一个助手错误状态，500px 下无横向溢出，截断流仍保留安全 `stream_id`。
- [x] Docker/Nginx 代理配置已补齐 SSE 必需项：关闭 buffering/cache、HTTP/1.1、清除 `Connection`、`X-Accel-Buffering: no` 和 1 小时读写超时；配置说明已同步到部署文档。
- [ ] 在 Docker daemon 可用的真实反向代理/部署环境中复现网关 502、SSE 截断和下载服务故障，确认代理层不会改写或吞掉终止事件。
- [x] SSE 的 `run_started`、`error` 和 `done` 共用受控的 12 位 `stream_id`；失败气泡可显示该 ID 供定位，禁止把 token、私有路径、原始请求体写入界面或日志。
- [x] Agent 工具 schema 按用户意图收窄：绑定知识库的普通公式/方法问题只开放 `kb_search`；只有明确代码意图或上传输入 artifact 时才开放代码工具，避免误触发 lint/fix 或生成 `.pro`。单元回归 30 passed；Gemini2API 实际浏览器回放确认生成一条助手回答、无代码产物且 500px 无横向溢出。
- [x] 选中知识库的 Agent 请求增加一次受控 RAG 预检：先通过 SSE 展示 `kb_search` 调用与结果，再交给模型组织回答；有实际 `[n]` 使用标记才显示来源卡片，无结果或未使用来源仍不渲染引用。Gemini2API 实际回放确认 1 条助手气泡、1 个来源卡片、可展开工具轨迹，500px 下无横向溢出。
- [x] Agent 在执行前通过 SSE 输出受控任务计划：按研究项目、知识库、输入 artifact、外部文献/GEE/Preview 授权生成最多 6 个事实步骤；计划与工具轨迹一起挂在最终助手气泡中，取消请求不会继续发出计划或工具事件。Gemini2API 浏览器回放确认计划可展开、引用与工具轨迹共存且仍只有 1 个助手气泡。
- [x] 任务计划项结构化为 `id/label/kind/requires_consent/authorized`，前端显示“已纳入 / 已授权 / 需授权”标签；研究任务提到 GEE、论文或 Preview 但未打开对应授权时，计划明确标记为“需授权”，不把它当成已执行步骤。单元回归覆盖未授权计划，500px Gemini2API 回放确认标签不溢出。
- [x] 计划状态与同轮工具轨迹绑定：`tool_call` 显示“执行中”，正常 `tool_result` 显示“已完成”，服务端拒绝显示“被拒绝”，最终回答落盘后证据步骤显示“已完成”；Gemini2API 历史回答回放确认检索与证据两步均从“待执行”变为“已完成”。

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
- [x] SSE 终止事件补充受控阶段计时（检索、重排、模型首 token/总耗时）；前端按阶段展示，不把等待模型工具决策与实际回答首 token 混为一个数字。Gemini2API 实际回放确认同一助手气泡显示检索与模型耗时，且未暴露提示词、路径或密钥。
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

## 下一轮任务拆解（Agent 可用性与错误边界）

这一轮先处理“模型不可用时不能伪装成成功工作流”的边界，再继续做多用户并发与历史运行诊断。顺序按用户是否会被误导、是否影响平台并发隔离、最后才是体验增强排列。

- [x] Agent 没有可用模型时直接发送明确 `error`，不回退为带检索来源的成功回答，也不创建空/带引用的助手消息；新增回归覆盖代码意图请求。
- [x] Agent 阶段计时改为请求级上下文：检索、模型总耗时和模型首 token 在当前 Agent generator 内累计，`done/error` 优先使用本次事件数据；并发请求不再读取共享服务对象的上一条计时。31 项 Agent/SSE 定向回归与 Gemini2API 实际回放通过。
- [x] 普通 Chat `ask-stream` 同样使用请求级阶段计时：终止事件和请求日志优先采用本次流携带的检索/模型计时，不再把共享服务对象的上一请求数据显示给用户；新增普通流回归覆盖终止事件与历史落盘。
- [x] 历史 Agent 运行记录提供检索、模型总耗时和首 token 详情，并保留失败原因；恢复会话时可以核对本次运行，而不是只看到总时长。路由回归覆盖阶段计时从终止事件落盘到历史接口，前端构建通过。
- [x] 工作台页面路径与内存导航状态同步：直接打开或刷新 `/chat`、`/research` 等地址会恢复对应页面，浏览器前进/后退也会更新页面，不再把深链接重置为“概览”。
- [ ] 在可用 Docker daemon 的环境固化 Nginx SSE 502、截断和下载故障回放；本机 daemon 不可用时不以本地直连结果替代代理验收。
- [x] 增加浏览器级“模型未配置 → 错误 → provider 恢复后重试”回放：错误态只显示一个对话内错误卡片，不出现来源卡片、第二个助手气泡或残留运行计划；恢复后重试最终收束为一个助手回答。错误卡片对管理员提供模型设置入口，对普通用户明确提示由管理员维护全局配置。
- [x] 取消/失败运行的重试改为携带并校验原始 `message_id`：服务端复用同一条用户消息和私有附件，前端重试期间不追加第二个问题；Gemini2API 浏览器回放确认停止后重试最终为 1 条 user + 1 条 assistant，历史接口计数一致。
- [x] 设置页保存模型配置后通过跨标签页事件通知原 Chat 页面：无需刷新或重新输入问题，原错误卡片会变成“模型配置已更新，请点击重试”，保留原问题与附件重试上下文。
- [x] 对 Chat 页面完成桌面与窄屏 Lighthouse 回放：桌面/移动 Accessibility、Best Practices、SEO 和 Agentic Browsing 均为 100；修复侧栏选中项在暖灰背景上的对比度不足，500px 页面无横向溢出。
- [x] Agent 运行中切换历史会话或新建会话会立即 abort 旧 SSE，并用 request id 守卫迟到的 `run_started`/token；Gemini2API 浏览器回放确认新会话保持空白，旧会话仅留下 1 条 user 和 1 条 cancelled run，无 assistant 回写。
- [x] 修复 Agent 步骤折叠面板的重复 React key：流式事件与已落盘轨迹合并时不再只依赖后端步骤 ID，而是使用“步骤 ID + 事件位置”的稳定键；Gemini2API 500px 实时回放无重复 key 警告，最终仍只有一个助手回答。
- [x] 修复 Agent 工具标签 `kb_search` 的窄屏对比度：绿色标签改为高对比度样式；Chat Lighthouse 移动/桌面快照均恢复为 Accessibility、Best Practices、SEO、Agentic Browsing 100/100/100/100。
- [x] 将 Chat 顶部知识库状态从 `ready`、`fallback embedding`、`hybrid_rrf_no_rerank` 等内部标识改为“已就绪 / 备用向量 / 混合检索（不重排）/ 候选 / 重排”中文状态；保留诊断含义但不再把实现名直接暴露给老师和学生。
- [x] 本轮门禁使用项目 `backend/.venv` 执行全量后端测试 `169 passed, 1 skipped`；前端构建通过，Chat 移动/桌面 Lighthouse 均为 100/100/100/100。
- [x] Chat 新增只读模型状态摘要：普通用户可看到 `Gemini2API · 当前模型 · 已配置/待配置`，接口不返回 API Key、完整 URL 或提示词；设置更新事件会自动刷新状态，避免只能等到发送失败后才知道模型配置。
- [x] 对 Research 工作台完成桌面与 500px 窄屏 Lighthouse 回放：Accessibility、Best Practices 和 Agentic Browsing 均为 100；修复项目选择列无标签、入口下拉框的非法 aria 属性、Ant Design 默认蓝/灰文字对比度不足、协议表单未挂载警告，以及窄屏研究标签溢出菜单导致的 ARIA 树错误；500px 页面无横向溢出。
- [x] 清理 Research 数据与快照流程的第二层可用性问题：GEE/STAC/验证样本/数据资产表单的必填 Select 改为不污染 ARIA 树的自定义校验；文件选择器和多波段对齐控件补齐可读名称；InputNumber 去除 addonAfter 弃用警告。数据 Tab 在 500px 下 Lighthouse Accessibility、Best Practices、SEO 和 Agentic Browsing 均通过。
- [x] 收紧 Research 实验与影像证据页：实验选择表格补齐选择列的表头和每行标签，重跑基准、候选参数和 JSON 编辑控件补齐名称；500px 与桌面实验 Tab Lighthouse Accessibility、Best Practices、SEO 和 Agentic Browsing 均通过，页面无横向溢出。
- [x] 修复 RAG Tab 的窄屏可读性：知识库绑定表与检索引用表改为内部横向滚动，避免知识库名称逐字竖排；检索按钮使用高对比度品牌色。研究协议输入框补齐可读名称，并在生成按钮禁用时显示“至少 8 个字符”的明确原因。
- [x] 自定义 Select 必填校验保留视觉必填星号，同时不把非法 `aria-required` 写到 Ant Design 的 div wrapper；用户仍能看到填写边界，屏幕阅读器仍保持合法树结构。
- [x] EvidenceCard / FormulaSpec 展示层把 `candidate`、`verified`、`draft`、`frozen` 等内部枚举翻译为明确中文状态，保留后端值不变；修复候选状态 Tag 在暖色背景上的低对比度，证据页 500px Lighthouse 四项门禁均通过。
- [x] 普通 Chat 流式期间显示“正在生成回答…”而不是误称为 Agent；运行记录使用通用无障碍名称，完成状态文字提高对比度；聊天桌面/移动 Lighthouse 快照均为 100。

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
- [x] 前端允许 Agent 在未选择知识库、未绑定研究项目时直接发送一般问题；普通聊天仍要求知识库或上传文件，避免把无证据回答误认为已完成检索。
- [x] 聊天工具栏改用实际聊天容器宽度断点；侧栏占用空间时也会自动换行，避免选择器、授权项和会话操作互相挤压。
- [x] 长任务期间状态提示固定显示在同一个助手气泡内，展示当前工具阶段和已用时；工具轨迹仍可展开查看，计时不进入屏幕阅读器的重复播报。
- [x] 研究工具结果读取 `metadata.runs[].status` 并显示 queued/running/completed/failed 阶段；创建 preview、排队任务和 GEE/文献/代码工具也使用真实工具名映射，不再显示难以理解的内部标识。
- [x] 研究运行摘要卡对 queued/running 的当前用户运行每 5 秒只读刷新；运行完成、失败或取消后自动停止轮询，临时网络失败保留上次可见状态并退避重试。
- [x] ChatSession 持久化研究项目绑定；重新打开历史会话时恢复项目选择和 Agent 模式，避免消息上下文与顶部研究上下文脱节。
- [x] 新增项目级只读运行查询；恢复历史会话时展示最近研究运行并复用 queued/running 自动刷新，不重放 Agent 请求或生成第二条回答。

## 当前轮次进度（空流与引用一致性）

- [x] 修复“只引用第 2 个检索候选”时的编号错位：收尾阶段同时重排正文标记和来源列表，未被正文引用的候选不再出现在参考来源卡片中。
- [x] Agent 收到 `done` 后、历史消息回读期间不再渲染只有工具步骤的临时机器人气泡，避免完成态短暂出现第二个回答。
- [x] Gemini2API 普通 SSE/Agent 快速路径遇到只有 role/finish 帧的空流时改走明确的非空本地降级回答；不会保存空助手消息并发送误导性的成功完成态。
- [x] 回归证据：Agent/SSE 测试 25 项通过；前端生产构建通过；本地 Gemini2API 真实回放为 `step → token × 3 → done`、0 个 `error`，并在历史接口中读取到非空助手回答。

## 当前轮次进度（Agent 历史轨迹）

- [x] 已完成 Agent 的工具调用/结果轨迹会随助手消息落库，历史接口一次返回回答与轨迹，恢复时不重放模型请求。
- [x] 轨迹只保存 bounded 的工具名、参数字段名、结果长度/摘要指纹和安全研究摘要；原始路径、私有 URI、凭据、工具输出和完整提示词不会进入 `chat_messages`。
- [x] 历史会话恢复后，轨迹继续附在唯一的助手气泡中；普通 Chat、GEE、IDL 和无工具 Agent 消息保持空轨迹兼容。
- [x] 回归证据：Agent/研究工具定向测试 33 项通过，前端 TypeScript/生产构建通过，数据库兼容迁移覆盖已有 `chat_messages` 表。

## 当前轮次进度（Agent 延迟与工具范围）

- [x] 按会话上下文裁剪 Gemini2API 的 function schema：研究项目、知识库和外部搜索/Preview/GEE 授权分别只暴露相关工具；服务器端权限校验保持不变。
- [x] 对常见的研究项目状态查询启用只读快路径：直接执行项目摘要、协议就绪检查和运行摘要，再进行一次无工具最终回答；研究检索、公式、代码、GEE 和写入任务继续使用完整 ReAct。
- [x] 本地 Gemini2API 真实回放：状态查询从上一轮约 58–87 秒的多轮决策，降为 `session_id=85`、约 7.7 秒、3 个只读工具调用、1 个 `done`、0 个 `error`，仍保留 6 个可追踪工具步骤。

## 当前轮次进度（运行落盘与历史核查）

- [x] 普通 Chat 与 Agent SSE 在收到 `run_started` 后建立安全关联；该事件只携带会话 ID，不携带问题、文件路径、私有 URI 或密钥。
- [x] 每次流式请求落盘 `stream_id`、终态（`completed` / `failed` / `cancelled`）、服务端耗时、首 token 耗时、引用/产物数量、Agent 步数和受限错误摘要；旧数据库会在启动时兼容补列。
- [x] 新增 `GET /api/chat/sessions/{session_id}/runs?limit=20`，只返回当前用户拥有会话的安全运行元数据，不返回 prompt、原始模型输出或凭据。
- [x] 历史会话加载后在同一个聊天区域展示运行记录，失败/取消请求不会伪装为成功；旧记录没有 stream ID 的字段会保守显示为“已完成/失败”，不影响原有消息恢复。
- [x] 概览页将 Embedding 降级与 Chat/Agent 模型状态分开表达，不再把网关 404、URL 和 httpx 原始错误直接展示给用户；有运行记录的历史会话即使只有一次成功请求也会显示可核对的运行条目。
- [x] 回归证据：后端全量 `163 passed, 1 skipped`；Ruff、Python 编译检查和前端生产构建均通过；本地 Gemini2API 实时 Agent 回放收到 `run_started → step × 8 → token → done`，并在 session 87 的运行接口中读取到 `completed`、`agent_step_count=8`、`error_message=null`。

## 当前轮次进度（历史失败运行重试）

- [x] 运行记录保存安全重试上下文：原消息 ID、Agent/普通模式、`.pro` 开关、输入 artifact ID 和附件文件名；不保存 prompt、完整回答、服务器路径或密钥。
- [x] 上传附件以私有 `chat_input` artifact 保存文本提取结果，历史消息可显示“已保存上下文”；原始二进制不被伪装成可下载的历史文件。
- [x] 对 `failed` / `cancelled` 运行提供同一会话内的一键“重试”，自动恢复可用的知识库/项目上下文、输入资料和附件文本；附件已清理时给出明确提示，不静默发送缺上下文的请求。
- [x] 回归证据：真实 Gemini2API Agent 请求 `session_id=89` 返回唯一 `done`，`run_id=49` 记录 `message_id=198`、附件 artifact `aac47da23ba249abad856ebc0210ea47`，历史消息中用户输入与 `chat_input` artifact 一一对应；前端 `npm run build` 通过。

## 当前轮次进度（科研回答排版）

- [x] Agent/Chat 助手回答使用安全 Markdown 渲染，支持标题、段落、列表、引用、行内代码和 fenced code block；原始 HTML 默认不执行，避免把模型输出当成页面代码。
- [x] 接入 KaTeX 数学公式渲染，`$...$`、`$$...$$` 和常见 LaTeX 公式在回答气泡中以可读的数学排版显示，长公式在窄屏内部横向滚动，不撑破页面。
- [x] 真实浏览器回放确认 NDVI 公式从原始 `$\\text{...}$` 字符串变成可读分式；页面无 console error，768px 下仍无横向溢出。

## 当前轮次进度（历史会话模式连续性）

- [x] 会话列表根据最近一次安全运行记录返回 `last_mode`，不修改已有数据库结构；旧会话没有运行记录时保持空值。
- [x] 重新打开历史会话时恢复普通/Agent 模式；绑定研究项目仍强制使用 Agent，避免项目上下文被普通 Chat 绕过。
- [x] 回归证据：真实账号的 session 90、89 等历史 Agent 会话接口返回 `last_mode=agent`，浏览器选择 session 90 后顶部 Agent 单选项自动选中；路由定向测试 6 项通过。

## 当前轮次进度（研究证据边界可见性）

- [x] Chat 中的研究运行卡显式区分 `preview` 与 `formal`，不再只用绿色 `completed` 状态让探索性结果看起来像正式结论。
- [x] Preview 没有 `validation_metrics` 时，卡片说明当前只有阶段图/运行清单，必须补充参考资产或样本验证设计后才能比较指标。
- [x] Formal 卡片仍提示必须检查证据包和独立测试；这只是证据边界提示，不会把运行状态自动升级为科学结论。
- [x] Chat 运行卡与 Research 运行详情共用 `ResearchEvidenceBoundary`，Preview/Formal、validation_metrics 和 Evidence Package 校验状态不再由两套文案分别维护。
