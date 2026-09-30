# 遥感研究工作流平台 V1：验收标准

> **状态：验收基线。**  
> 本文将 [研究与实施蓝图](research-workflow-platform-plan.md) 中的 V1 目标转为可观察的验收项。通过单一后端单元测试不等于整个 V1 完成；每一项必须有与其范围相符的代码、运行记录、自动化测试或人工演示证据。

## 1. 验收原则

1. **先证明，再宣称。** 规划、页面草图和未执行代码都不是完成证据。
2. **研究结论与探索结果分开。** 正式结果只能来自冻结的数据、公式、参数、验证和环境。
3. **私有数据最小外发。** 默认 `private-local`；没有明确策略与审计证据，不得假设可以把原始资产传给外部服务。
4. **每个空间步骤可见。** 有空间输出的步骤必须有可回溯的影像/地图、统计和渲染元数据。
5. **IDL 不阻断 Python。** Python 是新增研究模板的默认执行器；IDL 只能作为可选对照和兼容路径。

## 2. 分阶段验收门槛

### Gate A — 研究协议与数据快照基础

| ID | 必须满足的行为 | 证明方式 |
|---|---|---|
| A-01 | 登录用户可创建并列出自己的私有研究项目；未受邀用户无法通过项目 ID 读取项目或其资产。项目创建者可按已注册用户名邀请协作成员，成员无需教师/学生角色分级即可共同使用项目。 | API 集成测试覆盖拥有者/成员/未受邀用户路径。 |
| A-02 | 项目默认 `visibility=my`、`egress_policy=private-local`、`status=exploratory`。 | 创建响应、持久化记录和自动化测试。 |
| A-03 | 本地、GEE 和参考数据可登记为 DataAsset，记录来源 URI、资产类型、可选 SHA-256 与元数据；登记本身不读取文件或外发数据。 | API/服务测试与代码审查。 |
| A-04 | 数据快照只能引用同一项目资产，不能重复引用同一资产；创建后生成稳定 SHA-256 指纹且标为冻结。 | API 集成测试。 |
| A-05 | 证据卡区分 candidate/verified/imported/experiment_pinned；已核验或已固定卡必须有 DOI 或来源 URL。 | API 集成测试。 |
| A-06 | 候选/冻结 FormulaSpec 必须关联证据卡；冻结规格至少关联一张已核验证据。 | API 集成测试。 |
| A-07 | 正式实验必须使用冻结公式和数据快照，验证方案必须声明 `spatiotemporal-holdout`，并登记同快照的 0/1 参考资产或“独立测试 + 最小置信度 + 无冲突 + 总体与逐空间块/逐时间分层的最小有效样本覆盖”的点样本规则；还必须具备可视化证据契约，否则拒绝创建。 | API 集成测试。 |

**Gate A 当前自动化入口：** `backend/tests/test_research_workflow_routes.py`。Gate A 通过仅表示研究领域的元数据/协议基础可用，**不表示 PythonRunner 已实现**。

### Gate B — PythonRunner 与模型对照

| ID | 必须满足的行为 | 证明方式 |
|---|---|---|
| B-01 | PythonRunner 能在受控本地环境读取一个 DataSnapshot，并按 FormulaSpec 运行至少一个 M1 光学基线。 | 可重复的集成测试、示例数据与 Run 日志。 |
| B-02 | M1–M3 输出遵守同一结果契约：栅格/矢量产物、统计、运行日志、参数和环境信息。 | 产物 manifest 与集成测试。 |
| B-03 | IDLRunner 缺失、未配置或许可不可用时，Python 预览/正式任务仍可运行；IDL 任务明确报告不可用原因。 | 模拟运行器测试。 |
| B-04 | 如果研究者已将本地受许可 IDL 输出登记为私有 `derived` GeoTIFF 并在实验参数中声明对照，系统可比较 CRS/网格、NoData、连续变量差异、分类一致性指标和允许容差；差异 GeoTIFF、PNG 与 JSON 报告必须随 Run 保存。 | 对照集成测试、Run manifest 和正式证据包。 |
| B-05 | 研究者可用受限、声明式波段数学公式测试反射率/指数候选：只能引用同一栅格内已声明波段、有限数值参数及白名单数学运算；不得执行 Python、调用未知函数、访问属性/下标或隐式读取验证标签。每次运行输出特征及分类栅格/预览，并把公式、参数和波段变换记录在 manifest。 | 数值等价、参数覆盖、非法表达式拒绝、图件产物与正式验证/证据包集成测试。 |
| B-06 | 研究者可将同一项目内两个或多个私有 raster 资产显式对齐为新的多波段 GeoTIFF：参考资产定义 CRS/仿射变换/尺寸，其他资产按声明的 nearest/bilinear/cubic 重采样；输出有新 SHA-256、源资产指纹、网格和 NoData 元数据。远端 reference、跨项目、非栅格或不可读输入必须拒绝。 | `backend/tests/test_research_raster_stack.py` 覆盖正常对齐、跨项目/远端拒绝、无效 GeoTIFF；真实 Sentinel-2 B03/B11 同场景 API 探针已生成 2 波段 EPSG:32650 输出。 |
| B-07 | 研究者可对 `preview` 实验提交 2–20 个候选参数，在 `development` 或 `model_selection` 点样本上逐个运行并按 OA/Precision/Recall/F1/IoU 排名；每个候选保存参数、样本划分、指标、失败原因和独立图件。系统拒绝正式实验、IDL 扫参、`independent_test` 扫参及非法/重复候选；不自动冻结公式或宣称正式结论。扫参支持 sync/queue 两种模式，队列可被 worker claim、取消、超时回收和独立重试。 | `backend/tests/test_research_parameter_sweep.py` 覆盖候选排名、独立测试隔离、候选失败保留、队列 claim、已完成重试、排队取消/重试、数量边界和未受邀拒绝；研究页提供候选 JSON、划分、排序指标和立即/排队入口，前端构建通过。 |

### Gate C — 影像证据与验证

| ID | 必须满足的行为 | 证明方式 |
|---|---|---|
| C-01 | 输入、预处理、特征/公式、模型输出、不确定性、误差和最终图至少按模板契约保存。 | 运行产物清单与界面演示。 |
| C-02 | 每张图可回溯到 Run、DataSnapshot、FormulaSpec、色带、数值范围、CRS、时间、NoData 比例和生成参数。 | 视觉元数据 JSON、API/界面检查。 |
| C-03 | 标注样本记录来源、时间、标注人、置信度、时空分层、数据划分和冲突状态；支持绑定一个冻结快照的受限 CSV 原子批量导入。 | 标注/导入服务测试和数据导出检查。 |
| C-04 | 正式验证采用时空独立留出设计；报告 OA、Precision、Recall、F1、IoU、面积差异及不确定性/分层结果。 | 冻结 ExperimentSpec、指标产物和研究报告。 |
| C-05 | 栅格参考验证和独立点样本验证对 OA、Precision、Recall、IoU 输出可复现的 95% Wilson 区间；每个区间记录成功数、分母、方法和“未加权/不代表复杂抽样设计”的假设；分母为零时输出 `null`，不得伪造区间。 | `backend/tests/test_validation_statistics.py` 覆盖边界；PythonRunner 集成结果将区间写入 `validation_metrics.json`、`validation_sample_metrics.json` 和 Run manifest。 |
| C-06 | 正式点样本验证可声明 `sample_validation.weighting`，从样本 `metadata_json` 的指定顶层字段读取正有限权重，检查总权重和有效样本量下限，并输出加权混淆矩阵、OA、Precision、Recall、F1、IoU、权重总量和有效样本量；权重缺失/非正/非有限或覆盖不足时失败，不把未加权区间冒充加权设计区间。 | `backend/tests/test_validation_statistics.py` 覆盖加权指标和非法权重；`backend/tests/test_python_runner.py` 覆盖真实 API→Runner→manifest 的加权正式运行。 |
| C-07 | 正式点样本验证可声明 `sample_validation.area_adjustment`，从样本 `metadata_json` 的指定顶层字段读取分层标识和正有限分层面积，检查期望分层覆盖、分层面积一致性和最低分层数，并输出分层面积调整混淆矩阵、OA、Precision、Recall、F1、IoU、参考/预测正类面积和面积绝对误差；缺失、非法、面积不一致或与通用 weighting 同时启用时失败，不伪造设计型置信区间。 | `backend/tests/test_validation_statistics.py` 覆盖面积调整结果、分层覆盖、非法面积和设计混用拒绝；PythonRunner manifest/前端研究页展示面积调整设计边界。 |

### Gate D — 研究证据包、开放研究与容器化

| ID | 必须满足的行为 | 证明方式 |
|---|---|---|
| D-01 | 开放研究模式可从自然语言或数据起步，并在正式运行前生成可编辑、可冻结的研究协议。每个实验必须记录协议快照及指纹，正式运行/证据包不得因后续项目编辑而改变历史协议。 | 端到端演示和 API/前端测试，以及证据包中协议快照与指纹的完整性检查。 |
| D-02 | 每个正式运行生成版本化 Research Evidence Package，包含协议、数据引用、方法证据、代码/环境、日志、图件、验证和结论边界；研究页可下载 ZIP，并通过校验接口检查外层 SHA-256、内部 `checksums.sha256`、运行清单摘要、生成产物摘要和隐私声明。 | 导出包结构与完整性检查；`backend/tests/test_research_evidence_package.py` 覆盖正常包与篡改包，PythonRunner 集成测试覆盖正式 Run→verification API。 |
| D-03 | 导出默认不复制私有原始数据，仅保留受保护引用、校验值与访问说明。 | 包内容审计测试。 |
| D-04 | 在 PythonRunner 稳定后，Docker Compose 能启动前端、API、worker 与持久化存储；API `/api/health` 提供 liveness，`/api/ready` 在数据库和独立 worker 心跳均可用时返回 ready；worker 容器 healthcheck 只在 heartbeat 为 `running` 且未过期时通过，Web 等待 API/worker 均 healthy；密钥不进入镜像或 Git。 | 干净环境启动、两个 health/readiness 端点、worker 过期心跳测试与秘密扫描。 |
| D-05 | 内部多用户部署前，项目成员、共享范围和对象级授权全部经越权测试验证。V1 不按教师/学生区分日常工作权限；受邀成员共同工作，项目创建者只管理成员名单。 | 集成安全测试和部署评审。 |

#### D-01 当前迭代：协议快照与可复现性子门槛

| ID | 可观察的验收标准 | 验证方式 |
|---|---|---|
| D-01a | 不选模板的用户可由至少 8 个字符的研究问题生成本地可编辑协议草案；草案不写回项目，也不查询外部服务、RAG 或模型。 | 项目路由集成测试覆盖字段、未持久化和未受邀访问拒绝。 |
| D-01b | 每个新建实验保存项目协议的规范化 JSON 快照及其 SHA-256；正式实验必须先有至少 8 个字符的 `research_question` 或兼容的 `question`。 | API 响应与正/反向集成测试。 |
| D-01c | 正式运行在执行前重新校验已保存快照及指纹；正式证据包中的 `project_protocol.json` 和包清单必须来自实验快照，而非运行时项目当前协议。 | 创建实验后修改项目协议，再实际运行并检查 ZIP 内容。 |
| D-01d | 既有 SQLite 数据库启动时能增补实验协议字段；无法证明已冻结协议的旧正式实验须被诚实拒绝重新创建，而不是悄悄使用后来的项目协议。 | 预建旧表结构的迁移测试及运行前置条件测试。 |
| D-01e | 预览实验仍可在尚无完整研究问题时建立和运行，不因正式研究门槛被误阻断。 | 预览 API/Runner 回归测试。 |
| D-01f | 每次保存内容不同的项目协议都会产生项目内单调递增的版本、规范化 JSON 与 SHA-256；只改项目名称/说明、或重复保存相同协议，不产生伪版本。 | API 集成测试与数据库约束检查。 |
| D-01g | 项目创建者和成员可查看协议版本历史；未受邀用户按项目对象级授权被拒绝。 | 成员/未受邀 API 集成测试。 |
| D-01h | 新正式实验必须关联当前已保存协议版本，响应、实验快照和证据包都保留该版本 ID；项目稍后产生新版本时不改写旧实验。 | 创建正式实验、再次保存协议、实际运行并检查 ZIP/manifest 的集成测试。 |
| D-01i | 用户可选择只查询已显式绑定的项目文本 RAG，生成带 chunk 引用的未持久化协议证据映射；每项固定为 `unverified`，不会自动创建/升级 EvidenceCard、保存协议、访问 Data Catalog 或形成研究结论。未绑定资料和未受邀用户路径必须可证明。 | 项目 RAG 集成测试覆盖无绑定、成员检索、引用保留、无持久化与未受邀拒绝。 |
| D-01j | 用户可以对已保存协议运行只读就绪检查，得到带路径和修复说明的缺项列表；完整协议返回 `ready=true`、当前版本 ID 和指纹。检查不阻断预览，未受邀用户不能查看。 | API 集成测试覆盖缺项、完整协议、版本/指纹和未受邀拒绝。 |
| D-01k | 外部文献助手可选择 Crossref、OpenAlex 或 Semantic Scholar 公开元数据发现；仅发送关键词、数量和固定字段，记录 provider/查询审计，候选不会自动进入 RAG 或变成已核验证据。 | 外部 HTTP 客户端伪造响应测试、三类字段解析测试、provider 越界拒绝和候选导入审计测试。 |
| D-01l | 用户明确选择已绑定的 `method` 知识库后，可将当前审计候选的公开元数据和摘要作为 `abstract_only` Markdown 记录排队进入项目 RAG；伪造/跨项目候选、未绑定知识库、无摘要、重复内容和未受邀用户必须拒绝。记录保留 DOI/URL、provider、外部 ID 和 audit ID，全文和许可仍需用户核对后手动上传。 | `backend/tests/test_research_literature_search.py` 覆盖绑定、排队、实际索引、项目 RAG 引用、重复导入、审计候选伪造和未受邀路径。 |
| D-01m | 对已完成审计的 Semantic Scholar 候选，用户可按页展开 `references` 或 `citations`；请求只携带已审计的公开 paper ID、关系、分页和固定字段，结果创建独立审计并保留来源审计 ID。扩展结果仍是候选，不自动进入 RAG、EvidenceCard 或 `verified`；非 Semantic Scholar、伪造候选、跨项目/未完成审计、越界 paper ID 必须拒绝。 | `backend/tests/test_research_literature_search.py` 覆盖关系 URL/字段、分页、key 不泄露、候选复用及拒绝路径。 |
| D-01n | 运行详情页同时展示阶段 PNG、CRS、栅格尺寸、NoData/有效像元、阈值、公式操作和冻结快照指纹；每张图可单独下载，正式证据包仍可整体下载。 | PythonRunner 运行测试检查 manifest 中的栅格/快照元数据；前端 TypeScript/Vite 构建通过，研究页使用运行输出接口加载并下载 PNG。 |
| D-01o | 研究运行支持显式 `mode=queue`：API 先返回 `queued`，内嵌或独立 worker 原子 claim 后执行 PythonRunner，最终持久化 `completed`/`failed`/`unavailable` 与产物；研究页提供“立即运行”和“加入后台队列”，queued/running 时自动刷新。 | `backend/tests/test_research_run_queue.py` 覆盖队列响应、worker claim、manifest 和阶段产物；完整后端回归与前端构建。 |
| D-01p | worker 在启动或轮询队列时会回收超过 `IDLRAG_RESEARCH_RUN_TIMEOUT_MINUTES` 仍为 `running` 的**队列来源**研究运行，将其持久化为 `failed`、记录可操作错误并同步实验状态，避免进程中断后永久卡住；立即运行模式不会被 worker 抢占或误回收。 | 队列集成测试人为制造过期队列 `running` 记录并断言终态、错误信息和实验状态；同步运行与配置边界由回归测试覆盖。 |
| D-01q | 用户可以取消 `queued` 运行；对 `running` 运行提交协作式取消请求，worker 在当前步骤结束后持久化 `cancelled` 并保留已有产物。已完成、失败、不可用或已取消的运行可以创建独立重试 Run，保留 `retry_of_run_id` 关系；不能取消或重复重试仍在执行的 Run。 | `backend/tests/test_research_run_queue.py` 覆盖 queued 取消、running 取消请求、超时取消收尾、重试链、终态取消拒绝；研究页提供取消/重新排队按钮，前端构建通过。 |
| D-01r | 正式运行生成的 Evidence Package 可由拥有项目权限的成员显式校验；校验只读取包和运行产物，不读取或外发原始私有影像。完整性失败必须返回可操作问题，不能显示为“已验证”。 | `backend/tests/test_research_evidence_package.py` 覆盖有效包和篡改产物；研究页提供“校验证据包”按钮并显示通过/失败详情。 |
| D-01s | 同一 formal 实验的两个 `completed` Run 可由拥有项目权限的成员按绝对/相对容差比较；协议/公式/快照/参数/执行器上下文不一致、缺失产物、CRS/网格/掩膜不一致或超差像元必须明确失败。比较只读取运行产物，不读取或外发原始私有影像；preview 和 parameter sweep 不得作为正式重跑证据。 | `backend/tests/test_research_reproducibility.py` 覆盖精确一致、容差内、超差和冻结上下文不一致；`backend/tests/test_python_runner.py` 覆盖两次真实 formal Run→API 比较；研究页提供重跑基准选择与差异提示。 |
| D-01t | formal Python Run 自动生成确定性的 review-first Markdown 报告，记录研究问题、冻结上下文、验证摘要、图件/数值产物、限制和结论边界；报告不包含私有原始 URI/凭据，不自动宣称科学优越性，并进入 Evidence Package。研究页可下载报告。 | `backend/tests/test_research_report.py` 覆盖不同 run token 下报告摘要一致、研究问题、限制和隐私边界；`backend/tests/test_python_runner.py` 覆盖 formal Run 和 ZIP 内报告；前端构建覆盖可下载非 PNG 产物。 |
| D-01u | 同一项目内两个 `completed formal` Run 可作为基线/候选比较：必须使用同一冻结 DataSnapshot 和完全相同的验证方案，但允许 FormulaSpec 不同；系统输出共同 OA、Precision、Recall、F1、IoU 及“候选 − 基线”delta。preview、parameter sweep、快照/验证设计不一致或没有共同有限指标时必须拒绝，并明确 delta 不代表统计显著性或科学优越性。 | `backend/tests/test_research_run_comparison.py` 覆盖正常 delta、快照/验证方案不一致和 preview 拒绝；`backend/tests/test_python_runner.py` 覆盖真实 formal Run→comparison API；研究页提供基线选择和指标差异表。 |
| D-01v | Agent 可绑定一个研究项目，在成员权限范围内只读查看安全项目摘要、数据目录、运行摘要和证据包校验，检索项目显式绑定的 Method/IDL/Python RAG、生成未持久化协议草案并读取就绪检查；外部文献搜索必须由用户显式允许，且只发送查询词，不发送影像、快照、URI 或凭据。研究 Agent 只可在另一个明确授权下创建/排队 Python `preview`，不能运行 formal/IDL、修改协议/公式或导入资料。 | `backend/tests/test_research_agent_tools.py` 覆盖成员/未授权隔离、目录脱敏、preview 创建/排队、未持久化草案、外部搜索同意门槛和 Agent 流工具步骤；聊天页提供研究项目选择器及独立授权开关，前端构建通过。 |
| D-01w | Agent 只有在聊天页同时获得“允许 Agent 预览执行”授权、用户明确要求、工具参数 `confirm=true` 且资源属于当前项目时，才能创建 Python `preview` 计划或把已有 Python `preview` 实验加入队列；不能创建/执行 formal、parameter sweep 或 IDL，不能修改公式/协议或读取原始路径。 | `backend/tests/test_research_agent_tools.py` 覆盖 preview 创建/排队边界、confirm 和授权门槛；实际队列持久化、原子 claim/取消/重试由 `backend/tests/test_research_run_queue.py` 覆盖；前端提供独立授权开关。 |
| D-01x | Agent 只有在聊天页单独打开“允许 Agent 获取 GEE”、用户明确要求获取、工具参数 `confirm=true` 且项目成员权限有效时，才能按现有 GEE 白名单、bbox、尺度、波段、日期和下载限制获取数据并登记私有 DataAsset；返回只包含资产摘要，不暴露私有 URI/像元，不自动冻结 DataSnapshot 或创建实验。 | `backend/tests/test_research_agent_tools.py` 覆盖 consent、confirm、参数传递和 URI 脱敏；实际 GEE 服务的白名单/下载/资产登记由 `backend/tests/test_research_gee.py` 覆盖；聊天页提供独立 GEE 授权开关。 |
| D-01y | 项目成员可通过专用接口上传私有 `.pro` 脚本；脚本不会进入 DataSnapshot，IDL 实验必须显式引用同项目的脚本资产和合法入口。Runner 只调用配置的 `IDLRAG_IDL_EXECUTABLE -batch`，把快照资产复制到受限 `inputs` 目录，通过固定环境变量提供输入/输出约定，实施超时、输入/输出数量与大小、符号链接和路径边界校验；可用 `parameters.idl_prediction_output_file` 声明受限目录内的预测 GeoTIFF（默认 `water_mask.tif`），若验证方案声明参考栅格或独立点样本，则复用 PythonRunner 的同一验证、误差图和指标输出；成功 Run 生成 IDL manifest、日志、图件并可进入正式报告/证据包，缺少脚本或许可节点时返回 `unavailable` 且不影响 PythonRunner。 | `backend/tests/test_research_idl_runner.py` 覆盖脚本上传、非 `.pro` 拒绝、脚本不得进入快照、入口和预测文件名校验、沙箱输出、参考栅格/点样本验证、验证图件下载和缺少运行时的 `unavailable`；`backend/tests/test_python_runner.py` 保留旧 IDL 不可用回归；真实受许可 IDL/ENVI 节点仍需现场验收。 |

本轮文献搜索扩展：除 Crossref/OpenAlex 外，研究页面与 API 已接入 Semantic Scholar 公开 paper search，并可从已审计候选展开引用/参考文献关系；三类 provider 均记录出站字段、结果审计和候选快照，并复用相同的人工核验与摘要级 Method RAG 导入边界。关系扩展同样只返回候选，不自动形成研究结论。

Semantic Scholar 的可选 key 只允许通过 `x-api-key` 请求头注入，并由进程内最小间隔保护请求；429/超时/5xx 会留下 `failed` 审计并返回可重试的 503，不会把 key 写入日志、候选快照或 RAG 文档。

## 3. 首个真实研究模板的最终验收

“云遮挡条件下 Sentinel-1/Sentinel-2 多源协同的 10 m 季节性水体制图——鄱阳湖”仅在下列所有条件满足时视为完成：

- [ ] 2018–2025 数据范围、ROI、产品版本、预处理和样本来源均作为版本化资产保存。
- [ ] M1 光学、M2 SAR、M3 可解释融合、M4 公式候选在相同的可追溯数据条件下可比较。
- [ ] 开发/模型选择/最终测试在时间和空间上独立；最终测试没有参与调参。
- [ ] 所有空间处理阶段都有规定的影像证据、图例、数值范围、参数和统计摘要。
- [ ] 正式结论如实报告改进、无差异或退化，不能只展示最佳案例。
- [ ] 证据包可在相同环境中重跑，结果在预先记录的数值容差内一致。
- [ ] 如有 IDL 基线，对照结果及其差异均被保留；如没有 IDL 许可，不影响 Python 主结果。

## 4. 当前实现进度（2026-09-29）

以下是已由自动化运行验证的实现状态，**不是**对尚未满足条目的完成宣告。

| 范围 | 已验证实现 | 尚未通过的边界 |
|---|---|---|
| Gate A | 私有项目、资产登记/上传、冻结快照、EvidenceCard、FormulaSpec 与正式实验前置条件均由 `backend/tests/test_research_workflow_routes.py` 覆盖。本地上传和受控 GEE 获取都可直接登记为当前项目的私有 DataAsset。项目创建者还可按用户名邀请成员；成员共同读写项目，未受邀用户无法读取，而创建者可移出成员。 | 尚未提供按学校/企业组织、项目组或目录策略批量管理成员的能力。 |
| Gate B（部分） | PythonRunner 已实际运行 M1 归一化差分阈值、M2 SAR VV 阈值、M3 可解释的光学—SAR `or/and` 融合；M3 会拒绝未对齐栅格，并输出不一致不确定性图。M4 已有受控的全局 Otsu 指数阈值候选，记录范围与直方图参数且不读取验证标签；还可运行 `safe_band_math_threshold`，以 AST 白名单解释器在同一栅格的已声明波段和有限参数上计算研究者提出的反射率/指数候选，拒绝任意 Python、属性/下标、未知名称/函数和验证标签访问。它会输出输入预览、特征与分类 GeoTIFF/PNG，并把表达式、波段变换和参数写入 manifest；定向测试已覆盖数值等价、参数覆盖、非法语法拒绝、正式验证和证据包。preview 实验还可把多个候选参数隔离到 development/model_selection 样本上比较并保存排名、失败原因和图件，但不会触碰 independent_test 或自动冻结公式。IDL 缺失时明确报告不可用，不影响 Python。研究者还可将本机 IDL/ENVI 输出登记为私有 `derived` GeoTIFF，在实验创建前声明与 Python 输出的分类或连续变量对照；现在还可通过专用 `.pro` 上传接口登记项目脚本，在 IDL 实验中显式引用脚本、把快照输入放入受限目录并收集日志/图件/manifest；脚本不能进入数据快照，缺少许可时运行明确为 `unavailable`。系统会拒绝 CRS/网格不一致，报告 NoData 不一致，输出分类一致率/F1/IoU/面积像元差或连续变量的 MAE/RMSE/最大差/容差通过率，并把差异 GeoTIFF、PNG、JSON 与 Run manifest 纳入正式证据包。 | 真实受许可 IDL/ENVI 版本仍需现场核验输入读取、输出命名、过程约定和性能；当前自动化使用受控 subprocess mock，不代表本机已安装或已授权 IDL。M4 尚未包含按季节、云量或局部统计自适应的研究变体，也尚未对真实研究数据得出物理正确性或效果优于基线的结论。 |
| Gate C（部分） | 当冻结快照登记 `validation_plan.reference_asset_id`（共网格的 0/1 参考栅格）时，Runner 会输出混淆矩阵、OA、Precision、Recall、F1、IoU、面积像元差、TN/TP/FP/FN GeoTIFF 与 PNG，并输出标记为 `wilson_95_unweighted` 的 OA/Precision/Recall/IoU 95% 区间。点样本验证还支持声明权重字段，输出权重覆盖和加权指标；也支持与通用 weighting 互斥的 `sample_validation.area_adjustment`，按样本元数据中的分层面积输出面积调整混淆矩阵、面积 OA/Precision/Recall/F1/IoU、参考/预测正类面积和面积绝对误差，并检查期望分层覆盖、面积一致性和最低分层数；面积调整结果明确标记为未报告设计型置信区间。项目内也可逐条登记，或以受限 UTF-8 CSV 原子批量导入人工验证样本；来源快照/资产、经纬度、观测时间、标注人、置信度、开发/模型选择/独立测试划分、空间块、时间分层和冲突状态均被持久化，导入解析失败不会留下部分样本，并经拥有者隔离测试覆盖。正式实验创建时强制声明时空留出和独立参考来源；点样本方案必须预先声明总体、空间块、时间分层及每个空间块/时间分层的最低样本覆盖，Runner 只会选择绑定当前冻结快照、满足独立测试/置信度/无冲突规则的 WGS84 样本，并会实际拒绝未达到任何下限的结果。通过后才输出总体、时间分层和空间块指标；标签不参与公式或阈值计算。 | 下限数值的科学合理性、抽样权重、空间相关性、误差调整面积和设计一致的置信区间仍需根据真实研究方案与人工审查确定；当前 Wilson 区间不是复杂抽样设计的替代品，加权结果及当前面积调整输出都不是复杂抽样设计推断的替代品，真实研究仍需人工审查和设计一致的方差估计。 |
| Gate D（部分） | 每一次完成的正式 Python 运行自动生成受保护下载的 `research_evidence_package.zip`，内含实验创建时冻结的项目协议版本 ID/JSON/SHA-256、冻结快照的哈希与受保护引用、公式、证据卡、环境与运行记录、全部生成图件和校验清单；包不含私有原始影像、原始 URI 或凭据。内容不同的协议保存会产生项目内递增版本，成员可查看历史，重复保存/纯说明修改不会制造版本；正式实验要求已保存研究问题和可追溯版本，创建后项目协议的编辑不会改写实验或包内历史协议。开放研究页可以从至少 8 个字符的自然语言研究问题生成一份**本地、未持久化、可编辑**的协议草案；该动作不读取项目资产，也不调用外部检索、RAG 或模型。另一条受控路径可只查询成员已显式绑定的项目文本 RAG，并把返回 chunk 引用以 `unverified` 状态写入未持久化的协议证据映射；它不读取 Data Catalog、不会保存协议或自动创建/升级 EvidenceCard，测试覆盖未绑定、成员、引用保留、无持久化和未受邀拒绝。研究协议页还提供只读就绪检查，逐路径报告 ROI、日期、冻结快照、可信证据、时空验证、空间分块、图件契约和结论边界缺项；完整协议返回版本 ID/指纹，预览不受阻断。外部文献助手可选择 Crossref 或 OpenAlex，记录 provider、查询和结果数审计；候选仍不会自动进入 RAG 或变成已核验证据，但用户明确选择已绑定的 `method` 知识库后，可将带公开摘要的候选以 `abstract_only` Markdown 排队、索引并在项目 RAG 中返回带引用的结果。公开 STAC 助手只向配置白名单的公共端点发送集合、bbox、日期、云量和数量，记录查询审计；研究者必须再次提交原候选和 asset key 才能登记远端引用，或显式下载为经大小/超时/GeoTIFF 校验、可选 bbox 裁剪/重采样和 SHA-256 保护的私有 `research://assets/` 栅格。只有私有下载资产可创建快照并进入 Runner；远端引用仍标记为 `stac_reference_only`。成员可将自己拥有的 Method/IDL/Python 文本知识库显式绑定到项目并共享检索；遥感资产仍不进入向量库。简单的按用户名项目协作已实现，并覆盖未受邀访问拒绝、成员协作和移出后拒绝。 | 当前证据映射、就绪检查和外部元数据发现都是研究者审查辅助，不是自动文献综述、RAG/LLM 研究设计或完整正式协议状态机；就绪检查也不代替正式实验对具体参考资产、点样本最低覆盖和指标的校验。OpenAlex/Crossref 只返回公开元数据；全文获取、版权/许可确认、人工核验以及把候选文献导入合适的项目 RAG 知识库仍需由用户明确完成；当前摘要导入不替代全文许可核验。STAC 下载路径已由合成 GeoTIFF 集成测试覆盖，但真实公共对象是否允许匿名下载、签名 URL 是否过期仍需在部署环境做现场验收。Docker 镜像真实启动、任务队列和学校/企业组织目录部署也尚未完成。 |

**自动化证据（加入 STAC 下载、裁剪、SAS 授权、COG Range、栅格对齐、文献摘要 RAG 导入与私有 GeoTIFF 登记后）：** `uv run --project backend pytest backend/tests -q` 已完成 `99 passed, 620 warnings in 71.47s`；`uv run --project backend ruff check backend/app backend/tests` 通过；`npm run build --prefix frontend` 通过（Vite 提示单一入口包约 1.32 MB、gzip 412.66 kB，属于部署优化提醒）。按根目录文档启动 `uv run --project backend uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8765` 后，`GET /api/health` 返回 200；`npm run dev --prefix frontend -- --host 127.0.0.1 --port 5173` 后首页返回 200 且包含应用标题。`backend/tests/test_research_raster_stack.py` 会把合成的异分辨率资产串到 DataSnapshot → MNDWI FormulaSpec → PythonRunner，并断言 input/feature/classification 栅格和 PNG 产物；`backend/tests/test_research_literature_search.py` 会把审计候选摘要排队、索引并通过项目 RAG 返回引用。另以 Microsoft Planetary Computer 的匿名 STAC 查询和真实 Sentinel-2 B01 COG 做了现场探针：约 7.32 MB 原始 GeoTIFF 经 SAS 授权下载后，WGS84 bbox 裁剪得到 EPSG:32650、82×93 的私有输出；B03 10 m 原始对象超过 100 MB 时，指定 crop_bbox 后改走 COG HTTP Range，真实 API 下载得到 EPSG:32650、492×558 的私有 B03 输出；没有 crop_bbox 时仍按上限拒绝。随后以同一 Sentinel-2 场景的 B03/B11 运行了真实 API 级搜索 → 两波段下载（B03 为 `cog_http_range`，B11 为 `full_download`）→ 参考网格对齐 → DataSnapshot 冻结 → MNDWI FormulaSpec → PythonRunner 预览：输出 EPSG:32650、9×11、2 波段 float32 GeoTIFF，并生成 input/feature/classification 栅格与 PNG、manifest，Run 返回 `completed`。这证明了真实数据链路和工程闭环，不代表已经有独立样本支持的科学结论。前端构建仍提示现有单一入口包超过 500 kB，这不是功能失败，但在部署前应采用路由懒加载或手工分包。

**最终复跑记录（2026-09-29，加入 Semantic Scholar 关系扩展、影像元数据展示、独立索引 worker、研究运行队列、超时回收、同步/队列 claim 隔离、真实 worker 主循环、取消/重试、验证置信区间、样本设计加权、分层面积调整、开发参数候选实验及其队列/取消/重试、证据包完整性校验、formal Run 重跑一致性比较、review-first 研究报告、formal 基线/候选指标比较、研究 Agent 项目上下文工具、用户确认后 preview 排队动作、用户确认后 GEE DataAsset 获取动作、项目级 IDLRunner、IDL 预测 GeoTIFF 保护与共享验证契约、API readiness、worker 配置隔离修复、IDL 入口类型拒绝、共享 worker heartbeat healthcheck 和真实 IDL opt-in 探针后）：**默认 `uv run --project backend pytest backend/tests -q` 返回 `127 passed, 1 skipped, 458 warnings in 92.36s`（跳过项是未设置本机 IDL 可执行文件时的 opt-in 现场测试）；Workbench/ENVI GUI 与 `idlrt` 入口的拒绝行为、worker heartbeat 的 fresh/stale/invalid 分支由测试覆盖；在本机设置真实命令行 IDL executable 后，严格 `real_local_probe` 仍需许可/ENVI batch 现场验证，不能仅以进程退出记为通过；`uv run --project backend ruff check backend/app backend/tests` 通过；`npm run build --prefix frontend` 通过，Vite 单入口包 `1,346.34 kB`（gzip `418.99 kB`）仅为分包优化提醒。本记录覆盖上方早先的自动化计数。

Docker 部署静态验收：使用 `IDLRAG_AUTH_SECRET=... docker compose config` 通过，API/worker/Web 三服务、API liveness 健康检查、独立 `app.index_worker` 命令、共享命名卷、worker heartbeat healthcheck、Web 等待 API/worker healthy 的依赖关系和 `/api` 代理配置均被解析；秘密扫描确认 Dockerfile、`.dockerignore` 和镜像构建上下文不会包含 `.env` 或运行时数据。当前主机的 Docker Desktop Linux daemon 未启动（Docker client 报告 `//./pipe/dockerDesktopLinuxEngine` 不存在），因此镜像构建、容器 health check、Web→API 代理、worker 心跳和重启持久化仍待在 Docker daemon 可用的干净环境现场验收。

真实科学案例验收：已用 `uv run --project backend python backend/scripts/run_real_research_case.py` 完成一条真实公开数据链路：Planetary Computer Sentinel-2 B03/B11 + Sentinel-1 RTC VV（线性功率转换到 dB）+ ESA WorldCover class 80 代理参考 → 10 m 同网格对齐 → MNDWI 与 SAR threshold formal PythonRunner → 阶段影像、验证指标、研究报告、formal comparison 和两份证据包。修复 EPSG:4326 目标分辨率单位、SAR 栅格对齐、派生资产输入类型和线性功率/dB 单位问题后，两个 Run 均返回 `completed`，证据包均 `verified=true`，comparison `comparable=true`；光学 OA/F1/IoU 为 0.940478/0.723817/0.567174，SAR OA/F1/IoU 为 0.913649/0.408235/0.256467。该案例明确标注 WorldCover 2021 不是 2024 同期现场真值，单 ROI 不支持湖泊级泛化；真实案例及问题闭环记录在 [`docs/real-research-case-poyang.md`](real-research-case-poyang.md) 和 [`docs/real-case-issues.md`](real-case-issues.md)。

IDL 现场探针：本机发现 `D:\envi5.6\ENVI56\IDL88\bin\bin.x86_64\envi_idl.exe` 与 IDL 8.8.0；它是 Workbench 入口，直接 `-batch` 可以退出但没有执行项目脚本。严格项目探针要求生成 `idl_probe.tif`，当前实际 Run 没有产生该文件；Runner 已按完整性契约拒绝空结果。相同目录的 `idlde.exe` 在无界面探针中也没有提供可验证的 `.pro` 执行，`idlrt.exe` 是 SAV-only runtime，不能作为项目 `.pro` 入口。改用候选命令行 `idl.exe` 后，现场 Run 返回 `Failed to initialize IDL instance`，同样没有生成 `idl_probe.tif`，系统把它归类为 `unavailable`。官方 IDL 文档将 `-batch` 定义为执行 batch file，ENVI Classic 还要求在脚本中恢复 ENVI save files 并调用 `ENVI_BATCH_INIT`；因此下一次现场验收必须在有有效 IDL/ENVI 授权的节点上完成 batch 初始化、输入读取和带空间参考的预测 GeoTIFF 验证。

真实现场探针的场景、处理链、输出元数据和“工程验证不等于科学结论”边界单独记录在 [`docs/real-data-validation-record.md`](real-data-validation-record.md)。

## 5. 验收记录格式

每个 Gate 完成时，在对应 PR/变更记录中至少写明：

```text
验收项：A-01, A-02
实现位置：路径和主要 API/服务
自动化证据：执行命令、测试名称、结果摘要
人工证据：截图/Run ID/导出包路径（如适用）
未覆盖项：明确说明没有覆盖的边界及后续 Gate
```

严禁将未实现的 Gate 标为通过，也不能以“文档已写好”替代运行、可视化、数据隔离或可复现性证据。
