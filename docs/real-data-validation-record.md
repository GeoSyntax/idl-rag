# 真实 Sentinel-2 MNDWI 工程验证记录

> 记录类型：工程链路验收，不是科学结论。  
> 目的：证明公开数据搜索、私有化、跨分辨率波段对齐、公式执行和阶段影像产物可以在同一研究项目中复现。

本记录是早期的小 ROI 工程探针。完整的真实研究案例（含 WorldCover 代理参考、正式 PythonRunner、指标、证据包和现场问题修复）见：[鄱阳湖真实研究案例](real-research-case-poyang.md)；对应问题闭环见：[real-case-issues.md](real-case-issues.md)。

## 1. 数据与来源

- 数据服务：Microsoft Planetary Computer STAC，集合 `sentinel-2-l2a`。
- 现场场景：`S2A_MSIL2A_20241028T030831_R075_T50SMJ_20241028T065048`。
- 波段：B03（10 m）和 B11（20 m）。
- ROI：现场探针使用一个很小的 WGS84 bbox，目的是控制下载量和运行时间，不代表最终研究区。
- 授权：Planetary Computer 的短期 SAS 只用于本次读取，未写入项目数据库或证据包。
- 隐私边界：原始像元保存在临时项目私有 `research://assets/` 存储中，本文只记录公开场景标识、处理方式和输出元数据。

## 2. 实际链路

1. 使用公开 STAC 元数据搜索并写入项目审计记录。
2. 显式下载 B03 和 B11：B03 因对象超过整文件上限使用 `cog_http_range`，B11 使用 `full_download` 后裁剪。
3. 以 B03 为参考网格，对 B11 执行 bilinear 重采样。
4. 生成新的私有双波段 float32 GeoTIFF，并计算输出 SHA-256。
5. 创建冻结 DataSnapshot。
6. 登记带 DOI 的 MNDWI EvidenceCard。
7. 创建冻结 FormulaSpec：

```json
{
  "operation": "normalized_difference_threshold",
  "input_asset_id": "aligned_stack_asset",
  "inputs": {"green": 1, "swir1": 2},
  "parameters": {"threshold": 0.0}
}
```

8. 以 PythonRunner 运行 preview 实验。

## 3. 观察结果

| 项目 | 结果 |
|---|---|
| 输出 CRS | `EPSG:32650` |
| 输出尺寸 | `9 × 11` |
| 波段数 | `2` |
| 输出类型 | `float32` |
| NoData | `-9999` |
| Run 状态 | `completed` |
| 产物 | input preview、feature raster/preview、classification raster/preview、run manifest |
| 资产哈希 | 对私有化输出和对齐输出分别计算 SHA-256 |

## 4. 证据与边界

这次记录证明了：

- STAC 候选必须来自当前项目刚刚完成的审计搜索；
- 大 COG 可以在指定 crop 时使用 HTTP Range 读取；
- 10 m/20 m 波段可以形成确定的参考网格；
- 对齐资产可以被 DataSnapshot 和 PythonRunner 复用；
- 每个空间处理阶段有栅格/PNG/manifest 产物。

这次记录**不能**证明：

- MNDWI 阈值 0.0 适合所有区域、季节或传感器；
- MNDWI 优于其他指数、SAR 或融合基线；
- 结果具有独立标签支持的精度、面积或泛化能力；
- 该小 ROI 可以代表鄱阳湖或任何目标研究区。

正式研究仍需在协议中冻结研究区、时间分层、参考标签、独立时空留出、采样权重、误差调整和置信区间，并通过 formal Run 生成 Research Evidence Package。
