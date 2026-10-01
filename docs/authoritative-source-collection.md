# 权威资料补充清单

本轮已在本地资料目录补充 4 份 USGS/NASA 产品资料，并导入“Remote Sensing Algorithms”知识库：

- Landsat 8–9 Collection 2 Level-2 Science Product Guide v6（45 页）
- Landsat 4–7 Collection 2 Level-2 Science Product Guide v4（44 页）
- USGS Fact Sheet 2021–3055：Landsat Collection 2 Level-2 Science Products（2 页）
- NASA/USGS HLS User Guide V2（Harmonized Landsat and Sentinel-2，30 页）
- Google Earth Engine Sentinel-2 Harmonized / Cloud Score+ catalog note（官方数据集页面摘要，含缩放因子、波段分辨率、QA60/SCL 和云掩膜注意事项）
- Google Earth Engine Sentinel-1 GRD catalog note（SAR 极化、轨道、线性功率/dB 和 GRD/SLC 边界）
- GDAL Raster Data Model note（仿射变换、CRS、nodata/mask 和栅格验证清单）

这些资料覆盖了当前算法库里比较缺的一块：产品级输入约定，而不仅是指数公式。重点包括：

- Surface Reflectance 与 Surface Temperature 的产品定义
- Collection 2 缩放因子、填充值和有效范围
- `QA_PIXEL`、`QA_RADSAT`、`SR_QA_AEROSOL` 等质量波段
- LaSRC/LEDAPS 的处理边界
- 产品版本、数据格式和引用方式
- Sentinel-2 Harmonized 的 2022 年处理基线偏移、SR 缩放因子、跨分辨率重采样和 Cloud Score+ 阈值记录要求
- Sentinel-1 GRD 的极化/轨道筛选、线性功率与 dB 换算边界，以及 GDAL 栅格元数据、仿射变换和 nodata 校验

本地文件位于：

```text
data/sources/remote_sensing_official/
```

它们没有提交到 GitHub，因为 `data/` 包含运行时资料和索引。重新部署或换机器时，应按该目录的 `README.md` 以及 `gee_sentinel2_harmonized.md` 中的官方 URL 重新获取，并在导入后检查 SHA-256。GitHub 中只维护来源清单、导入规则和可复现说明，不把第三方 PDF 或运行时向量库直接提交进仓库。

## 下一批建议收集

### 传感器与产品规范

- ESA Sentinel-2 MSI User Handbook、产品规范和 QA60/SCL 说明
- NASA HLS User Guide（Landsat/Sentinel-2 harmonized surface reflectance）
- Sentinel-1 GRD/RTC 产品说明、线性功率与 dB 转换约定
- MODIS/VIIRS 地表温度、植被指数产品指南

### 算法与验证资料

- 6S/LaSRC/LEDAPS 大气校正与验证文档
- NDVI/EVI/NDWI/MNDWI/NDBI/SAVI/BSI 的原始论文和适用条件
- 云、云影、雪、饱和像元和 QA 掩膜的验证文档
- 分类、变化检测和精度评价的公开基准数据说明

### 工具与代码资料

- GDAL/Rasterio 的栅格、投影、nodata、重采样和 COG 规范
- Google Earth Engine 官方数据集页面、波段表和缩放因子
- Sentinel Hub/Planetary Computer/STAC API 的数据访问和许可说明
- IDL/ENVI 版本对应的 API 手册、示例代码和批处理约定

收集时要把资料分成“官方产品规范、开放全文论文、论文候选元数据、代码示例、内部资料”五类，并为每类记录来源、许可、版本、获取时间和 SHA-256。不能把搜索摘要或论文元数据直接升级为已核验全文。
