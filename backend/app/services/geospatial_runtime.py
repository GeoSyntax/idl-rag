from __future__ import annotations

import importlib.util
import os
from pathlib import Path


def configure_bundled_rasterio_data() -> None:
    """让 Rasterio 使用与其 wheel 匹配的 GDAL/PROJ 数据目录。

    Windows 上常见的 PostgreSQL/PostGIS 安装会向进程注入 PROJ_LIB/GDAL_DATA。
    如果它们与 Rasterio wheel 的 PROJ 版本不同，连 EPSG:4326 都无法解析。必须在
    导入 rasterio 前完成此配置，避免被外部安装目录污染本地科研执行器。
    """

    spec = importlib.util.find_spec("rasterio")
    if spec is None or spec.origin is None:
        return
    package_dir = Path(spec.origin).resolve().parent
    proj_data = package_dir / "proj_data"
    gdal_data = package_dir / "gdal_data"
    if proj_data.is_dir():
        value = str(proj_data)
        os.environ["PROJ_DATA"] = value
        os.environ["PROJ_LIB"] = value
    if gdal_data.is_dir():
        os.environ["GDAL_DATA"] = str(gdal_data)
