# 权威资料补充清单

当前本地资料目录已经补充并导入“Remote Sensing Algorithms”知识库的权威产品、算法和工具资料包括：

- Landsat 8–9 Collection 2 Level-2 Science Product Guide v6（45 页）
- Landsat 4–7 Collection 2 Level-2 Science Product Guide v4（44 页）
- USGS Fact Sheet 2021–3055：Landsat Collection 2 Level-2 Science Products（2 页）
- NASA/USGS HLS User Guide V2（Harmonized Landsat and Sentinel-2，30 页）
- Google Earth Engine Sentinel-2 Harmonized / Cloud Score+ catalog note（官方数据集页面摘要，含缩放因子、波段分辨率、QA60/SCL 和云掩膜注意事项）
- Google Earth Engine Sentinel-1 GRD catalog note（SAR 极化、轨道、线性功率/dB 和 GRD/SLC 边界）
- GDAL Raster Data Model note（仿射变换、CRS、nodata/mask 和栅格验证清单）
- Sen2Cor 2.12 Quick Guide（ESA/STEP，1.0 MB）
- Sen2Cor L2A Algorithm Theoretical Baseline Document v2.10（ESA/STEP，4.7 MB）
- MODIS MOD11 User Guide V6 与 MOD11 Algorithm Theoretical Basis Document（NASA LP DAAC，用户指南 33 页、ATBD 77 页）
- MODIS MCD43 BRDF/Albedo User Guide V5（NASA Earthdata，覆盖 BRDF、反照率、质量波段与时间合成说明）
- MODIS Collection 6 Vegetation Index User Guide（NASA MODIS，NDVI/EVI 理论基础、合成和 QA）
- USGS LEDAPS Algorithm Description（Landsat 4–7 大气校正和地表反射率处理）
- ESA WorldCover 2020 v100 Product User Manual、2021 v200 Product User Manual 与 v200 Product Validation Report（共 81 页）
- Google Earth Engine Dynamic World、WorldCover、MOD11A1 与 Landsat Collection 2 官方 catalog note
- NASA Earthdata MCD43 BRDF/Albedo User Guide V5：`https://www.earthdata.nasa.gov/s3fs-public/2025-04/MCD43_User_Guide_V5.pdf`
- GDAL COG、Rasterio windowed I/O/reprojection、STAC 核心规范、GEE Python 认证导出和 GeoTIFF 验证清单
- ESA Sentinel-2 User Handbook（MSI 波段、产品级别、分辨率、tile 结构和处理等级）：`https://sentinels.copernicus.eu/documents/247904/685211/Sentinel-2_User_Handbook`
- NASA/JPL VIIRS VNP21 User Guide 与 LST&E ATBD（TES、发射率、WVS 大气校正和验证假设）：`https://lpdaac.usgs.gov/documents/1662/VNP21_User_Guide_V2.pdf`、`https://viirsland.gsfc.nasa.gov/PDF/VNP21_LSTE_ATBD_v2.1.pdf`
- USGS Landsat COG Data Format Control Book LSDS-1388（COG、STAC 元数据、内部瓦片、overview 和 HTTP range 访问）：`https://www.usgs.gov/media/files/landsat-cloud-optimized-geotiff-data-format-control-book`
- ESA Earth Observation Framework Sentinel-1 Product Unit Definition and Metadata ICD（ESA-EOPG-EOPGC-SP-1 v2.1，产品单元、辅助数据、OData 属性映射和 Sentinel-1 元数据）：`https://eof.esa.int/document/esa-eo-framework-eof-csc-sentinel-1-product-unit-definition-and-metadata-icd/`
- ESA Sentinel-1 Product Definition 与 2021 Product Specification（Level-1/Level-2 产品族、manifest、measurement/annotation/representation 数据集和元数据 cardinality）：`https://sentinels.copernicus.eu/documents/247904/1877131/Sentinel-1-Product-Definition.pdf`、`https://sentinels.copernicus.eu/documents/247904/1877131/Sentinel-1-Product-Specification-18052021.pdf`
- USGS Landsat Collection 2 QA Bands 与 Known Issues（QA_PIXEL、QA_RADSAT、SR_QA_AEROSOL、SR_CLOUD_QA、ST_QA bit 语义、clear bit 和 NoData 已知问题）：`https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands`、`https://www.usgs.gov/landsat-missions/landsat-collection-2-known-issues`
- NASA MODIS MCD43 BRDF/Albedo Algorithm Theoretical Basis Document V5（BRDF/albedo 反演、角度采样、质量标记和算法假设）：`https://lpdaac.usgs.gov/documents/97/MCD43_ATBD.pdf`
- USGS Landsat 8–9 Calibration/Validation Algorithm Description Document v5（LaSRC、大气/辐射/几何处理和地表温度算法）：`https://www.usgs.gov/media/files/landsat-8-9-calibration-validation-algorithm-description`
- ESA Sentinel-1 Radiometric Calibration V1.0（校准 LUT、β⁰/σ⁰/γ⁰、DN 缩放、入射角归一化和 GRD/SLC 示例）：`https://sentinels.copernicus.eu/documents/247904/685163/S1-Radiometric-Calibration-V1.0.pdf`
- USGS Landsat 8–9 Collection 2 Provisional Aquatic Reflectance Algorithm Description v6（水体反射率、Rayleigh 校正、中间反射率和水体掩膜）：`https://www.usgs.gov/media/files/landsat-8-9-collection-2-level-2-provisional-aquatic-reflectance-algorithm-description`
- NASA MODIS MOD13 Vegetation Index ATBD V5（NDVI/EVI、BRDF/正视合成、QA 和大气校正输入）：`https://lpdaac.usgs.gov/documents/449/MOD13_ATBD_V5.pdf`
- NASA/VIIRS VNP13 User Guide and Abridged ATBD V2.1.2（VIIRS NDVI/EVI、物候合成、质量标记和验证）：`https://lpdaac.usgs.gov/documents/1372/VNP13_User_Guide_ATBD_V2.1.2.pdf`

其中 825 页的 USGS LaSRC 文档保留完整原文，并通过 `backend/scripts/split_pdf_for_indexing.py` 生成 4 个带页码范围和 SHA-256 映射的索引分片，避免因单文档页数限制而截断证据。

本地还保存了一份可增量更新的 OpenAlex 遥感候选清单：当前为 1,500 条记录，其中 1,481 条有 DOI、1,238 条含摘要、1,500 条有落地页。它们是发现和去重线索，不是自动核验的全文，也不代表许可已经允许再分发；候选 Markdown 与 JSONL manifest 位于 `data/sources/collected/`，并通过严格来源审计后才进入本地检索库。

另有独立的扩展候选池正在通过带 checkpoint 的采集器增量收集；当前已安全保存 1,501 条中间记录，但 OpenAlex 近期触发了 429 限流，因此这批记录尚未自动并入生产候选库。恢复网络限流后可用同一命令继续，完成后还需与正式 1,500 条清单去重和质量抽查。

此外，本地 `data/sources/open_access_papers/` 已加入 307 篇开放获取候选论文（约 2.0 GB；包含中断恢复后重新登记和修复来源 URL 的全文），覆盖：

- 地表反射率、大气校正与跨传感器校准
- Landsat/Sentinel/MODIS 的 LST、植被指数和水体指数
- GEE + Random Forest 分类、土地覆盖变化检测
- 高光谱分类、SAR 土壤水分和云检测
- FORCE 分析就绪数据、GLC_FCS30 等产品方法

论文文件均保留 DOI、OpenAlex ID、原始 PDF URL、下载时间和 SHA-256。OpenAlex 的 OA 标记不等于再分发许可，清单统一标注为“需要逐条核对许可”；它们只用于本地研究资料库，不应未经核验发布到公共下载服务。当前 307 条全文记录均已通过 PDF 解析、来源和哈希审计，但仍不能据此推断公开再分发权。

这些资料覆盖了当前算法库里比较缺的一块：产品级输入约定，而不仅是指数公式。重点包括：

- Surface Reflectance 与 Surface Temperature 的产品定义
- Collection 2 缩放因子、填充值和有效范围
- `QA_PIXEL`、`QA_RADSAT`、`SR_QA_AEROSOL` 等质量波段
- LaSRC/LEDAPS 的处理边界
- 产品版本、数据格式和引用方式
- Sentinel-2 Harmonized 的 2022 年处理基线偏移、SR 缩放因子、跨分辨率重采样和 Cloud Score+ 阈值记录要求
- Sentinel-1 GRD 的极化/轨道筛选、线性功率与 dB 换算边界，以及 GDAL 栅格元数据、仿射变换和 nodata 校验
- Sen2Cor 的 L2A 产品定义、输入输出目录、处理基线和大气校正算法入口
- MODIS LST 的缩放因子、QC 位掩码、产品级别和广义分裂窗/昼夜算法入口
- WorldCover 的 v100/v200 算法差异、11 类编码、独立验证精度和产品限制
- 开放获取论文中的公式、实验条件、验证指标和跨传感器比较结果
- COG、窗口化处理、重投影、STAC 资产元数据和 GEE 导出的工程化约束

本地文件位于：

```text
data/sources/remote_sensing_official/
```

它们没有提交到 GitHub，因为 `data/` 包含运行时资料和索引。重新部署或换机器时，应按该目录的 `README.md`、`gee_sentinel2_harmonized.md` 以及本文列出的官方 URL 重新获取，并在导入后检查 SHA-256。开放获取论文还应按 `open_access_papers_manifest.jsonl` 的 DOI 与原始 URL 重新下载。GitHub 中只维护来源清单、导入规则和可复现说明，不把第三方 PDF 或运行时向量库直接提交进仓库。

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
