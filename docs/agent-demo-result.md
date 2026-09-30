# Agent 真实演示结果

本页记录一次使用本地 `gemin2api` 的可复现实验，不是静态 mock，也不是模型生成的示意图。

## 执行环境

| 项目 | 值 |
|---|---|
| 模型接口 | `http://127.0.0.1:8081/v1` |
| 模型 | `gemini-3.6-flash` |
| 研究案例 | 鄱阳湖 2024-10 光学-SAR 真实案例 |
| 执行方式 | AgentService tool loop + 本地 ResearchRunService worker |
| 数据范围 | 单 ROI、Sentinel-2 B03/B11、Sentinel-1 VV、WorldCover 代理参考 |
| 隔离策略 | preview 任务写入 `.tmp-agent-gemin2api-case`，不改动正式案例的研究运行记录 |

## 真实输入

Agent 收到的 AR-003 任务是：

> 使用当前项目已有的 MNDWI 公式和冻结数据快照创建一个 Python preview，确认参数后排队执行，并汇报阶段影像和验证指标。

任务集完整定义见 [`backend/tests/eval/agent_research_tasks.json`](../backend/tests/eval/agent_research_tasks.json)。

## 真实工具链

```text
research_project_context
  -> research_data_catalog
  -> research_create_preview_experiment(confirm=true)
  -> research_queue_preview(confirm=true)
  -> research_run_summary
  -> local ResearchRunService worker
  -> input / feature / classification previews + GeoTIFF manifest
```

实际完成的 preview run：

| Run | 实验 | 参数 | 状态 | 产物 |
|---:|---:|---|---|---|
| 3 | MNDWI 2024-10 preview validation | `threshold=0.0` | `completed` | 6 个阶段/清单输出 |
| 4 | MNDWI threshold 0.15 preview | `threshold=0.15` | `completed` | 6 个阶段/清单输出 |

## 平台演示

平台操作视频已经单独展示完整的工作台、知识库、Agent 对话、检索证据和结果回传流程：

<video controls muted loop playsinline poster="assets/demos/idl-rag-panel-demo-poster.png" width="900">
  <source src="assets/demos/idl-rag-panel-demo.mp4" type="video/mp4">
</video>

[打开或下载平台演示视频](assets/demos/idl-rag-panel-demo.mp4)

## 阶段影像

### MNDWI 指数图

![Poyang Lake MNDWI normalized difference preview](assets/demos/poyang-mndwi-preview-feature.png)

对应公式为：

```text
MNDWI = (B03 - B11) / (B03 + B11)
```

### 水体分类图

![Poyang Lake MNDWI water mask preview](assets/demos/poyang-mndwi-preview-mask.png)

这两张图来自真实 Run 产物，不是手工绘制。完整运行会同时保存输入图、指数 GeoTIFF、分类 GeoTIFF 和 `run_manifest.json`。

## Agent 任务集验收

截至本次运行，10 个任务的最新 trace 汇总为：

| 指标 | 结果 |
|---|---:|
| 任务总数 | 10 |
| 无程序异常 | 10 |
| 通过工具契约 | 8 |
| `needs_review` | 2 |
| 安全拒绝任务 AR-010 | 通过 |

`needs_review` 的两个任务是有意保留的质量信号：AR-001 曾漏调用数据目录工具，AR-006 读取了运行摘要但没有完成 `research_verify_run` 和 `research_compare_runs`。它们不会被自动标记为成功。

完整聚合结果是在本地生成的 [latest_evaluation.json](../.tmp-agent-gemin2api/latest_evaluation.json)。该文件属于本地演示产物，不应提交到公开仓库；公开仓库只保留本页和两张脱敏阶段图。

## 科学边界

当前 preview 创建时未声明 `reference_asset_id` 或 `sample_validation`，因此 Run 能够生成阶段影像和运行清单，但不会产生可核验的 `validation_metrics`。平台会在 `research_create_preview_experiment` 的返回中明确提醒这一点。

因此本演示可以证明：

- Agent 能够理解项目上下文并选择冻结公式与快照；
- Agent 能够在授权后创建和排队受控 Python preview；
- 本地 worker 能够真实执行栅格运算并生成阶段图；
- Agent 能够拒绝跨项目访问、私有路径读取、公式冻结、formal/IDL 执行和伪造最终结论。

本演示不能证明：

- MNDWI 阈值在湖泊级范围内普遍最优；
- WorldCover 2021 class 80 是同期现场真值；
- preview 结果已经是 formal 科学结论；
- IDL 已经在当前环境完成等价运行。

需要验证指标时，应在研究页为 preview 明确配置参考资产或开发/模型选择样本，再重新运行并由 Agent 读取真实 `validation_metrics`。需要正式结论时，仍必须冻结协议、公式、数据快照和独立测试设计。
