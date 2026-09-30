# ruff: noqa: E402
from __future__ import annotations

import ast
import hashlib
import json
import math
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.geospatial_runtime import configure_bundled_rasterio_data

configure_bundled_rasterio_data()

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.warp import transform as transform_coordinates

from app.db.models import (
    FormulaSpec,
    ResearchDataAsset,
    ResearchDataSnapshot,
    ResearchExperiment,
    ResearchValidationSample,
)
from app.services.research_asset_storage import ResearchAssetStorage


@dataclass(frozen=True)
class PythonRunResult:
    manifest: dict[str, Any]
    outputs: list[dict[str, Any]]


class PythonRunner:
    """受 FormulaSpec 约束的本地栅格执行器。

    公式以明确 operation 扩展，绝不执行 Agent 或用户提交的任意 Python 代码。受限
    的 ``safe_band_math_threshold`` 使用 AST 白名单解释器，而不是 ``eval``/``exec``。
    """

    _OPERATION = "normalized_difference_threshold"
    _ADAPTIVE_OPERATION = "adaptive_normalized_difference_otsu"
    _SAR_OPERATION = "sar_backscatter_threshold"
    _FUSION_OPERATION = "transparent_water_fusion"
    _BAND_MATH_OPERATION = "safe_band_math_threshold"
    _NODATA_FLOAT = -9999.0
    _NODATA_MASK = 255

    def __init__(self) -> None:
        self.asset_storage = ResearchAssetStorage()

    def execute(
        self,
        experiment: ResearchExperiment,
        formula_spec: FormulaSpec,
        snapshot: ResearchDataSnapshot,
        assets: list[ResearchDataAsset],
        output_dir: Path,
        run_token: str,
        validation_samples: list[ResearchValidationSample] | None = None,
    ) -> PythonRunResult:
        requested_operation = str((formula_spec.spec_json or {}).get("operation") or self._OPERATION)
        if requested_operation == self._BAND_MATH_OPERATION:
            result = self._execute_safe_band_math_threshold(
                experiment=experiment, formula_spec=formula_spec, snapshot=snapshot, assets=assets,
                output_dir=output_dir, run_token=run_token,
            )
            return self._attach_validations(
                result, experiment, snapshot, assets, output_dir, run_token, validation_samples or []
            )
        if requested_operation == self._SAR_OPERATION:
            result = self._execute_sar_threshold(
                experiment=experiment, formula_spec=formula_spec, snapshot=snapshot, assets=assets,
                output_dir=output_dir, run_token=run_token,
            )
            return self._attach_validations(
                result, experiment, snapshot, assets, output_dir, run_token, validation_samples or []
            )
        if requested_operation == self._ADAPTIVE_OPERATION:
            result = self._execute_adaptive_normalized_difference(
                experiment=experiment, formula_spec=formula_spec, snapshot=snapshot, assets=assets,
                output_dir=output_dir, run_token=run_token,
            )
            return self._attach_validations(
                result, experiment, snapshot, assets, output_dir, run_token, validation_samples or []
            )
        if requested_operation == self._FUSION_OPERATION:
            result = self._execute_transparent_fusion(
                experiment=experiment, formula_spec=formula_spec, snapshot=snapshot, assets=assets,
                output_dir=output_dir, run_token=run_token,
            )
            return self._attach_validations(
                result, experiment, snapshot, assets, output_dir, run_token, validation_samples or []
            )
        operation, input_asset_id, green_band, swir1_band, threshold = self._resolve_spec(
            formula_spec, experiment, snapshot, assets
        )
        if operation != self._OPERATION:
            raise ValueError(f"PythonRunner 暂不支持公式操作：{operation}")

        asset = next(asset for asset in assets if asset.id == input_asset_id)
        input_path = self.asset_storage.resolve_asset_uri(asset.source_uri)
        output_dir.mkdir(parents=True, exist_ok=False)

        with rasterio.open(input_path) as dataset:
            if dataset.count < max(green_band, swir1_band):
                raise ValueError("输入影像波段数不足，无法读取 FormulaSpec 指定的 green/swir1 波段。")
            green = dataset.read(green_band, masked=True).astype(np.float32)
            swir1 = dataset.read(swir1_band, masked=True).astype(np.float32)
            valid = ~(np.ma.getmaskarray(green) | np.ma.getmaskarray(swir1))
            denominator = green.filled(np.nan) + swir1.filled(np.nan)
            valid &= np.isfinite(denominator) & (np.abs(denominator) > np.finfo(np.float32).eps)
            index = np.full(dataset.shape, self._NODATA_FLOAT, dtype=np.float32)
            np.divide(
                green.filled(np.nan) - swir1.filled(np.nan),
                denominator,
                out=index,
                where=valid,
            )
            water_mask = np.full(dataset.shape, self._NODATA_MASK, dtype=np.uint8)
            water_mask[valid] = (index[valid] >= threshold).astype(np.uint8)

            profile = dataset.profile.copy()
            profile.update(count=1, compress="lzw")
            index_path = output_dir / "normalized_difference.tif"
            mask_path = output_dir / "water_mask.tif"
            self._write_raster(index_path, profile, index, dtype="float32", nodata=self._NODATA_FLOAT)
            self._write_raster(mask_path, profile, water_mask, dtype="uint8", nodata=self._NODATA_MASK)

            input_preview_path = output_dir / "input_preview.png"
            index_preview_path = output_dir / "normalized_difference_preview.png"
            mask_preview_path = output_dir / "water_mask_preview.png"
            self._render_preview(input_preview_path, green.filled(np.nan), "Input band: green", "gray")
            self._render_preview(index_preview_path, index, "Normalized difference", "BrBG", vmin=-1, vmax=1)
            self._render_preview(mask_preview_path, water_mask, "Water mask", "Blues", vmin=0, vmax=1)

            raster_metadata = {
                "crs": dataset.crs.to_string() if dataset.crs else None,
                "transform": list(dataset.transform)[:6],
                "width": dataset.width,
                "height": dataset.height,
                "input_nodata": dataset.nodata,
                "valid_pixel_count": int(valid.sum()),
                "nodata_pixel_count": int(valid.size - valid.sum()),
                "water_pixel_count": int((water_mask == 1).sum()),
                "threshold": threshold,
                "green_band": green_band,
                "swir1_band": swir1_band,
            }

        outputs = [
            self._output_descriptor(input_preview_path, "input_preview", run_token, {"cmap": "gray"}),
            self._output_descriptor(
                index_path,
                "feature_raster",
                run_token,
                {"formula": "(green - swir1) / (green + swir1)", "nodata": self._NODATA_FLOAT},
            ),
            self._output_descriptor(
                index_preview_path,
                "feature_preview",
                run_token,
                {"cmap": "BrBG", "vmin": -1, "vmax": 1},
            ),
            self._output_descriptor(
                mask_path,
                "classification_raster",
                run_token,
                {"threshold": threshold, "nodata": self._NODATA_MASK},
            ),
            self._output_descriptor(
                mask_preview_path,
                "classification_preview",
                run_token,
                {"cmap": "Blues", "vmin": 0, "vmax": 1},
            ),
        ]
        manifest = {
            "schema_version": 1,
            "run_token": run_token,
            "runner": {
                "type": "python",
                "python_version": sys.version.split()[0],
                "platform": platform.platform(),
                "numpy_version": np.__version__,
                "rasterio_version": rasterio.__version__,
                "matplotlib_version": matplotlib.__version__,
            },
            "experiment": {
                "id": experiment.id,
                "name": experiment.name,
                "execution_mode": experiment.execution_mode,
                "parameters": experiment.parameters_json,
            },
            "formula_spec": {
                "id": formula_spec.id,
                "name": formula_spec.name,
                "version": formula_spec.version,
                "status": formula_spec.status,
                "operation": operation,
                "evidence_card_ids": formula_spec.evidence_card_ids_json,
            },
            "data_snapshot": {
                "id": snapshot.id,
                "snapshot_hash": snapshot.snapshot_hash,
                "asset_ids": snapshot.asset_ids_json,
            },
            "input_asset": {
                "id": asset.id,
                "name": asset.name,
                "asset_kind": asset.asset_kind,
                "source_type": asset.source_type,
                "sha256": asset.sha256,
            },
            "raster_metadata": raster_metadata,
            "outputs": outputs,
        }
        manifest_path = output_dir / "run_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs.append(self._output_descriptor(manifest_path, "run_manifest", run_token, {}))
        manifest["outputs"] = outputs
        return self._attach_validations(
            PythonRunResult(manifest=manifest, outputs=outputs),
            experiment,
            snapshot,
            assets,
            output_dir,
            run_token,
            validation_samples or [],
        )

    def _execute_safe_band_math_threshold(
        self,
        *,
        experiment: ResearchExperiment,
        formula_spec: FormulaSpec,
        snapshot: ResearchDataSnapshot,
        assets: list[ResearchDataAsset],
        output_dir: Path,
        run_token: str,
    ) -> PythonRunResult:
        """Evaluate a tightly scoped numeric FormulaSpec without Python execution.

        The formula may contain names declared in ``inputs`` or ``parameters``;
        the AST evaluator below accepts only numeric literals, basic arithmetic,
        and a deliberately small set of NumPy scalar/array math functions.
        """
        spec = formula_spec.spec_json or {}
        asset = self._resolve_asset(spec, "input_asset_id", snapshot, assets)
        expression_source = self._normalize_band_math_expression(spec.get("expression"))
        expression = self._parse_band_math_expression(expression_source)
        input_definitions = self._resolve_band_math_inputs(spec.get("inputs"))
        parameter_values = self._resolve_band_math_parameters(
            spec.get("parameters"), experiment.parameters_json or {}, set(input_definitions)
        )
        raw_threshold = parameter_values.get("threshold")
        if raw_threshold is None:
            raise ValueError("safe_band_math_threshold 的 parameters 必须声明有限数值 threshold。")
        threshold = float(raw_threshold)
        comparison = str(spec.get("water_condition", ">="))
        if comparison not in {">=", "<="}:
            raise ValueError("safe_band_math_threshold 的 water_condition 只能是 >= 或 <=。")
        input_path = self.asset_storage.resolve_asset_uri(asset.source_uri)
        output_dir.mkdir(parents=True, exist_ok=False)

        with rasterio.open(input_path) as dataset:
            maximum_band = max(item["band"] for item in input_definitions.values())
            if dataset.count < maximum_band:
                raise ValueError("输入影像波段数不足，无法读取安全波段数学公式指定的波段。")
            valid = np.ones(dataset.shape, dtype=bool)
            values: dict[str, np.ndarray | float] = dict(parameter_values)
            input_arrays: dict[str, np.ndarray] = {}
            for name, definition in input_definitions.items():
                source = dataset.read(definition["band"], masked=True).astype(np.float32)
                array = source.filled(np.nan) * definition["scale"] + definition["offset"]
                valid &= ~np.ma.getmaskarray(source) & np.isfinite(array)
                input_arrays[name] = array
                values[name] = array
            with np.errstate(all="ignore"):
                calculated = self._evaluate_band_math_expression(expression, values)
            calculated_array = np.asarray(calculated, dtype=np.float32)
            if calculated_array.ndim == 0:
                calculated_array = np.full(dataset.shape, calculated_array.item(), dtype=np.float32)
            if calculated_array.shape != dataset.shape:
                raise ValueError("安全波段数学公式的输出形状必须与输入栅格相同。")
            valid &= np.isfinite(calculated_array)
            feature = np.full(dataset.shape, self._NODATA_FLOAT, dtype=np.float32)
            feature[valid] = calculated_array[valid]
            water_mask = np.full(dataset.shape, self._NODATA_MASK, dtype=np.uint8)
            if comparison == ">=":
                water_mask[valid] = (feature[valid] >= threshold).astype(np.uint8)
            else:
                water_mask[valid] = (feature[valid] <= threshold).astype(np.uint8)

            profile = dataset.profile.copy()
            first_input_name = next(iter(input_definitions))
            input_preview_path = output_dir / "input_preview.png"
            feature_path = output_dir / "band_math_feature.tif"
            feature_preview_path = output_dir / "band_math_feature_preview.png"
            mask_path = output_dir / "water_mask.tif"
            mask_preview_path = output_dir / "water_mask_preview.png"
            self._write_raster(feature_path, profile, feature, dtype="float32", nodata=self._NODATA_FLOAT)
            self._write_raster(mask_path, profile, water_mask, dtype="uint8", nodata=self._NODATA_MASK)
            self._render_preview(input_preview_path, input_arrays[first_input_name], f"Input band: {first_input_name}", "gray")
            self._render_preview(feature_preview_path, feature, "Safe band-math feature", "BrBG")
            self._render_preview(mask_preview_path, water_mask, "Water mask", "Blues", vmin=0, vmax=1)
            raster_metadata = self._raster_metadata(
                dataset,
                valid,
                water_mask,
                expression=expression_source,
                input_bands={name: definition["band"] for name, definition in input_definitions.items()},
                input_transforms={
                    name: {"scale": definition["scale"], "offset": definition["offset"]}
                    for name, definition in input_definitions.items()
                },
                parameter_values=parameter_values,
                threshold=threshold,
                water_condition=comparison,
            )

        outputs = [
            self._output_descriptor(input_preview_path, "input_preview", run_token, {"cmap": "gray"}),
            self._output_descriptor(
                feature_path,
                "feature_raster",
                run_token,
                {"expression": expression_source, "parameters": parameter_values, "nodata": self._NODATA_FLOAT},
            ),
            self._output_descriptor(feature_preview_path, "feature_preview", run_token, {"cmap": "BrBG"}),
            self._output_descriptor(
                mask_path,
                "classification_raster",
                run_token,
                {"threshold": threshold, "comparison": comparison, "nodata": self._NODATA_MASK},
            ),
            self._output_descriptor(mask_preview_path, "classification_preview", run_token, {"cmap": "Blues"}),
        ]
        return self._finish_result(
            output_dir=output_dir,
            run_token=run_token,
            outputs=outputs,
            operation=self._BAND_MATH_OPERATION,
            experiment=experiment,
            formula_spec=formula_spec,
            snapshot=snapshot,
            input_assets=[asset],
            raster_metadata=raster_metadata,
        )

    def _attach_validations(
        self,
        result: PythonRunResult,
        experiment: ResearchExperiment,
        snapshot: ResearchDataSnapshot,
        assets: list[ResearchDataAsset],
        output_dir: Path,
        run_token: str,
        validation_samples: list[ResearchValidationSample],
    ) -> PythonRunResult:
        result = self._attach_reference_validation(result, experiment, snapshot, assets, output_dir, run_token)
        return self._attach_point_sample_validation(
            result, experiment, snapshot, output_dir, run_token, validation_samples
        )

    def _execute_sar_threshold(
        self,
        *,
        experiment: ResearchExperiment,
        formula_spec: FormulaSpec,
        snapshot: ResearchDataSnapshot,
        assets: list[ResearchDataAsset],
        output_dir: Path,
        run_token: str,
    ) -> PythonRunResult:
        spec = formula_spec.spec_json or {}
        asset = self._resolve_asset(spec, "input_asset_id", snapshot, assets)
        vv_band = self._resolve_band((spec.get("inputs") or {}).get("vv"), "vv")
        threshold = self._resolve_threshold(
            experiment, spec, "threshold", default=-17.0, minimum=-60.0, maximum=30.0
        )
        input_path = self.asset_storage.resolve_asset_uri(asset.source_uri)
        output_dir.mkdir(parents=True, exist_ok=False)

        with rasterio.open(input_path) as dataset:
            if dataset.count < vv_band:
                raise ValueError("输入影像波段数不足，无法读取 FormulaSpec 指定的 vv 波段。")
            vv = dataset.read(vv_band, masked=True).astype(np.float32)
            valid = ~np.ma.getmaskarray(vv) & np.isfinite(vv.filled(np.nan))
            backscatter = np.full(dataset.shape, self._NODATA_FLOAT, dtype=np.float32)
            backscatter[valid] = vv.filled(np.nan)[valid]
            water_mask = np.full(dataset.shape, self._NODATA_MASK, dtype=np.uint8)
            water_mask[valid] = (backscatter[valid] <= threshold).astype(np.uint8)
            profile = dataset.profile.copy()
            input_preview_path = output_dir / "input_preview.png"
            feature_path = output_dir / "sar_backscatter.tif"
            feature_preview_path = output_dir / "sar_backscatter_preview.png"
            mask_path = output_dir / "water_mask.tif"
            mask_preview_path = output_dir / "water_mask_preview.png"
            self._write_raster(feature_path, profile, backscatter, dtype="float32", nodata=self._NODATA_FLOAT)
            self._write_raster(mask_path, profile, water_mask, dtype="uint8", nodata=self._NODATA_MASK)
            self._render_preview(input_preview_path, backscatter, "Input band: VV", "gray")
            self._render_preview(feature_preview_path, backscatter, "SAR backscatter", "viridis")
            self._render_preview(mask_preview_path, water_mask, "Water mask", "Blues", vmin=0, vmax=1)
            raster_metadata = self._raster_metadata(
                dataset,
                valid,
                water_mask,
                threshold=threshold,
                vv_band=vv_band,
                water_condition="vv <= threshold",
            )

        outputs = [
            self._output_descriptor(input_preview_path, "input_preview", run_token, {"cmap": "gray"}),
            self._output_descriptor(
                feature_path,
                "feature_raster",
                run_token,
                {"feature": "vv_backscatter", "nodata": self._NODATA_FLOAT},
            ),
            self._output_descriptor(feature_preview_path, "feature_preview", run_token, {"cmap": "viridis"}),
            self._output_descriptor(
                mask_path,
                "classification_raster",
                run_token,
                {"threshold": threshold, "comparison": "<=", "nodata": self._NODATA_MASK},
            ),
            self._output_descriptor(mask_preview_path, "classification_preview", run_token, {"cmap": "Blues"}),
        ]
        return self._finish_result(
            output_dir=output_dir,
            run_token=run_token,
            outputs=outputs,
            operation=self._SAR_OPERATION,
            experiment=experiment,
            formula_spec=formula_spec,
            snapshot=snapshot,
            input_assets=[asset],
            raster_metadata=raster_metadata,
        )

    def _execute_adaptive_normalized_difference(
        self,
        *,
        experiment: ResearchExperiment,
        formula_spec: FormulaSpec,
        snapshot: ResearchDataSnapshot,
        assets: list[ResearchDataAsset],
        output_dir: Path,
        run_token: str,
    ) -> PythonRunResult:
        """Run a reproducible Otsu threshold candidate for an index image.

        This operation is deliberately scoped to a registered FormulaSpec. It is
        not an optimization loop and does not inspect validation labels, so a
        researcher can compare it honestly with the fixed M1 threshold.
        """
        spec = formula_spec.spec_json or {}
        asset = self._resolve_asset(spec, "input_asset_id", snapshot, assets)
        inputs = spec.get("inputs") or {}
        green_band = self._resolve_band(inputs.get("green"), "green")
        swir1_band = self._resolve_band(inputs.get("swir1"), "swir1")
        histogram_bins = self._resolve_integer_parameter(
            experiment, spec, "histogram_bins", default=256, minimum=16, maximum=1024
        )
        threshold_min = self._resolve_threshold(
            experiment, spec, "threshold_min", default=-1.0, minimum=-1.0, maximum=1.0
        )
        threshold_max = self._resolve_threshold(
            experiment, spec, "threshold_max", default=1.0, minimum=-1.0, maximum=1.0
        )
        if threshold_min >= threshold_max:
            raise ValueError("threshold_min 必须小于 threshold_max。")
        input_path = self.asset_storage.resolve_asset_uri(asset.source_uri)
        output_dir.mkdir(parents=True, exist_ok=False)

        with rasterio.open(input_path) as dataset:
            if dataset.count < max(green_band, swir1_band):
                raise ValueError("输入影像波段数不足，无法读取 FormulaSpec 指定的 green/swir1 波段。")
            green = dataset.read(green_band, masked=True).astype(np.float32)
            swir1 = dataset.read(swir1_band, masked=True).astype(np.float32)
            valid = ~(np.ma.getmaskarray(green) | np.ma.getmaskarray(swir1))
            denominator = green.filled(np.nan) + swir1.filled(np.nan)
            valid &= np.isfinite(denominator) & (np.abs(denominator) > np.finfo(np.float32).eps)
            index = np.full(dataset.shape, self._NODATA_FLOAT, dtype=np.float32)
            np.divide(
                green.filled(np.nan) - swir1.filled(np.nan),
                denominator,
                out=index,
                where=valid,
            )
            threshold = self._otsu_threshold(index[valid], histogram_bins, threshold_min, threshold_max)
            water_mask = np.full(dataset.shape, self._NODATA_MASK, dtype=np.uint8)
            water_mask[valid] = (index[valid] >= threshold).astype(np.uint8)
            profile = dataset.profile.copy()
            input_preview_path = output_dir / "input_preview.png"
            index_path = output_dir / "normalized_difference.tif"
            index_preview_path = output_dir / "normalized_difference_preview.png"
            mask_path = output_dir / "water_mask.tif"
            mask_preview_path = output_dir / "water_mask_preview.png"
            self._write_raster(index_path, profile, index, dtype="float32", nodata=self._NODATA_FLOAT)
            self._write_raster(mask_path, profile, water_mask, dtype="uint8", nodata=self._NODATA_MASK)
            self._render_preview(input_preview_path, green.filled(np.nan), "Input band: green", "gray")
            self._render_preview(index_preview_path, index, "Normalized difference", "BrBG", vmin=-1, vmax=1)
            self._render_preview(mask_preview_path, water_mask, "Adaptive water mask", "Blues", vmin=0, vmax=1)
            raster_metadata = self._raster_metadata(
                dataset,
                valid,
                water_mask,
                threshold=threshold,
                threshold_method="otsu_global_histogram",
                histogram_bins=histogram_bins,
                threshold_min=threshold_min,
                threshold_max=threshold_max,
                green_band=green_band,
                swir1_band=swir1_band,
            )

        outputs = [
            self._output_descriptor(input_preview_path, "input_preview", run_token, {"cmap": "gray"}),
            self._output_descriptor(
                index_path,
                "feature_raster",
                run_token,
                {"formula": "(green - swir1) / (green + swir1)", "nodata": self._NODATA_FLOAT},
            ),
            self._output_descriptor(index_preview_path, "feature_preview", run_token, {"cmap": "BrBG", "vmin": -1, "vmax": 1}),
            self._output_descriptor(
                mask_path,
                "classification_raster",
                run_token,
                {"threshold": threshold, "threshold_method": "otsu_global_histogram", "nodata": self._NODATA_MASK},
            ),
            self._output_descriptor(mask_preview_path, "classification_preview", run_token, {"cmap": "Blues"}),
        ]
        return self._finish_result(
            output_dir=output_dir,
            run_token=run_token,
            outputs=outputs,
            operation=self._ADAPTIVE_OPERATION,
            experiment=experiment,
            formula_spec=formula_spec,
            snapshot=snapshot,
            input_assets=[asset],
            raster_metadata=raster_metadata,
        )

    def _execute_transparent_fusion(
        self,
        *,
        experiment: ResearchExperiment,
        formula_spec: FormulaSpec,
        snapshot: ResearchDataSnapshot,
        assets: list[ResearchDataAsset],
        output_dir: Path,
        run_token: str,
    ) -> PythonRunResult:
        spec = formula_spec.spec_json or {}
        optical_asset = self._resolve_asset(spec, "optical_asset_id", snapshot, assets)
        sar_asset = self._resolve_asset(spec, "sar_asset_id", snapshot, assets)
        inputs = spec.get("inputs") or {}
        green_band = self._resolve_band(inputs.get("green"), "green")
        swir1_band = self._resolve_band(inputs.get("swir1"), "swir1")
        vv_band = self._resolve_band(inputs.get("vv"), "vv")
        optical_threshold = self._resolve_threshold(
            experiment, spec, "optical_threshold", default=0.0, minimum=-1.0, maximum=1.0
        )
        sar_threshold = self._resolve_threshold(
            experiment, spec, "sar_threshold", default=-17.0, minimum=-60.0, maximum=30.0
        )
        rule = str((experiment.parameters_json or {}).get("fusion_rule", (spec.get("parameters") or {}).get("fusion_rule", "or")))
        if rule not in {"or", "and"}:
            raise ValueError("fusion_rule 必须为 'or' 或 'and'，以保持融合规则可解释。")
        optical_path = self.asset_storage.resolve_asset_uri(optical_asset.source_uri)
        sar_path = self.asset_storage.resolve_asset_uri(sar_asset.source_uri)
        output_dir.mkdir(parents=True, exist_ok=False)

        with rasterio.open(optical_path) as optical_dataset, rasterio.open(sar_path) as sar_dataset:
            self._validate_fusion_grids(optical_dataset, sar_dataset)
            if optical_dataset.count < max(green_band, swir1_band) or sar_dataset.count < vv_band:
                raise ValueError("融合输入影像波段数不足，无法读取 FormulaSpec 指定的波段。")
            green = optical_dataset.read(green_band, masked=True).astype(np.float32)
            swir1 = optical_dataset.read(swir1_band, masked=True).astype(np.float32)
            vv = sar_dataset.read(vv_band, masked=True).astype(np.float32)
            optical_valid = ~(np.ma.getmaskarray(green) | np.ma.getmaskarray(swir1))
            denominator = green.filled(np.nan) + swir1.filled(np.nan)
            optical_valid &= np.isfinite(denominator) & (np.abs(denominator) > np.finfo(np.float32).eps)
            sar_valid = ~np.ma.getmaskarray(vv) & np.isfinite(vv.filled(np.nan))
            valid = optical_valid & sar_valid
            index = np.full(optical_dataset.shape, self._NODATA_FLOAT, dtype=np.float32)
            np.divide(
                green.filled(np.nan) - swir1.filled(np.nan),
                denominator,
                out=index,
                where=optical_valid,
            )
            optical_water = index >= optical_threshold
            sar_water = vv.filled(np.nan) <= sar_threshold
            fused_water = optical_water | sar_water if rule == "or" else optical_water & sar_water
            water_mask = np.full(optical_dataset.shape, self._NODATA_MASK, dtype=np.uint8)
            water_mask[valid] = fused_water[valid].astype(np.uint8)
            disagreement = np.full(optical_dataset.shape, self._NODATA_FLOAT, dtype=np.float32)
            disagreement[valid] = (optical_water[valid] != sar_water[valid]).astype(np.float32)
            profile = optical_dataset.profile.copy()
            input_preview_path = output_dir / "input_preview.png"
            sar_preview_path = output_dir / "sar_input_preview.png"
            index_path = output_dir / "normalized_difference.tif"
            index_preview_path = output_dir / "normalized_difference_preview.png"
            mask_path = output_dir / "water_mask.tif"
            mask_preview_path = output_dir / "water_mask_preview.png"
            uncertainty_path = output_dir / "fusion_disagreement.tif"
            uncertainty_preview_path = output_dir / "fusion_disagreement_preview.png"
            self._write_raster(index_path, profile, index, dtype="float32", nodata=self._NODATA_FLOAT)
            self._write_raster(mask_path, profile, water_mask, dtype="uint8", nodata=self._NODATA_MASK)
            self._write_raster(uncertainty_path, profile, disagreement, dtype="float32", nodata=self._NODATA_FLOAT)
            self._render_preview(input_preview_path, green.filled(np.nan), "Optical input band: green", "gray")
            self._render_preview(sar_preview_path, vv.filled(np.nan), "SAR input band: VV", "gray")
            self._render_preview(index_preview_path, index, "Normalized difference", "BrBG", vmin=-1, vmax=1)
            self._render_preview(mask_preview_path, water_mask, "Fused water mask", "Blues", vmin=0, vmax=1)
            self._render_preview(
                uncertainty_preview_path,
                disagreement,
                "Optical/SAR disagreement (uncertainty)",
                "magma",
                vmin=0,
                vmax=1,
            )
            raster_metadata = self._raster_metadata(
                optical_dataset,
                valid,
                water_mask,
                optical_threshold=optical_threshold,
                sar_threshold=sar_threshold,
                fusion_rule=rule,
                green_band=green_band,
                swir1_band=swir1_band,
                vv_band=vv_band,
                disagreement_pixel_count=int((disagreement == 1).sum()),
            )

        outputs = [
            self._output_descriptor(input_preview_path, "input_preview", run_token, {"source": "optical", "cmap": "gray"}),
            self._output_descriptor(sar_preview_path, "auxiliary_preview", run_token, {"source": "sar", "cmap": "gray"}),
            self._output_descriptor(
                index_path,
                "feature_raster",
                run_token,
                {"formula": "(green - swir1) / (green + swir1)", "nodata": self._NODATA_FLOAT},
            ),
            self._output_descriptor(index_preview_path, "feature_preview", run_token, {"cmap": "BrBG", "vmin": -1, "vmax": 1}),
            self._output_descriptor(mask_path, "classification_raster", run_token, {"fusion_rule": rule, "nodata": self._NODATA_MASK}),
            self._output_descriptor(mask_preview_path, "classification_preview", run_token, {"cmap": "Blues"}),
            self._output_descriptor(
                uncertainty_path,
                "uncertainty_raster",
                run_token,
                {"meaning": "optical_sar_disagreement", "nodata": self._NODATA_FLOAT},
            ),
            self._output_descriptor(
                uncertainty_preview_path,
                "uncertainty_preview",
                run_token,
                {"cmap": "magma", "vmin": 0, "vmax": 1},
            ),
        ]
        return self._finish_result(
            output_dir=output_dir,
            run_token=run_token,
            outputs=outputs,
            operation=self._FUSION_OPERATION,
            experiment=experiment,
            formula_spec=formula_spec,
            snapshot=snapshot,
            input_assets=[optical_asset, sar_asset],
            raster_metadata=raster_metadata,
        )

    def _finish_result(
        self,
        *,
        output_dir: Path,
        run_token: str,
        outputs: list[dict[str, Any]],
        operation: str,
        experiment: ResearchExperiment,
        formula_spec: FormulaSpec,
        snapshot: ResearchDataSnapshot,
        input_assets: list[ResearchDataAsset],
        raster_metadata: dict[str, Any],
    ) -> PythonRunResult:
        manifest = {
            "schema_version": 1,
            "run_token": run_token,
            "runner": self._runner_manifest(),
            "experiment": {
                "id": experiment.id,
                "name": experiment.name,
                "execution_mode": experiment.execution_mode,
                "parameters": experiment.parameters_json,
            },
            "formula_spec": {
                "id": formula_spec.id,
                "name": formula_spec.name,
                "version": formula_spec.version,
                "status": formula_spec.status,
                "operation": operation,
                "evidence_card_ids": formula_spec.evidence_card_ids_json,
            },
            "data_snapshot": {"id": snapshot.id, "snapshot_hash": snapshot.snapshot_hash, "asset_ids": snapshot.asset_ids_json},
            "input_assets": [
                {
                    "id": asset.id,
                    "name": asset.name,
                    "asset_kind": asset.asset_kind,
                    "source_type": asset.source_type,
                    "sha256": asset.sha256,
                }
                for asset in input_assets
            ],
            "raster_metadata": raster_metadata,
            "outputs": outputs,
        }
        manifest_path = output_dir / "run_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs.append(self._output_descriptor(manifest_path, "run_manifest", run_token, {}))
        manifest["outputs"] = outputs
        return PythonRunResult(manifest=manifest, outputs=outputs)

    def _attach_reference_validation(
        self,
        result: PythonRunResult,
        experiment: ResearchExperiment,
        snapshot: ResearchDataSnapshot,
        assets: list[ResearchDataAsset],
        output_dir: Path,
        run_token: str,
    ) -> PythonRunResult:
        """Optionally compare a generated mask with a co-registered 0/1 reference raster.

        A validation plan remains useful before labels are available, so the field
        is optional for now. When `reference_asset_id` is set, however, metrics
        and a spatial error map are generated from the frozen snapshot rather
        than relying on an unverified narrative claim.
        """
        plan = experiment.validation_plan_json or {}
        reference_asset_id = plan.get("reference_asset_id")
        if reference_asset_id is None:
            result.manifest["validation"] = {
                "status": "planned_without_reference",
                "plan": plan,
                "message": "未登记 reference_asset_id，未计算分类指标或误差图。",
            }
            self._refresh_manifest_output(result, output_dir, run_token)
            return result
        if not isinstance(reference_asset_id, int) or reference_asset_id not in snapshot.asset_ids_json:
            raise ValueError("validation_plan.reference_asset_id 必须引用当前冻结数据快照中的资产。")
        reference_asset = next((asset for asset in assets if asset.id == reference_asset_id), None)
        if reference_asset is None:
            raise ValueError("验证参考资产不存在或不属于当前冻结数据快照。")
        predicted_path = self._prediction_output_file(result, output_dir)
        reference_path = self.asset_storage.resolve_asset_uri(reference_asset.source_uri)
        with rasterio.open(predicted_path) as predicted_dataset, rasterio.open(reference_path) as reference_dataset:
            self._validate_fusion_grids(predicted_dataset, reference_dataset)
            if reference_dataset.count < 1:
                raise ValueError("验证参考栅格至少需要一个 0/1 标签波段。")
            predicted = predicted_dataset.read(1, masked=True)
            reference = reference_dataset.read(1, masked=True)
            predicted_values = predicted.filled(self._NODATA_MASK)
            reference_values = reference.astype(np.float32).filled(np.nan)
            valid = (
                ~np.ma.getmaskarray(predicted)
                & ~np.ma.getmaskarray(reference)
                & (predicted_values != self._NODATA_MASK)
                & np.isfinite(reference_values)
                & np.isin(reference_values, [0.0, 1.0])
            )
            sample_count = int(valid.sum())
            if sample_count == 0:
                raise ValueError("验证参考栅格没有可用的 0/1 标签像元。")
            actual = reference_values.astype(np.uint8)
            predicted_binary = (predicted_values == 1).astype(np.uint8)
            true_positive = int(((predicted_binary == 1) & (actual == 1) & valid).sum())
            true_negative = int(((predicted_binary == 0) & (actual == 0) & valid).sum())
            false_positive = int(((predicted_binary == 1) & (actual == 0) & valid).sum())
            false_negative = int(((predicted_binary == 0) & (actual == 1) & valid).sum())
            precision = self._safe_ratio(true_positive, true_positive + false_positive)
            recall = self._safe_ratio(true_positive, true_positive + false_negative)
            f1 = self._safe_ratio(2 * true_positive, 2 * true_positive + false_positive + false_negative)
            iou = self._safe_ratio(true_positive, true_positive + false_positive + false_negative)
            overall_accuracy = self._safe_ratio(true_positive + true_negative, sample_count)
            pixel_area = abs(
                predicted_dataset.transform.a * predicted_dataset.transform.e
                - predicted_dataset.transform.b * predicted_dataset.transform.d
            )
            metrics = {
                "schema_version": 1,
                "reference_asset_id": reference_asset.id,
                "reference_asset_sha256": reference_asset.sha256,
                "sample_count": sample_count,
                "ignored_label_pixel_count": int((~valid).sum()),
                "confusion_matrix": {
                    "true_positive": true_positive,
                    "true_negative": true_negative,
                    "false_positive": false_positive,
                    "false_negative": false_negative,
                },
                "metrics": {
                    "overall_accuracy": overall_accuracy,
                    "precision": precision,
                    "recall": recall,
                    "f1": f1,
                    "iou": iou,
                },
                "confidence_intervals": self._binary_confidence_intervals(
                    true_positive, true_negative, false_positive, false_negative
                ),
                "area": {
                    "pixel_area_crs_units2": pixel_area,
                    "predicted_water_pixel_count": int(((predicted_binary == 1) & valid).sum()),
                    "reference_water_pixel_count": int(((actual == 1) & valid).sum()),
                    "difference_pixel_count": false_positive - false_negative,
                },
                "plan": plan,
            }
            error_map = np.full(predicted_dataset.shape, self._NODATA_MASK, dtype=np.uint8)
            error_map[valid & (predicted_binary == 0) & (actual == 0)] = 0
            error_map[valid & (predicted_binary == 1) & (actual == 1)] = 1
            error_map[valid & (predicted_binary == 1) & (actual == 0)] = 2
            error_map[valid & (predicted_binary == 0) & (actual == 1)] = 3
            error_path = output_dir / "validation_error_map.tif"
            error_preview_path = output_dir / "validation_error_map_preview.png"
            self._write_raster(
                error_path, predicted_dataset.profile.copy(), error_map, dtype="uint8", nodata=self._NODATA_MASK
            )
            self._render_preview(
                error_preview_path,
                error_map,
                "Validation error map: 0 TN · 1 TP · 2 FP · 3 FN",
                "tab10",
                vmin=0,
                vmax=3,
            )

        metrics_path = output_dir / "validation_metrics.json"
        metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        result.outputs.extend(
            [
                self._output_descriptor(metrics_path, "validation_metrics", run_token, {"metrics": metrics["metrics"]}),
                self._output_descriptor(
                    error_path,
                    "validation_error_raster",
                    run_token,
                    {"codes": {"0": "TN", "1": "TP", "2": "FP", "3": "FN"}, "nodata": self._NODATA_MASK},
                ),
                self._output_descriptor(
                    error_preview_path,
                    "validation_error_preview",
                    run_token,
                    {"cmap": "tab10", "vmin": 0, "vmax": 3},
                ),
            ]
        )
        result.manifest["validation"] = {"status": "completed", **metrics}
        self._refresh_manifest_output(result, output_dir, run_token)
        return result

    def _attach_point_sample_validation(
        self,
        result: PythonRunResult,
        experiment: ResearchExperiment,
        snapshot: ResearchDataSnapshot,
        output_dir: Path,
        run_token: str,
        validation_samples: list[ResearchValidationSample],
    ) -> PythonRunResult:
        """Evaluate the classification raster at independently registered WGS84 points.

        This is deliberately a post-classification operation: the labels are never
        passed into a formula or threshold operation. A formal plan must opt in
        through ``sample_validation``; merely having a project-level sample table
        is not enough to imply that a run was validated.
        """
        plan = experiment.validation_plan_json or {}
        point_plan = plan.get("sample_validation")
        validation = result.manifest.setdefault("validation", {})
        if point_plan is None:
            validation["point_samples"] = {"status": "not_requested"}
            self._refresh_manifest_output(result, output_dir, run_token)
            return result
        if not isinstance(point_plan, dict):
            raise ValueError("validation_plan.sample_validation 必须是对象。")

        split = point_plan.get("split", "independent_test")
        if split not in {"development", "model_selection", "independent_test"}:
            raise ValueError("sample_validation.split 必须是 development、model_selection 或 independent_test。")
        min_confidence = point_plan.get("min_confidence", 0.0)
        if isinstance(min_confidence, bool) or not isinstance(min_confidence, (int, float)) or not 0 <= min_confidence <= 1:
            raise ValueError("sample_validation.min_confidence 必须是 0 到 1 之间的数值。")
        require_unconflicted = point_plan.get("require_unconflicted", True)
        if not isinstance(require_unconflicted, bool):
            raise ValueError("sample_validation.require_unconflicted 必须是布尔值。")
        minimums: dict[str, int] = {}
        for field_name in (
            "min_sample_count",
            "min_spatial_blocks",
            "min_temporal_strata",
            "min_samples_per_spatial_block",
            "min_samples_per_temporal_stratum",
        ):
            value = point_plan.get(field_name, 1)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"sample_validation.{field_name} 必须是至少为 1 的整数。")
            minimums[field_name] = value

        eligible = [
            sample
            for sample in validation_samples
            if sample.data_snapshot_id == snapshot.id
            and sample.split == split
            and sample.confidence >= float(min_confidence)
            and (not require_unconflicted or sample.conflict_status == "none")
        ]
        selection = {
            "snapshot_id": snapshot.id,
            "split": split,
            "min_confidence": float(min_confidence),
            "require_unconflicted": require_unconflicted,
            **minimums,
            "snapshot_bound_candidate_count": len(validation_samples),
            "eligible_sample_count": len(eligible),
        }
        if not eligible:
            raise ValueError("样本验证已启用，但没有符合冻结快照、划分、置信度和冲突规则的验证样本。")

        predicted_path = self._prediction_output_file(result, output_dir)
        with rasterio.open(predicted_path) as predicted_dataset:
            if predicted_dataset.crs is None:
                raise ValueError("点样本验证需要分类栅格具有 CRS，才能从 WGS84 经纬度定位样本。")
            coordinates = [(sample.longitude, sample.latitude) for sample in eligible]
            try:
                xs, ys = transform_coordinates(
                    "EPSG:4326",
                    predicted_dataset.crs,
                    [coordinate[0] for coordinate in coordinates],
                    [coordinate[1] for coordinate in coordinates],
                )
            except Exception as exc:  # noqa: BLE001 - normalize projection failures into run evidence
                raise ValueError("无法将验证样本的 WGS84 经纬度转换到分类栅格 CRS。") from exc

            predicted = predicted_dataset.read(1, masked=True)
            predicted_values = predicted.filled(self._NODATA_MASK)
            predicted_mask = np.ma.getmaskarray(predicted)
            used: list[tuple[ResearchValidationSample, int]] = []
            excluded_outside = 0
            excluded_nodata = 0
            for sample, x, y in zip(eligible, xs, ys, strict=True):
                row, column = predicted_dataset.index(x, y)
                if row < 0 or column < 0 or row >= predicted_dataset.height or column >= predicted_dataset.width:
                    excluded_outside += 1
                    continue
                if predicted_mask[row, column] or predicted_values[row, column] == self._NODATA_MASK:
                    excluded_nodata += 1
                    continue
                used.append((sample, int(predicted_values[row, column] == 1)))

        if not used:
            raise ValueError("点样本验证没有落入分类栅格的有效像元；请检查 ROI、CRS、时间与样本经纬度。")

        weight_by_id, weighting = self._resolve_point_sample_weighting(point_plan, used)
        area_stratum_by_sample, area_by_stratum, area_adjustment = self._resolve_area_adjustment(point_plan, used)
        metrics = self._point_sample_metrics(used, weight_by_id=weight_by_id)
        if area_stratum_by_sample is not None and area_by_stratum is not None:
            metrics["area_adjusted"] = self._point_sample_area_adjusted_metrics(
                used,
                area_stratum_by_sample,
                area_by_stratum,
                area_adjustment["area_unit"],
            )
        temporal_strata = self._point_sample_strata(used, "temporal_stratum", weight_by_id=weight_by_id)
        spatial_blocks = self._point_sample_strata(used, "spatial_block", weight_by_id=weight_by_id)
        if len(used) < minimums["min_sample_count"]:
            raise ValueError(
                f"点样本验证仅有 {len(used)} 个有效样本，低于研究协议声明的 "
                f"min_sample_count={minimums['min_sample_count']}。"
            )
        if len(spatial_blocks) < minimums["min_spatial_blocks"]:
            raise ValueError(
                f"点样本验证仅覆盖 {len(spatial_blocks)} 个空间块，低于研究协议声明的 "
                f"min_spatial_blocks={minimums['min_spatial_blocks']}。"
            )
        if len(temporal_strata) < minimums["min_temporal_strata"]:
            raise ValueError(
                f"点样本验证仅覆盖 {len(temporal_strata)} 个时间分层，低于研究协议声明的 "
                f"min_temporal_strata={minimums['min_temporal_strata']}。"
            )
        sparse_spatial_block = next(
            (
                (block, metrics["sample_count"])
                for block, metrics in spatial_blocks.items()
                if metrics["sample_count"] < minimums["min_samples_per_spatial_block"]
            ),
            None,
        )
        if sparse_spatial_block is not None:
            block, sample_count = sparse_spatial_block
            raise ValueError(
                f"点样本验证的空间块 {block!r} 仅有 {sample_count} 个有效样本，低于研究协议声明的 "
                f"min_samples_per_spatial_block={minimums['min_samples_per_spatial_block']}。"
            )
        sparse_temporal_stratum = next(
            (
                (stratum, metrics["sample_count"])
                for stratum, metrics in temporal_strata.items()
                if metrics["sample_count"] < minimums["min_samples_per_temporal_stratum"]
            ),
            None,
        )
        if sparse_temporal_stratum is not None:
            stratum, sample_count = sparse_temporal_stratum
            raise ValueError(
                f"点样本验证的时间分层 {stratum!r} 仅有 {sample_count} 个有效样本，低于研究协议声明的 "
                f"min_samples_per_temporal_stratum={minimums['min_samples_per_temporal_stratum']}。"
            )
        payload = {
            "schema_version": 1,
            "selection": {
                **selection,
                "used_sample_count": len(used),
                "excluded_outside_raster_count": excluded_outside,
                "excluded_nodata_count": excluded_nodata,
                "weighting": weighting,
                "area_adjustment": area_adjustment,
                "used_sample_ids": [sample.id for sample, _prediction in used],
            },
            "metrics": metrics,
            "strata": {"temporal_stratum": temporal_strata, "spatial_block": spatial_blocks},
        }
        metrics_path = output_dir / "validation_sample_metrics.json"
        metrics_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result.outputs.append(
            self._output_descriptor(
                metrics_path,
                "validation_sample_metrics",
                run_token,
                {"metrics": metrics["metrics"], "sample_count": metrics["sample_count"]},
            )
        )
        validation["point_samples"] = {"status": "completed", **payload}
        self._refresh_manifest_output(result, output_dir, run_token)
        return result

    @classmethod
    def _point_sample_strata(
        cls,
        used: list[tuple[ResearchValidationSample, int]],
        attribute: str,
        *,
        weight_by_id: dict[int, float] | None = None,
    ) -> dict[str, dict[str, Any]]:
        strata: dict[str, list[tuple[ResearchValidationSample, int]]] = {}
        for item in used:
            key = str(getattr(item[0], attribute))
            strata.setdefault(key, []).append(item)
        return {
            key: cls._point_sample_metrics(items, weight_by_id=weight_by_id)
            for key, items in sorted(strata.items())
        }

    @staticmethod
    def _point_sample_metrics(
        used: list[tuple[ResearchValidationSample, int]],
        *,
        weight_by_id: dict[int, float] | None = None,
    ) -> dict[str, Any]:
        weighted = weight_by_id is not None

        def sample_weight(sample: ResearchValidationSample) -> float | int:
            return weight_by_id[sample.id] if weighted else 1

        weight = sample_weight
        true_positive = sum(weight(sample) for sample, prediction in used if sample.label == 1 and prediction == 1)
        true_negative = sum(weight(sample) for sample, prediction in used if sample.label == 0 and prediction == 0)
        false_positive = sum(weight(sample) for sample, prediction in used if sample.label == 0 and prediction == 1)
        false_negative = sum(weight(sample) for sample, prediction in used if sample.label == 1 and prediction == 0)
        count = len(used)
        metrics = {
            "sample_count": count,
            "confusion_matrix": {
                "true_positive": true_positive,
                "true_negative": true_negative,
                "false_positive": false_positive,
                "false_negative": false_negative,
            },
            "metrics": {
                "overall_accuracy": PythonRunner._safe_ratio(true_positive + true_negative, count),
                "precision": PythonRunner._safe_ratio(true_positive, true_positive + false_positive),
                "recall": PythonRunner._safe_ratio(true_positive, true_positive + false_negative),
                "f1": PythonRunner._safe_ratio(2 * true_positive, 2 * true_positive + false_positive + false_negative),
                "iou": PythonRunner._safe_ratio(true_positive, true_positive + false_positive + false_negative),
            },
            "confidence_intervals": PythonRunner._binary_confidence_intervals(
                int(true_positive), int(true_negative), int(false_positive), int(false_negative)
            ),
        }
        if weighted:
            total_weight = float(sum(weight(sample) for sample, _prediction in used))
            sum_squared_weights = float(sum(weight(sample) ** 2 for sample, _prediction in used))
            metrics["weight_sum"] = total_weight
            metrics["effective_sample_size"] = (total_weight * total_weight / sum_squared_weights) if sum_squared_weights else 0.0
            metrics["confusion_matrix"] = {
                "true_positive": float(true_positive),
                "true_negative": float(true_negative),
                "false_positive": float(false_positive),
                "false_negative": float(false_negative),
            }
            metrics["metrics"] = {
                "overall_accuracy": PythonRunner._safe_ratio_float(true_positive + true_negative, total_weight),
                "precision": PythonRunner._safe_ratio_float(true_positive, true_positive + false_positive),
                "recall": PythonRunner._safe_ratio_float(true_positive, true_positive + false_negative),
                "f1": PythonRunner._safe_ratio_float(2 * true_positive, 2 * true_positive + false_positive + false_negative),
                "iou": PythonRunner._safe_ratio_float(true_positive, true_positive + false_positive + false_negative),
            }
            metrics["confidence_intervals"] = {
                "method": "not_reported_for_weighted_samples",
                "reason": "加权点样本需要依据真实抽样设计计算方差；未将未加权 Wilson 区间冒充设计一致区间。",
            }
        return metrics

    @classmethod
    def _resolve_point_sample_weighting(
        cls,
        point_plan: dict[str, Any],
        used: list[tuple[ResearchValidationSample, int]],
    ) -> tuple[dict[int, float] | None, dict[str, Any]]:
        weighting = point_plan.get("weighting")
        if weighting is None:
            return None, {"enabled": False}
        if not isinstance(weighting, dict):
            raise ValueError("sample_validation.weighting 必须是对象。")
        enabled = weighting.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("sample_validation.weighting.enabled 必须是布尔值。")
        if not enabled:
            return None, {"enabled": False}
        metadata_key = weighting.get("metadata_key", "sampling_weight")
        if not isinstance(metadata_key, str) or not metadata_key or len(metadata_key) > 100:
            raise ValueError("sample_validation.weighting.metadata_key 必须是 1 到 100 个字符。")
        minimum_total_weight = weighting.get("minimum_total_weight", 0.0)
        if (
            isinstance(minimum_total_weight, bool)
            or not isinstance(minimum_total_weight, (int, float))
            or not math.isfinite(float(minimum_total_weight))
            or minimum_total_weight < 0
        ):
            raise ValueError("sample_validation.weighting.minimum_total_weight 必须是非负有限数值。")
        minimum_effective_sample_size = weighting.get("minimum_effective_sample_size", 1.0)
        if (
            isinstance(minimum_effective_sample_size, bool)
            or not isinstance(minimum_effective_sample_size, (int, float))
            or not math.isfinite(float(minimum_effective_sample_size))
            or minimum_effective_sample_size < 1
        ):
            raise ValueError("sample_validation.weighting.minimum_effective_sample_size 必须是至少为 1 的有限数值。")

        weight_by_id: dict[int, float] = {}
        for sample, _prediction in used:
            raw_weight = (sample.metadata_json or {}).get(metadata_key)
            if (
                isinstance(raw_weight, bool)
                or not isinstance(raw_weight, (int, float))
                or not math.isfinite(float(raw_weight))
                or float(raw_weight) <= 0
            ):
                raise ValueError(
                    f"样本 {sample.id} 的权重 metadata.{metadata_key} 必须是正的有限数值。"
                )
            weight_by_id[sample.id] = float(raw_weight)
        total_weight = sum(weight_by_id.values())
        sum_squared_weights = sum(value * value for value in weight_by_id.values())
        effective_sample_size = (total_weight * total_weight / sum_squared_weights) if sum_squared_weights else 0.0
        if total_weight < float(minimum_total_weight):
            raise ValueError(
                f"点样本验证总权重 {total_weight:g} 低于研究协议声明的 "
                f"minimum_total_weight={float(minimum_total_weight):g}。"
            )
        if effective_sample_size < float(minimum_effective_sample_size):
            raise ValueError(
                f"点样本验证有效样本量 {effective_sample_size:g} 低于研究协议声明的 "
                f"minimum_effective_sample_size={float(minimum_effective_sample_size):g}。"
            )
        return weight_by_id, {
            "enabled": True,
            "metadata_key": metadata_key,
            "total_weight": total_weight,
            "effective_sample_size": effective_sample_size,
            "minimum_total_weight": float(minimum_total_weight),
            "minimum_effective_sample_size": float(minimum_effective_sample_size),
            "confidence_intervals": "not_reported_for_weighted_samples",
        }

    @classmethod
    def _resolve_area_adjustment(
        cls,
        point_plan: dict[str, Any],
        used: list[tuple[ResearchValidationSample, int]],
    ) -> tuple[dict[int, str] | None, dict[str, float] | None, dict[str, Any]]:
        raw_plan = point_plan.get("area_adjustment")
        if raw_plan is None:
            return None, None, {"enabled": False}
        if not isinstance(raw_plan, dict):
            raise ValueError("sample_validation.area_adjustment 必须是对象。")
        enabled = raw_plan.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("sample_validation.area_adjustment.enabled 必须是布尔值。")
        if not enabled:
            return None, None, {"enabled": False}
        weighting = point_plan.get("weighting")
        if isinstance(weighting, dict) and weighting.get("enabled", True):
            raise ValueError("area_adjustment 暂不与 weighting 同时启用；请明确选择一种抽样设计。")
        stratum_metadata_key = raw_plan.get("stratum_metadata_key", "sampling_stratum")
        area_metadata_key = raw_plan.get("area_metadata_key", "stratum_area")
        for field_name, value in (
            ("stratum_metadata_key", stratum_metadata_key),
            ("area_metadata_key", area_metadata_key),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > 100:
                raise ValueError(f"sample_validation.area_adjustment.{field_name} 必须是 1 到 100 个字符。")
        area_unit = raw_plan.get("area_unit", "unknown")
        if not isinstance(area_unit, str) or not area_unit.strip() or len(area_unit) > 40:
            raise ValueError("sample_validation.area_adjustment.area_unit 必须是 1 到 40 个字符。")
        minimum_strata = raw_plan.get("minimum_strata", 1)
        if isinstance(minimum_strata, bool) or not isinstance(minimum_strata, int) or minimum_strata < 1:
            raise ValueError("sample_validation.area_adjustment.minimum_strata 必须是至少为 1 的整数。")
        expected_strata = raw_plan.get("expected_strata", [])
        if not isinstance(expected_strata, list) or any(
            not isinstance(value, str) or not value.strip() for value in expected_strata
        ):
            raise ValueError("sample_validation.area_adjustment.expected_strata 必须是非空字符串数组。")
        if len(set(expected_strata)) != len(expected_strata):
            raise ValueError("sample_validation.area_adjustment.expected_strata 不能包含重复分层。")

        stratum_by_sample: dict[int, str] = {}
        area_by_stratum: dict[str, float] = {}
        for sample, _prediction in used:
            metadata = sample.metadata_json or {}
            stratum = metadata.get(stratum_metadata_key)
            area = metadata.get(area_metadata_key)
            if not isinstance(stratum, str) or not stratum.strip():
                raise ValueError(
                    f"样本 {sample.id} 缺少 metadata.{stratum_metadata_key} 分层标识。"
                )
            if (
                isinstance(area, bool)
                or not isinstance(area, (int, float))
                or not math.isfinite(float(area))
                or float(area) <= 0
            ):
                raise ValueError(
                    f"样本 {sample.id} 的 metadata.{area_metadata_key} 必须是正的有限面积数值。"
                )
            area_value = float(area)
            previous_area = area_by_stratum.get(stratum)
            if previous_area is not None and not math.isclose(
                previous_area, area_value, rel_tol=1e-9, abs_tol=1e-9
            ):
                raise ValueError(f"分层 {stratum!r} 的面积值必须在所有样本中保持一致。")
            stratum_by_sample[sample.id] = stratum
            area_by_stratum[stratum] = area_value

        missing_expected = sorted(set(expected_strata) - set(area_by_stratum))
        if missing_expected:
            raise ValueError(f"面积调整缺少协议声明的分层样本：{', '.join(missing_expected[:10])}。")
        if len(area_by_stratum) < minimum_strata:
            raise ValueError(
                f"面积调整仅覆盖 {len(area_by_stratum)} 个分层，低于 minimum_strata={minimum_strata}。"
            )
        total_area = sum(area_by_stratum.values())
        if not math.isfinite(total_area) or total_area <= 0:
            raise ValueError("面积调整的分层总面积必须是正的有限数值。")
        return stratum_by_sample, area_by_stratum, {
            "enabled": True,
            "stratum_metadata_key": stratum_metadata_key,
            "area_metadata_key": area_metadata_key,
            "area_unit": area_unit,
            "expected_strata": expected_strata,
            "minimum_strata": minimum_strata,
            "strata_count": len(area_by_stratum),
            "total_area": total_area,
            "confidence_intervals": "not_reported_for_design_based_area_adjustment",
        }

    @classmethod
    def _point_sample_area_adjusted_metrics(
        cls,
        used: list[tuple[ResearchValidationSample, int]],
        stratum_by_sample: dict[int, str],
        area_by_stratum: dict[str, float],
        area_unit: str,
    ) -> dict[str, Any]:
        samples_by_stratum: dict[str, list[tuple[ResearchValidationSample, int]]] = {}
        for sample, prediction in used:
            samples_by_stratum.setdefault(stratum_by_sample[sample.id], []).append((sample, prediction))

        area_confusion = {
            "true_positive": 0.0,
            "true_negative": 0.0,
            "false_positive": 0.0,
            "false_negative": 0.0,
        }
        strata: dict[str, dict[str, Any]] = {}
        for stratum, area in sorted(area_by_stratum.items()):
            stratum_samples = samples_by_stratum.get(stratum, [])
            if not stratum_samples:
                raise ValueError(f"面积调整分层 {stratum!r} 没有有效像元样本。")
            sample_count = len(stratum_samples)
            confusion_counts = {
                "true_positive": sum(sample.label == 1 and prediction == 1 for sample, prediction in stratum_samples),
                "true_negative": sum(sample.label == 0 and prediction == 0 for sample, prediction in stratum_samples),
                "false_positive": sum(sample.label == 0 and prediction == 1 for sample, prediction in stratum_samples),
                "false_negative": sum(sample.label == 1 and prediction == 0 for sample, prediction in stratum_samples),
            }
            area_values = {
                key: area * float(value) / sample_count for key, value in confusion_counts.items()
            }
            for key, value in area_values.items():
                area_confusion[key] += value
            reference_positive_area = area_values["true_positive"] + area_values["false_negative"]
            predicted_positive_area = area_values["true_positive"] + area_values["false_positive"]
            strata[stratum] = {
                "area": area,
                "sample_count": sample_count,
                "sample_confusion_matrix": confusion_counts,
                "area_confusion_matrix": area_values,
                "reference_positive_area": reference_positive_area,
                "predicted_positive_area": predicted_positive_area,
            }

        total_area = sum(area_confusion.values())
        metrics = {
            "overall_accuracy": cls._safe_ratio_float(
                area_confusion["true_positive"] + area_confusion["true_negative"], total_area
            ),
            "precision": cls._safe_ratio_float(
                area_confusion["true_positive"],
                area_confusion["true_positive"] + area_confusion["false_positive"],
            ),
            "recall": cls._safe_ratio_float(
                area_confusion["true_positive"],
                area_confusion["true_positive"] + area_confusion["false_negative"],
            ),
            "f1": cls._safe_ratio_float(
                2 * area_confusion["true_positive"],
                2 * area_confusion["true_positive"]
                + area_confusion["false_positive"]
                + area_confusion["false_negative"],
            ),
            "iou": cls._safe_ratio_float(
                area_confusion["true_positive"],
                area_confusion["true_positive"]
                + area_confusion["false_positive"]
                + area_confusion["false_negative"],
            ),
        }
        reference_positive_area = area_confusion["true_positive"] + area_confusion["false_negative"]
        predicted_positive_area = area_confusion["true_positive"] + area_confusion["false_positive"]
        return {
            "method": "stratified_area_adjusted",
            "area_unit": area_unit,
            "total_area": total_area,
            "strata": strata,
            "confusion_matrix": area_confusion,
            "metrics": metrics,
            "reference_positive_area": reference_positive_area,
            "predicted_positive_area": predicted_positive_area,
            "absolute_area_error": abs(predicted_positive_area - reference_positive_area),
            "confidence_intervals": {
                "method": "not_reported_for_design_based_area_adjustment",
                "reason": "分层面积调整结果需要依据真实抽样设计计算方差；未将普通 Wilson 区间冒充设计一致区间。",
            },
        }

    def _refresh_manifest_output(self, result: PythonRunResult, output_dir: Path, run_token: str) -> None:
        result.outputs[:] = [output for output in result.outputs if output.get("kind") != "run_manifest"]
        result.manifest["outputs"] = result.outputs
        manifest_path = output_dir / "run_manifest.json"
        manifest_path.write_text(json.dumps(result.manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result.outputs.append(self._output_descriptor(manifest_path, "run_manifest", run_token, {}))
        result.manifest["outputs"] = result.outputs

    @staticmethod
    def _safe_output_file(output_dir: Path, file_name: str) -> Path:
        path = (output_dir / Path(file_name).name).resolve()
        if not path.is_relative_to(output_dir.resolve()) or path.is_symlink() or not path.is_file():
            raise ValueError("验证所需的分类运行产物不存在。")
        return path

    @classmethod
    def _prediction_output_file(cls, result: PythonRunResult, output_dir: Path) -> Path:
        """Resolve the classification raster declared by a runner manifest.

        PythonRunner keeps the historical ``water_mask.tif`` default.  The
        project IDLRunner may declare another basename, but validation still
        receives the same strict path/symlink checks and only a GeoTIFF is
        accepted as a classification raster.
        """
        file_name = str(result.manifest.get("prediction_output_file") or "water_mask.tif")
        if Path(file_name).name != file_name or Path(file_name).suffix.lower() not in {".tif", ".tiff"}:
            raise ValueError("验证所需的 prediction_output_file 必须是当前运行中的 GeoTIFF 文件名。")
        return cls._safe_output_file(output_dir, file_name)

    @staticmethod
    def _safe_ratio(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    @staticmethod
    def _safe_ratio_float(numerator: float, denominator: float) -> float | None:
        return numerator / denominator if denominator else None

    @staticmethod
    def _wilson_interval(successes: int, trials: int) -> dict[str, float | int] | None:
        """Return a deterministic two-sided 95% Wilson interval.

        This is intentionally labelled as an unweighted pixel/sample interval;
        it must not be interpreted as a design-weighted area estimate or a
        substitute for a study-specific spatial sampling variance analysis.
        """
        if trials <= 0:
            return None
        z = 1.959963984540054
        proportion = successes / trials
        z_squared = z * z
        denominator = 1.0 + z_squared / trials
        center = (proportion + z_squared / (2.0 * trials)) / denominator
        half_width = (
            z
            * (
                proportion * (1.0 - proportion) / trials
                + z_squared / (4.0 * trials * trials)
            )
            ** 0.5
            / denominator
        )
        return {
            "lower": max(0.0, center - half_width),
            "upper": min(1.0, center + half_width),
            "successes": successes,
            "trials": trials,
        }

    @classmethod
    def _binary_confidence_intervals(
        cls, true_positive: int, true_negative: int, false_positive: int, false_negative: int
    ) -> dict[str, Any]:
        return {
            "method": "wilson_95_unweighted",
            "confidence_level": 0.95,
            "assumption": "像元/点样本独立且未加权；不代表复杂抽样设计下的面积或空间方差区间。",
            "overall_accuracy": cls._wilson_interval(
                true_positive + true_negative,
                true_positive + true_negative + false_positive + false_negative,
            ),
            "precision": cls._wilson_interval(true_positive, true_positive + false_positive),
            "recall": cls._wilson_interval(true_positive, true_positive + false_negative),
            "iou": cls._wilson_interval(true_positive, true_positive + false_positive + false_negative),
        }

    @staticmethod
    def _runner_manifest() -> dict[str, str]:
        return {
            "type": "python",
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "numpy_version": np.__version__,
            "rasterio_version": rasterio.__version__,
            "matplotlib_version": matplotlib.__version__,
        }

    @staticmethod
    def _raster_metadata(dataset: Any, valid: np.ndarray, water_mask: np.ndarray, **details: Any) -> dict[str, Any]:
        return {
            "crs": dataset.crs.to_string() if dataset.crs else None,
            "transform": list(dataset.transform)[:6],
            "width": dataset.width,
            "height": dataset.height,
            "input_nodata": dataset.nodata,
            "valid_pixel_count": int(valid.sum()),
            "nodata_pixel_count": int(valid.size - valid.sum()),
            "water_pixel_count": int((water_mask == 1).sum()),
            **details,
        }

    def _resolve_asset(
        self, spec: dict[str, Any], key: str, snapshot: ResearchDataSnapshot, assets: list[ResearchDataAsset]
    ) -> ResearchDataAsset:
        value = spec.get(key)
        if not isinstance(value, int) or value not in set(snapshot.asset_ids_json):
            raise ValueError(f"FormulaSpec 的 {key} 必须属于当前数据快照。")
        asset = next((item for item in assets if item.id == value), None)
        if asset is None or asset.asset_kind != "raster":
            raise ValueError(f"FormulaSpec 的 {key} 必须引用当前数据快照中的栅格资产。")
        return asset

    @staticmethod
    def _normalize_band_math_expression(value: object) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 1000:
            raise ValueError("safe_band_math_threshold 的 expression 必须是 1–1000 个字符的非空公式。")
        return value.strip()

    @staticmethod
    def _parse_band_math_expression(value: str) -> ast.Expression:
        try:
            expression = ast.parse(value, mode="eval")
        except SyntaxError as exc:
            raise ValueError("safe_band_math_threshold 的 expression 不是合法数学表达式。") from exc
        if sum(1 for _ in ast.walk(expression)) > 100:
            raise ValueError("safe_band_math_threshold 的 expression 过于复杂。")
        return expression

    @staticmethod
    def _finite_band_math_number(value: object, name: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"safe_band_math_threshold 的 {name} 必须是有限数值。")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"safe_band_math_threshold 的 {name} 必须是有限数值。") from exc
        if not np.isfinite(number) or abs(number) > 1e12:
            raise ValueError(f"safe_band_math_threshold 的 {name} 必须是绝对值不超过 1e12 的有限数值。")
        return number

    def _resolve_band_math_inputs(self, value: object) -> dict[str, dict[str, float | int]]:
        if not isinstance(value, dict) or not value or len(value) > 16:
            raise ValueError("safe_band_math_threshold 必须声明 1–16 个输入波段。")
        inputs: dict[str, dict[str, float | int]] = {}
        reserved_names = {"abs", "sqrt", "log", "exp", "clip", "minimum", "maximum"}
        for name, definition in value.items():
            if (
                not isinstance(name, str)
                or not name.isidentifier()
                or name.startswith("_")
                or name in reserved_names
            ):
                raise ValueError("safe_band_math_threshold 的输入名称必须是非保留 Python 标识符。")
            if not isinstance(definition, dict):
                raise ValueError(f"safe_band_math_threshold 的输入 {name} 必须是对象。")
            band = self._resolve_band(definition, name)
            inputs[name] = {
                "band": band,
                "scale": self._finite_band_math_number(definition.get("scale", 1.0), f"inputs.{name}.scale"),
                "offset": self._finite_band_math_number(definition.get("offset", 0.0), f"inputs.{name}.offset"),
            }
        return inputs

    def _resolve_band_math_parameters(
        self, defaults: object, experiment_parameters: dict[str, Any], input_names: set[str]
    ) -> dict[str, float]:
        if not isinstance(defaults, dict) or not defaults or len(defaults) > 32:
            raise ValueError("safe_band_math_threshold 必须声明 1–32 个默认数值 parameters。")
        values: dict[str, float] = {}
        reserved_names = {"abs", "sqrt", "log", "exp", "clip", "minimum", "maximum"}
        for name, default in defaults.items():
            if (
                not isinstance(name, str)
                or not name.isidentifier()
                or name.startswith("_")
                or name in input_names
                or name in reserved_names
            ):
                raise ValueError("safe_band_math_threshold 的参数名称必须是不与输入/函数冲突的标识符。")
            raw_value = experiment_parameters.get(name, default)
            values[name] = self._finite_band_math_number(raw_value, f"parameters.{name}")
        return values

    @staticmethod
    def _evaluate_band_math_expression(expression: ast.Expression, values: dict[str, np.ndarray | float]) -> np.ndarray | float:
        allowed_functions = {
            "abs": (np.abs, 1),
            "sqrt": (np.sqrt, 1),
            "log": (np.log, 1),
            "exp": (np.exp, 1),
            "clip": (np.clip, 3),
            "minimum": (np.minimum, 2),
            "maximum": (np.maximum, 2),
        }

        def evaluate(node: ast.AST) -> np.ndarray | float:
            if isinstance(node, ast.Constant):
                if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                    raise ValueError("安全波段数学公式只允许有限数值常量。")
                return PythonRunner._finite_band_math_number(node.value, "常量")
            if isinstance(node, ast.Name):
                if node.id not in values:
                    raise ValueError(f"安全波段数学公式引用了未声明的名称：{node.id}。")
                return values[node.id]
            if isinstance(node, ast.UnaryOp):
                operand = evaluate(node.operand)
                if isinstance(node.op, ast.UAdd):
                    return operand
                if isinstance(node.op, ast.USub):
                    return -operand
                raise ValueError("安全波段数学公式不允许该一元运算。")
            if isinstance(node, ast.BinOp):
                left, right = evaluate(node.left), evaluate(node.right)
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, ast.Div):
                    return np.divide(left, right)
                if isinstance(node.op, ast.Pow):
                    return np.power(left, right)
                raise ValueError("安全波段数学公式不允许该二元运算。")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                function = allowed_functions.get(node.func.id)
                if function is None or node.keywords or len(node.args) != function[1]:
                    raise ValueError(f"安全波段数学公式不允许函数调用：{node.func.id}。")
                return function[0](*(evaluate(argument) for argument in node.args))
            raise ValueError("安全波段数学公式只允许白名单数学运算、函数和已声明名称。")

        return evaluate(expression.body)

    @staticmethod
    def _resolve_threshold(
        experiment: ResearchExperiment,
        spec: dict[str, Any],
        name: str,
        *,
        default: float,
        minimum: float,
        maximum: float,
    ) -> float:
        raw = (experiment.parameters_json or {}).get(name, (spec.get("parameters") or {}).get(name, default))
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} 必须是数值。") from exc
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} 必须位于 [{minimum}, {maximum}]。")
        return value

    @staticmethod
    def _resolve_integer_parameter(
        experiment: ResearchExperiment,
        spec: dict[str, Any],
        name: str,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        raw = (experiment.parameters_json or {}).get(name, (spec.get("parameters") or {}).get(name, default))
        if isinstance(raw, bool):
            raise ValueError(f"{name} 必须是整数。")
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} 必须是整数。") from exc
        if value != raw or not minimum <= value <= maximum:
            raise ValueError(f"{name} 必须位于 [{minimum}, {maximum}] 的整数范围内。")
        return value

    @staticmethod
    def _otsu_threshold(values: np.ndarray, bins: int, lower: float, upper: float) -> float:
        finite = np.asarray(values, dtype=np.float32)
        finite = finite[np.isfinite(finite) & (finite >= lower) & (finite <= upper)]
        if finite.size == 0:
            raise ValueError("自适应阈值计算没有位于指定范围内的有效指数像元。")
        if np.allclose(finite.min(), finite.max()):
            return float(finite[0])
        histogram, edges = np.histogram(finite, bins=bins, range=(lower, upper))
        probabilities = histogram.astype(np.float64) / histogram.sum()
        omega = np.cumsum(probabilities)
        centers = (edges[:-1] + edges[1:]) / 2
        means = np.cumsum(probabilities * centers)
        total_mean = means[-1]
        denominator = omega * (1 - omega)
        between_class_variance = np.zeros_like(denominator)
        valid = denominator > np.finfo(np.float64).eps
        between_class_variance[valid] = ((total_mean * omega[valid] - means[valid]) ** 2) / denominator[valid]
        return float(centers[int(np.argmax(between_class_variance))])

    @staticmethod
    def _validate_fusion_grids(optical_dataset: Any, sar_dataset: Any) -> None:
        if (
            optical_dataset.width != sar_dataset.width
            or optical_dataset.height != sar_dataset.height
            or optical_dataset.crs != sar_dataset.crs
            or optical_dataset.transform != sar_dataset.transform
        ):
            raise ValueError("栅格对齐要求输入与参考具有相同尺寸、CRS 与仿射变换；请先对齐数据（透明融合和参考验证均适用）。")

    def _resolve_spec(
        self,
        formula_spec: FormulaSpec,
        experiment: ResearchExperiment,
        snapshot: ResearchDataSnapshot,
        assets: list[ResearchDataAsset],
    ) -> tuple[str, int, int, int, float]:
        spec = formula_spec.spec_json or {}
        operation = str(spec.get("operation") or self._OPERATION)
        asset_ids = set(snapshot.asset_ids_json)
        requested_asset_id = spec.get("input_asset_id")
        if requested_asset_id is None:
            raster_assets = [asset for asset in assets if asset.id in asset_ids and asset.asset_kind == "raster"]
            if len(raster_assets) != 1:
                raise ValueError("FormulaSpec 必须提供 input_asset_id，或数据快照中恰好存在一个栅格资产。")
            input_asset_id = raster_assets[0].id
        elif isinstance(requested_asset_id, int) and requested_asset_id in asset_ids:
            input_asset_id = requested_asset_id
        else:
            raise ValueError("FormulaSpec 的 input_asset_id 必须属于当前数据快照。")

        inputs = spec.get("inputs") or {}
        green_band = self._resolve_band(inputs.get("green"), "green")
        swir1_band = self._resolve_band(inputs.get("swir1"), "swir1")
        parameter_values = experiment.parameters_json or {}
        default_parameters = spec.get("parameters") or {}
        raw_threshold = parameter_values.get("threshold", default_parameters.get("threshold", 0.0))
        try:
            threshold = float(raw_threshold)
        except (TypeError, ValueError) as exc:
            raise ValueError("threshold 必须是数值。") from exc
        if not -1.0 <= threshold <= 1.0:
            raise ValueError("归一化差异阈值必须位于 [-1, 1]。")
        return operation, input_asset_id, green_band, swir1_band, threshold

    @staticmethod
    def _resolve_band(value: Any, name: str) -> int:
        if isinstance(value, dict):
            value = value.get("band")
        if not isinstance(value, int) or value < 1:
            raise ValueError(f"FormulaSpec 必须为 {name} 提供从 1 开始的整数波段编号。")
        return value

    @staticmethod
    def _write_raster(path: Path, base_profile: dict, array: np.ndarray, *, dtype: str, nodata: float | int) -> None:
        profile = base_profile.copy()
        # A source can carry tiled block dimensions that are invalid for a small
        # output raster. Let GDAL select strip/block layout for the new product.
        profile.pop("blockxsize", None)
        profile.pop("blockysize", None)
        profile.pop("tiled", None)
        profile.update(driver="GTiff", count=1, dtype=dtype, nodata=nodata)
        with rasterio.open(path, "w", **profile) as destination:
            destination.write(array, 1)

    @staticmethod
    def _render_preview(
        path: Path,
        array: np.ndarray,
        title: str,
        cmap: str,
        *,
        vmin: float | None = None,
        vmax: float | None = None,
    ) -> None:
        data = np.asarray(array, dtype=np.float32)
        masked = np.ma.masked_where(~np.isfinite(data) | (data == PythonRunner._NODATA_FLOAT) | (data == PythonRunner._NODATA_MASK), data)
        if vmin is None or vmax is None:
            valid_values = masked.compressed()
            if valid_values.size:
                lower, upper = np.percentile(valid_values, [2, 98])
                vmin = lower if vmin is None else vmin
                vmax = upper if vmax is None else vmax
                if vmin == vmax:
                    vmax = vmin + 1
        figure, axis = plt.subplots(figsize=(6, 5), dpi=150)
        image = axis.imshow(masked, cmap=cmap, vmin=vmin, vmax=vmax)
        axis.set_title(title)
        axis.set_axis_off()
        figure.colorbar(image, ax=axis, shrink=0.8)
        figure.tight_layout()
        figure.savefig(path, bbox_inches="tight")
        plt.close(figure)

    @staticmethod
    def _output_descriptor(path: Path, kind: str, run_token: str, metadata: dict[str, Any]) -> dict[str, Any]:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {
            "kind": kind,
            "file_name": path.name,
            "uri": f"research://runs/{run_token}/{path.name}",
            "sha256": digest,
            "size": path.stat().st_size,
            "metadata": metadata,
        }
