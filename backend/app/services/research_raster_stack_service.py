# ruff: noqa: E402
from __future__ import annotations

from app.services.geospatial_runtime import configure_bundled_rasterio_data

configure_bundled_rasterio_data()

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.io import MemoryFile
from rasterio.vrt import WarpedVRT
from sqlalchemy.orm import Session

from app.api.schemas import ResearchDataAssetCreate, ResearchDataAssetResponse, ResearchRasterStackCreate
from app.core.config import get_app_settings
from app.db.models import ResearchDataAsset
from app.services.research_asset_storage import ResearchAssetStorage
from app.services.research_service import ResearchService


class ResearchRasterStackService:
    """把同一项目的私有栅格显式对齐为一个可复跑的多波段 GeoTIFF。"""

    _RESAMPLING = {
        "nearest": Resampling.nearest,
        "bilinear": Resampling.bilinear,
        "cubic": Resampling.cubic,
    }

    def __init__(self) -> None:
        self.research_service = ResearchService()
        self.asset_storage = ResearchAssetStorage()

    def create_stack(
        self,
        db: Session,
        project_id: int,
        owner_user_id: int,
        payload: ResearchRasterStackCreate,
    ) -> ResearchDataAssetResponse:
        self.research_service.get_project(db, project_id, owner_user_id)
        if len(set(payload.asset_ids)) != len(payload.asset_ids):
            raise ValueError("栅格对齐不能重复引用同一资产。")
        if payload.band_names and len(payload.band_names) != len(payload.asset_ids):
            raise ValueError("band_names 数量必须与 asset_ids 一致，或留空使用资产名称。")
        if payload.reference_asset_id is not None and payload.reference_asset_id not in payload.asset_ids:
            raise ValueError("reference_asset_id 必须属于 asset_ids。")

        assets = (
            db.query(ResearchDataAsset)
            .filter(ResearchDataAsset.project_id == project_id, ResearchDataAsset.id.in_(payload.asset_ids))
            .all()
        )
        by_id = {asset.id: asset for asset in assets}
        if len(by_id) != len(payload.asset_ids):
            raise ValueError("栅格对齐只能引用当前研究项目中的数据资产。")
        for asset in (by_id[asset_id] for asset_id in payload.asset_ids):
            if asset.asset_kind != "raster":
                raise ValueError("栅格对齐的输入必须都是 asset_kind=raster。")
            if not asset.source_uri.startswith("research://assets/"):
                raise ValueError("只有已落盘的私有 research://assets/ 栅格才能对齐；远端引用不能直接运行。")

        reference_id = payload.reference_asset_id or payload.asset_ids[0]
        reference_asset = by_id[reference_id]
        reference_path = self.asset_storage.resolve_asset_uri(reference_asset.source_uri)
        output_content, grid_metadata = self._build_stack(
            [by_id[asset_id] for asset_id in payload.asset_ids],
            reference_path,
            payload.resampling,
        )
        source_fingerprints = [
            {
                "asset_id": asset.id,
                "name": asset.name,
                "sha256": asset.sha256,
                "source_uri": asset.source_uri,
            }
            for asset in (by_id[asset_id] for asset_id in payload.asset_ids)
        ]
        file_name = self._safe_file_name(payload.name)
        source_uri, sha256, size = self.asset_storage.store_bytes(project_id, file_name, output_content)
        metadata = {
            "derived_operation": "raster_stack",
            "source_asset_ids": payload.asset_ids,
            "reference_asset_id": reference_id,
            "band_names": payload.band_names or [by_id[asset_id].name for asset_id in payload.asset_ids],
            "resampling": payload.resampling,
            "source_fingerprints": source_fingerprints,
            "grid": grid_metadata,
            "stored_size": size,
            "raw_project_data_sent": False,
        }
        return self.research_service.create_data_asset(
            db,
            project_id,
            ResearchDataAssetCreate(
                name=payload.name.strip(),
                asset_kind="raster",
                source_type="local",
                source_uri=source_uri,
                sha256=sha256,
                metadata=metadata,
            ),
            owner_user_id,
        )

    def _build_stack(
        self,
        assets: list[ResearchDataAsset],
        reference_path,
        resampling: str,
    ) -> tuple[bytes, dict]:
        try:
            with rasterio.open(reference_path) as reference:
                if reference.crs is None:
                    raise ValueError("reference 栅格必须包含 CRS。")
                if reference.width < 1 or reference.height < 1:
                    raise ValueError("reference 栅格尺寸无效。")
                profile = reference.profile.copy()
                profile.update(
                    driver="GTiff",
                    count=len(assets),
                    dtype="float32",
                    nodata=-9999.0,
                    compress="lzw",
                    tiled=False,
                    width=reference.width,
                    height=reference.height,
                    crs=reference.crs,
                    transform=reference.transform,
                )
                max_pixels = get_app_settings().research_stac_max_output_pixels
                if reference.width * reference.height * len(assets) > max_pixels:
                    raise ValueError("栅格对齐输出像元数超过限制。")
                with MemoryFile() as output_memory:
                    with output_memory.open(**profile) as destination:
                        for band_index, asset in enumerate(assets, start=1):
                            input_path = self.asset_storage.resolve_asset_uri(asset.source_uri)
                            with rasterio.open(input_path) as source:
                                with WarpedVRT(
                                    source,
                                    crs=reference.crs,
                                    transform=reference.transform,
                                    width=reference.width,
                                    height=reference.height,
                                    resampling=self._RESAMPLING[resampling],
                                    nodata=-9999.0,
                                ) as aligned:
                                    values = aligned.read(1, out_dtype="float32", masked=True)
                                    destination.write(values.filled(-9999.0).astype(np.float32), band_index)
                        metadata = {
                            "crs": reference.crs.to_string(),
                            "transform": list(reference.transform)[:6],
                            "width": reference.width,
                            "height": reference.height,
                            "count": len(assets),
                            "dtype": "float32",
                            "nodata": -9999.0,
                        }
                    return output_memory.read(), metadata
        except ValueError:
            raise
        except (OSError, rasterio.errors.RasterioError) as exc:
            raise ValueError("栅格对齐失败：输入文件不是可读取的 GeoTIFF 或网格信息无效。") from exc

    @staticmethod
    def _safe_file_name(name: str) -> str:
        safe = "".join(char if char.isalnum() or char in "-_." else "_" for char in name.strip())
        return f"{safe[:180] or 'raster_stack'}.tif"
