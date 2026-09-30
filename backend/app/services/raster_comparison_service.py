# ruff: noqa: E402
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.geospatial_runtime import configure_bundled_rasterio_data

configure_bundled_rasterio_data()

import numpy as np
import rasterio

from app.db.models import ResearchDataAsset
from app.services.python_runner import PythonRunner
from app.services.research_asset_storage import ResearchAssetStorage


@dataclass(frozen=True)
class RasterComparisonResult:
    """Persistable result of a Python-versus-IDL raster comparison."""

    summary: dict[str, Any]
    outputs: list[dict[str, Any]]


class RasterComparisonService:
    """Compare a Python run raster against an explicitly registered local IDL result.

    The IDL raster is an auditable project DataAsset rather than a free filesystem
    path. This keeps the comparison usable before a licensed IDL worker is
    deployed, while still preserving the input identity in the run manifest.
    """

    _CONTINUOUS_NODATA = -9999.0
    _CLASSIFICATION_NODATA = -128

    def __init__(self) -> None:
        self.asset_storage = ResearchAssetStorage()

    def compare(
        self,
        *,
        output_dir: Path,
        run_token: str,
        python_output_file: str,
        idl_output_asset: ResearchDataAsset,
        comparison_mode: str,
        absolute_tolerance: float,
    ) -> RasterComparisonResult:
        safe_python_name = Path(python_output_file).name
        if safe_python_name != python_output_file or Path(safe_python_name).suffix.lower() not in {".tif", ".tiff"}:
            raise ValueError("IDL 对照的 python_output_file 必须是本次运行中的 GeoTIFF 文件名。")
        python_path = (output_dir / safe_python_name).resolve()
        if not python_path.is_relative_to(output_dir.resolve()) or not python_path.is_file() or python_path.is_symlink():
            raise ValueError("IDL 对照指定的 Python 栅格产物不存在。")
        idl_path = self.asset_storage.resolve_asset_uri(idl_output_asset.source_uri)
        if comparison_mode not in {"classification", "continuous"}:
            raise ValueError("IDL 对照模式必须为 classification 或 continuous。")
        if isinstance(absolute_tolerance, bool) or not isinstance(absolute_tolerance, (int, float)) or absolute_tolerance < 0:
            raise ValueError("IDL 对照 absolute_tolerance 必须是大于等于 0 的数值。")
        if comparison_mode == "classification" and absolute_tolerance != 0:
            raise ValueError("分类栅格 IDL 对照必须使用 absolute_tolerance=0。")

        with rasterio.open(python_path) as python_dataset, rasterio.open(idl_path) as idl_dataset:
            self._validate_grid(python_dataset, idl_dataset)
            python_masked = python_dataset.read(1, masked=True).astype(np.float64)
            idl_masked = idl_dataset.read(1, masked=True).astype(np.float64)
            python_values = python_masked.filled(np.nan)
            idl_values = idl_masked.filled(np.nan)
            python_valid = ~np.ma.getmaskarray(python_masked) & np.isfinite(python_values)
            idl_valid = ~np.ma.getmaskarray(idl_masked) & np.isfinite(idl_values)
            common_valid = python_valid & idl_valid
            if not common_valid.any():
                raise ValueError("IDL 对照栅格与 Python 栅格没有共同的有效像元。")
            profile = python_dataset.profile.copy()

        if comparison_mode == "classification":
            summary, difference, nodata = self._compare_classification(
                python_values, idl_values, common_valid, python_valid, idl_valid
            )
            difference_dtype = "int8"
            preview_cmap = "RdBu"
            preview_vmin, preview_vmax = -1.0, 1.0
        else:
            summary, difference, nodata = self._compare_continuous(
                python_values, idl_values, common_valid, python_valid, idl_valid, float(absolute_tolerance)
            )
            difference_dtype = "float32"
            preview_cmap = "RdBu_r"
            max_abs = float(np.max(np.abs(difference[common_valid])))
            preview_vmin, preview_vmax = -max(max_abs, 1e-12), max(max_abs, 1e-12)

        stem = Path(safe_python_name).stem
        prefix = f"idl_python_{comparison_mode}_{idl_output_asset.id}_{stem}"
        difference_path = output_dir / f"{prefix}_difference.tif"
        preview_path = output_dir / f"{prefix}_difference_preview.png"
        report_path = output_dir / f"{prefix}_report.json"
        PythonRunner._write_raster(difference_path, profile, difference, dtype=difference_dtype, nodata=nodata)
        preview_values = difference.astype(np.float32)
        preview_values[~common_valid] = np.nan
        PythonRunner._render_preview(
            preview_path,
            preview_values,
            f"Python − IDL difference ({comparison_mode})",
            preview_cmap,
            vmin=preview_vmin,
            vmax=preview_vmax,
        )

        payload = {
            "schema_version": 1,
            "comparison_mode": comparison_mode,
            "python_output_file": safe_python_name,
            "idl_output_asset": {
                "id": idl_output_asset.id,
                "name": idl_output_asset.name,
                "sha256": idl_output_asset.sha256,
                "asset_kind": idl_output_asset.asset_kind,
            },
            "grid": {
                "crs": profile.get("crs").to_string() if profile.get("crs") else None,
                "width": profile.get("width"),
                "height": profile.get("height"),
                "transform": list(profile.get("transform"))[:6] if profile.get("transform") else None,
            },
            **summary,
        }
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs = [
            PythonRunner._output_descriptor(
                difference_path,
                "idl_comparison_difference_raster",
                run_token,
                {"comparison_mode": comparison_mode, "nodata": nodata, "idl_output_asset_id": idl_output_asset.id},
            ),
            PythonRunner._output_descriptor(
                preview_path,
                "idl_comparison_difference_preview",
                run_token,
                {
                    "comparison_mode": comparison_mode,
                    "cmap": preview_cmap,
                    "vmin": preview_vmin,
                    "vmax": preview_vmax,
                    "idl_output_asset_id": idl_output_asset.id,
                },
            ),
            PythonRunner._output_descriptor(
                report_path,
                "idl_comparison_report",
                run_token,
                {"comparison_mode": comparison_mode, "idl_output_asset_id": idl_output_asset.id},
            ),
        ]
        return RasterComparisonResult(summary=payload, outputs=outputs)

    @staticmethod
    def _validate_grid(python_dataset: Any, idl_dataset: Any) -> None:
        if python_dataset.count < 1 or idl_dataset.count < 1:
            raise ValueError("IDL 对照的两个栅格都至少需要一个波段。")
        if python_dataset.crs is None or idl_dataset.crs is None:
            raise ValueError("IDL 对照的两个栅格都必须声明 CRS。")
        if python_dataset.crs != idl_dataset.crs:
            raise ValueError("IDL 对照栅格与 Python 栅格的 CRS 不一致。")
        if python_dataset.width != idl_dataset.width or python_dataset.height != idl_dataset.height:
            raise ValueError("IDL 对照栅格与 Python 栅格的宽高不一致。")
        if python_dataset.transform != idl_dataset.transform:
            raise ValueError("IDL 对照栅格与 Python 栅格的仿射变换/分辨率不一致。")

    def _compare_classification(
        self,
        python_values: np.ndarray,
        idl_values: np.ndarray,
        common_valid: np.ndarray,
        python_valid: np.ndarray,
        idl_valid: np.ndarray,
    ) -> tuple[dict[str, Any], np.ndarray, int]:
        valid_python_values = python_values[common_valid]
        valid_idl_values = idl_values[common_valid]
        if not np.isin(valid_python_values, [0, 1]).all() or not np.isin(valid_idl_values, [0, 1]).all():
            raise ValueError("分类 IDL 对照要求共同有效像元仅包含 0 或 1 标签。")
        python_labels = valid_python_values.astype(np.uint8)
        idl_labels = valid_idl_values.astype(np.uint8)
        tn = int(((python_labels == 0) & (idl_labels == 0)).sum())
        tp = int(((python_labels == 1) & (idl_labels == 1)).sum())
        python_water_idl_land = int(((python_labels == 1) & (idl_labels == 0)).sum())
        python_land_idl_water = int(((python_labels == 0) & (idl_labels == 1)).sum())
        total = int(common_valid.sum())
        difference = np.full(python_values.shape, self._CLASSIFICATION_NODATA, dtype=np.int8)
        difference[common_valid] = python_labels.astype(np.int8) - idl_labels.astype(np.int8)
        union = tp + python_water_idl_land + python_land_idl_water
        f1_denominator = 2 * tp + python_water_idl_land + python_land_idl_water
        return {
            "absolute_tolerance": 0.0,
            "common_valid_pixel_count": total,
            "python_nodata_pixel_count": int((~python_valid).sum()),
            "idl_nodata_pixel_count": int((~idl_valid).sum()),
            "nodata_mismatch_pixel_count": int((python_valid ^ idl_valid).sum()),
            "agreement_pixel_count": tn + tp,
            "disagreement_pixel_count": python_water_idl_land + python_land_idl_water,
            "agreement_rate": float((tn + tp) / total),
            "water_iou": float(tp / union) if union else 1.0,
            "water_f1": float(2 * tp / f1_denominator) if f1_denominator else 1.0,
            "water_pixel_difference_python_minus_idl": int((python_labels == 1).sum() - (idl_labels == 1).sum()),
            "confusion": {
                "both_land": tn,
                "both_water": tp,
                "python_water_idl_land": python_water_idl_land,
                "python_land_idl_water": python_land_idl_water,
            },
        }, difference, self._CLASSIFICATION_NODATA

    def _compare_continuous(
        self,
        python_values: np.ndarray,
        idl_values: np.ndarray,
        common_valid: np.ndarray,
        python_valid: np.ndarray,
        idl_valid: np.ndarray,
        absolute_tolerance: float,
    ) -> tuple[dict[str, Any], np.ndarray, float]:
        difference = np.full(python_values.shape, self._CONTINUOUS_NODATA, dtype=np.float32)
        common_difference = python_values[common_valid] - idl_values[common_valid]
        difference[common_valid] = common_difference.astype(np.float32)
        absolute_difference = np.abs(common_difference)
        total = int(common_valid.sum())
        return {
            "absolute_tolerance": absolute_tolerance,
            "common_valid_pixel_count": total,
            "python_nodata_pixel_count": int((~python_valid).sum()),
            "idl_nodata_pixel_count": int((~idl_valid).sum()),
            "nodata_mismatch_pixel_count": int((python_valid ^ idl_valid).sum()),
            "within_tolerance_pixel_count": int((absolute_difference <= absolute_tolerance).sum()),
            "within_tolerance_rate": float((absolute_difference <= absolute_tolerance).mean()),
            "mean_signed_difference": float(common_difference.mean()),
            "mean_absolute_difference": float(absolute_difference.mean()),
            "max_absolute_difference": float(absolute_difference.max()),
            "rmse": float(np.sqrt(np.mean(np.square(common_difference)))),
        }, difference, self._CONTINUOUS_NODATA
