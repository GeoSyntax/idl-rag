# 真实研究案例：鄱阳湖 Sentinel-2 MNDWI 与 Sentinel-1 VV（WorldCover 代理参考）

> 记录日期：2026-09-30  
> 记录性质：真实公开数据的端到端研究工作流验收；不是现场真值精度论文，也不支持对全鄱阳湖或多季节的泛化结论。

## 1. 研究问题和结论边界

本案例回答一个有限、可复核的问题：

> 在鄱阳湖一个 `0.10° × 0.10°` 的小区域内，2024-10-05 的 Sentinel-2 L2A B03/B11 MNDWI 与同月 Sentinel-1 RTC VV 阈值基线，相对 ESA WorldCover 2021 class 80（水体）代理参考的一致程度有何差异？

固定阈值公式为：

```text
MNDWI = (B03_green - B11_SWIR1) / (B03_green + B11_SWIR1)
water = MNDWI > 0.0
```

SAR 基线先把 Planetary Computer Sentinel-1 RTC 的线性功率转换为 dB：

```text
VV_dB = 10 * log10(VV_linear)
water = VV_dB <= -17.0
```

必须把结果解释为“与 WorldCover 代理层的一致性”，而不是现场真值精度。WorldCover 不是 2024-10-05 的同期人工标签，且本案例只有一个小 ROI；因此本次结果不能证明：

- MNDWI 优于 SAR 或多源融合；
- 阈值 0.0 适合整个鄱阳湖、其他季节或其他传感器；
- 与 WorldCover 的一致性等于现场调查精度；
- 候选公式已经优于基线。

## 2. 实际数据链路

| 项目 | 本次记录 |
|---|---|
| STAC 服务 | Microsoft Planetary Computer |
| Sentinel-2 集合 | `sentinel-2-l2a` |
| Sentinel 场景 | `S2A_MSIL2A_20241005T025601_R032_T50RMT_20241005T061609` |
| 场景时间 | `2024-10-05T02:56:01.024Z` |
| 报告云量 | `0.021916%`（STAC `eo:cloud_cover`） |
| 波段 | B03 10 m、B11 20 m |
| 研究区 | WGS84 `[115.95, 28.95, 116.05, 29.05]` |
| 参考集合 | `esa-worldcover` |
| 参考项 | `ESA_WorldCover_10m_2021_v200_N27E114` |
| 参考类别 | class 80 → 二值水体代理 |
| Sentinel-1 集合 | `sentinel-1-rtc`，VV asset；原始值为线性功率 |
| SAR 预处理 | VV 重投影到 Sentinel-2 10 m 网格，并转换为 dB；阈值 `-17 dB` |
| 运行器 | PythonRunner（Python 3.12.11、Rasterio 1.5.1、NumPy 2.4.4） |
| 输出网格 | EPSG:32650，10 m，755 × 1116 |

执行脚本：

```powershell
uv run --project backend python backend/scripts/run_real_research_case.py
```

脚本在隔离目录 `.tmp-real-poyang-case` 中完成：

1. STAC 搜索并保存审计候选；
2. 使用 Planetary Computer SAS 读取 B03、B11 和 WorldCover COG；
3. 在指定 bbox 内裁剪并保存私有 `research://assets/` 栅格；
4. 以 B03 为参考网格对齐 B11；
5. 将 Sentinel-1 VV 重投影到同一网格，并显式执行线性功率到 dB 的转换；
6. 将 WorldCover class 80 显式转换为二值参考，并重投影到 Sentinel 网格；
7. 冻结 DataSnapshot、EvidenceCard、FormulaSpec 和正式 Experiment；
8. 分别运行光学和 SAR PythonRunner，生成阶段影像、误差图、报告和证据包；
9. 通过 formal comparison 在相同冻结快照下比较两次运行；
10. 通过证据包完整性接口校验外层 SHA-256、内部 checksums、manifest 和输出摘要。

## 3. 真实运行结果

| 指标 | 结果 |
|---|---:|
| 有效像元 | 842,580 |
| TP | 65,719 |
| TN | 726,709 |
| FP | 25,386 |
| FN | 24,766 |
| Overall Accuracy | 0.940478 |
| Precision | 0.721354 |
| Recall | 0.726297 |
| F1 | 0.723817 |
| IoU | 0.567174 |
| 预测水体像元 | 91,105 |
| 参考水体像元 | 90,485 |
| 水体像元差异 | 620 |

### SAR VV dB 基线

| 指标 | 结果 |
|---|---:|
| 有效像元 | 841,464（另有 1,116 个无效/边缘像元） |
| TP | 25,063 |
| TN | 743,740 |
| FP | 7,239 |
| FN | 65,422 |
| Overall Accuracy | 0.913649 |
| Precision | 0.775896 |
| Recall | 0.276985 |
| F1 | 0.408235 |
| IoU | 0.256467 |
| 预测水体像元 | 32,302 |
| 参考水体像元 | 90,485 |
| 水体像元差异 | -58,183 |

相对 MNDWI（候选减基线）的描述性差异为：OA `-0.026829`、Precision `+0.054542`、Recall `-0.449312`、F1 `-0.315582`、IoU `-0.310707`。这些差异不是统计显著性检验，也不能证明某一传感器在一般场景下优越。

当前运行记录中的 Wilson 95% 区间为像元级、未加权区间：

- OA：`[0.939971, 0.940981]`
- Precision：`[0.718434, 0.724256]`
- Recall：`[0.723383, 0.729193]`
- IoU：`[0.564319, 0.570024]`

这些区间不代表复杂抽样设计下的空间方差。空间相邻像元并不独立，正式论文仍需要按空间块、时间分层和抽样设计建立独立样本及设计一致的方差估计。

## 4. 可核验产物

本次光学运行 token：`c9aebba73e974e9ea370d65f978cfaf7`；SAR 运行 token：`16c413214d35480aa79fb12fbf840047`。两次运行均返回 `completed`，证据包均 `verified=true`，formal comparison 返回 `comparable=true`。最新快照哈希为 `a002e39740469a32b30cebd3d6bbcfd0080d155a7f407cbba5a8970d32353ccf`。

在本机可直接检查：

- [real_case_summary.json](E:/desktop/idl-rag/.tmp-real-poyang-case/real_case_summary.json)（重新运行脚本后以此文件中的最新 run token 为准）
- [MNDWI run_manifest.json](E:/desktop/idl-rag/.tmp-real-poyang-case/data/research/runs/project-1/c9aebba73e974e9ea370d65f978cfaf7/run_manifest.json)
- [MNDWI validation_metrics.json](E:/desktop/idl-rag/.tmp-real-poyang-case/data/research/runs/project-1/c9aebba73e974e9ea370d65f978cfaf7/validation_metrics.json)
- [SAR run_manifest.json](E:/desktop/idl-rag/.tmp-real-poyang-case/data/research/runs/project-1/16c413214d35480aa79fb12fbf840047/run_manifest.json)
- [SAR validation_metrics.json](E:/desktop/idl-rag/.tmp-real-poyang-case/data/research/runs/project-1/16c413214d35480aa79fb12fbf840047/validation_metrics.json)
- [MNDWI 研究报告](E:/desktop/idl-rag/.tmp-real-poyang-case/data/research/runs/project-1/c9aebba73e974e9ea370d65f978cfaf7/research_report.md)
- [SAR 研究报告](E:/desktop/idl-rag/.tmp-real-poyang-case/data/research/runs/project-1/16c413214d35480aa79fb12fbf840047/research_report.md)
- [MNDWI 证据包](E:/desktop/idl-rag/.tmp-real-poyang-case/data/research/runs/project-1/c9aebba73e974e9ea370d65f978cfaf7/research_evidence_package.zip)
- [SAR 证据包](E:/desktop/idl-rag/.tmp-real-poyang-case/data/research/runs/project-1/16c413214d35480aa79fb12fbf840047/research_evidence_package.zip)
- [MNDWI 分类预览](E:/desktop/idl-rag/.tmp-real-poyang-case/data/research/runs/project-1/c9aebba73e974e9ea370d65f978cfaf7/water_mask_preview.png)
- [SAR 分类预览](E:/desktop/idl-rag/.tmp-real-poyang-case/data/research/runs/project-1/16c413214d35480aa79fb12fbf840047/water_mask_preview.png)
- [SAR 验证误差图](E:/desktop/idl-rag/.tmp-real-poyang-case/data/research/runs/project-1/16c413214d35480aa79fb12fbf840047/validation_error_map_preview.png)

光学证据包校验结果：`verified=true`，检查文件数 `19`，外层 SHA-256：

```text
5171ebab8e3a182aaf7c537c80ca70d4174d14b7364231612c660ae085cf03f5
```

SAR 证据包外层 SHA-256：`59792fe5fd0428fb1843476b7711e66244d5015ad985912e73dbed171eae21bc`。

## 5. 本案例发现并修复的问题

真实运行的第一次尝试显示 WorldCover 裁剪结果为 `1 × 1`。原因不是数据服务失败，而是服务把 API 的 `target_resolution=10` 直接当成了 EPSG:4326 的 10 度，导致参考栅格失真。该结果被拒绝，不能作为科学验证。

已完成的修复：

- 对地理坐标 CRS，按窗口纬度把经纬度范围换算为米，再计算输出尺寸；
- 对投影 CRS，使用 CRS 线性单位因子换算；
- 在栅格元数据中记录 `target_resolution_m` 和 `target_resolution_interpretation=metres`；
- 增加回归测试，确保 EPSG:4326 的 1000 m 请求仍产生多像元栅格，而不是 `1 × 1`；
- 修复后重新下载并完整重跑本案例，WorldCover 裁剪为 `974 × 1106`，随后重投影后的验证参考与 Sentinel 输出均为 `755 × 1116`。

SAR 扩展又发现并修复了三类问题：

- Sentinel-1 GRD 的候选 COG 在当前裁剪路径中缺少可用 CRS，无法安全裁剪；案例改用 Planetary Computer RTC，并保留该失败记录；
- 原始 RTC VV 与光学网格尺寸/仿射变换不同，且验证契约要求预测与参考同网格；新增显式重投影资产并将它登记为可执行 `raster`；
- RTC VV 的原始单位是线性功率而非 dB。第一次 `-17` 阈值试跑产生零水体，已拒绝该科学解释；当前脚本在阈值前写入 `10*log10(VV)` 的 dB 栅格，并在 manifest/资产名中保留该转换事实。

本次重跑还把 provenance 写入了资产 metadata，而不只依赖文件名：对齐后的 VV 记录 `source_units=linear_power`、`output_units=dB`、`conversion_formula=10*log10(VV_linear)`、`resampling=bilinear` 和目标网格资产；WorldCover 参考记录 `source_class=80`、`reference_is_field_truth=false`、时间关系和 `nearest` 重采样方式。这样证据包消费方无需读取脚本，也能检查关键预处理假设。

详细问题清单见 [real-case-issues.md](E:/desktop/idl-rag/docs/real-case-issues.md)。

## 6. 下一步科学工作

本次案例已经证明平台可以处理真实公开数据并生成可审计结果，但还不是完整论文实验。下一轮应在不改变冻结基线的前提下增加：

1. 至少三个时间层（枯水、丰水、过渡期）和多个空间块；
2. 与场景同期的人工判读点样本，或水文/外业参考数据；
3. 在已完成 MNDWI/SAR 基线之上增加明确的光学—SAR 融合实验，并验证输入网格和单位；
4. 在 development/model-selection 上探索阈值或自适应公式，再在真正独立测试集上冻结比较；
5. 分层抽样、面积调整和设计一致的置信区间；
6. 用同一证据包比较不同方法，报告改进、无差异、退化和未解决限制，而不是只保留最优结果。
