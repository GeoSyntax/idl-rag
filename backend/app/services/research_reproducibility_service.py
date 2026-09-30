# ruff: noqa: E402
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.services.geospatial_runtime import configure_bundled_rasterio_data

configure_bundled_rasterio_data()

import numpy as np
import rasterio


class ResearchReproducibilityService:
    """Compare two completed runs without accessing protected input assets."""

    _IGNORED_OUTPUT_KINDS = {"run_manifest", "research_evidence_package"}

    def compare(
        self,
        *,
        target_run_id: int,
        reference_run_id: int,
        target_manifest: dict[str, Any],
        reference_manifest: dict[str, Any],
        target_outputs: list[dict[str, Any]],
        reference_outputs: list[dict[str, Any]],
        target_dir: Path,
        reference_dir: Path,
        absolute_tolerance: float,
        relative_tolerance: float,
    ) -> dict[str, Any]:
        issues: list[str] = []
        exact_matches: list[str] = []
        raster_comparisons: list[dict[str, Any]] = []

        self._validate_context(target_manifest, reference_manifest, issues)
        target_by_key = self._output_map(target_outputs)
        reference_by_key = self._output_map(reference_outputs)
        output_keys = sorted(set(target_by_key) | set(reference_by_key))
        compared_output_count = 0

        for key in output_keys:
            label = f"{key[0]}:{key[1]}"
            target_output = target_by_key.get(key)
            reference_output = reference_by_key.get(key)
            if target_output is None or reference_output is None:
                issues.append(f"运行产物集合不一致：{label}。")
                continue
            target_path = self._safe_output_path(target_dir, str(target_output.get("file_name") or ""))
            reference_path = self._safe_output_path(reference_dir, str(reference_output.get("file_name") or ""))
            if target_path is None or reference_path is None:
                issues.append(f"运行产物路径不存在或不安全：{label}。")
                continue
            compared_output_count += 1
            suffix = target_path.suffix.lower()
            if suffix in {".tif", ".tiff"}:
                comparison = self._compare_raster(
                    target_path,
                    reference_path,
                    absolute_tolerance=absolute_tolerance,
                    relative_tolerance=relative_tolerance,
                )
                comparison["kind"] = key[0]
                comparison["file_name"] = key[1]
                raster_comparisons.append(comparison)
                if not comparison["matched"]:
                    issues.extend(f"{label}：{issue}" for issue in comparison["issues"])
            elif suffix == ".json":
                if self._canonical_json(target_path) == self._canonical_json(reference_path):
                    exact_matches.append(label)
                else:
                    issues.append(f"JSON 产物内容不一致：{label}。")
            else:
                if self._sha256(target_path) == self._sha256(reference_path):
                    exact_matches.append(label)
                else:
                    issues.append(f"非数值产物摘要不一致：{label}。")

        matched = not issues and compared_output_count == len(output_keys)
        return {
            "run_id": target_run_id,
            "reference_run_id": reference_run_id,
            "status": "matched" if matched else "failed",
            "matched": matched,
            "absolute_tolerance": absolute_tolerance,
            "relative_tolerance": relative_tolerance,
            "compared_output_count": compared_output_count,
            "exact_matches": exact_matches,
            "raster_comparisons": raster_comparisons,
            "issues": issues,
            "notice": (
                "两次运行的冻结上下文和登记产物均在给定容差内一致。"
                if matched
                else "重跑一致性校验未通过；请检查冻结上下文、产物差异和容差后再形成结论。"
            ),
        }

    @classmethod
    def _validate_context(
        cls,
        target_manifest: dict[str, Any],
        reference_manifest: dict[str, Any],
        issues: list[str],
    ) -> None:
        target_experiment = target_manifest.get("experiment") or {}
        reference_experiment = reference_manifest.get("experiment") or {}
        if target_experiment.get("execution_mode") != "formal" or reference_experiment.get("execution_mode") != "formal":
            issues.append("重跑一致性比较只允许 formal 运行；preview 结果不能作为正式可复现证据。")
        if target_manifest.get("run_kind") == "parameter_sweep" or reference_manifest.get("run_kind") == "parameter_sweep":
            issues.append("参数候选实验不能作为正式重跑一致性基准。")
        for section, keys in {
            "experiment": ("id", "parameters"),
            "formula_spec": ("id", "version", "status", "operation"),
            "data_snapshot": ("id", "snapshot_hash", "asset_ids"),
        }.items():
            target_section = target_manifest.get(section) or {}
            reference_section = reference_manifest.get(section) or {}
            for key in keys:
                if target_section.get(key) != reference_section.get(key):
                    issues.append(f"冻结上下文不一致：{section}.{key}。")
        if (target_manifest.get("runner") or {}) != (reference_manifest.get("runner") or {}):
            issues.append("执行器环境记录不一致。")

    @classmethod
    def _output_map(cls, outputs: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for output in outputs:
            kind = str(output.get("kind") or "")
            file_name = str(output.get("file_name") or "")
            if kind in cls._IGNORED_OUTPUT_KINDS or not file_name:
                continue
            result[(kind, file_name)] = output
        return result

    @staticmethod
    def _safe_output_path(output_dir: Path, file_name: str) -> Path | None:
        safe_name = Path(file_name).name
        if not safe_name or safe_name != file_name:
            return None
        path = (output_dir / safe_name).resolve()
        if not path.is_relative_to(output_dir.resolve()) or path.is_symlink() or not path.is_file():
            return None
        return path

    @classmethod
    def _compare_raster(
        cls,
        target_path: Path,
        reference_path: Path,
        *,
        absolute_tolerance: float,
        relative_tolerance: float,
    ) -> dict[str, Any]:
        issues: list[str] = []
        try:
            with rasterio.open(target_path) as target, rasterio.open(reference_path) as reference:
                if target.count != reference.count:
                    issues.append(f"波段数不一致（{target.count} != {reference.count}）。")
                if (target.width, target.height) != (reference.width, reference.height):
                    issues.append("栅格宽高不一致。")
                if target.crs != reference.crs:
                    issues.append("CRS 不一致。")
                if target.transform != reference.transform:
                    issues.append("仿射变换不一致。")
                target_data = target.read(masked=True).astype(np.float64)
                reference_data = reference.read(masked=True).astype(np.float64)
                target_values = target_data.filled(np.nan)
                reference_values = reference_data.filled(np.nan)
                target_valid = ~np.ma.getmaskarray(target_data) & np.isfinite(target_values)
                reference_valid = ~np.ma.getmaskarray(reference_data) & np.isfinite(reference_values)
                if target_valid.shape != reference_valid.shape:
                    issues.append("栅格有效像元掩膜形状不一致。")
                    return {
                        "matched": False,
                        "issues": issues,
                        "common_valid_pixel_count": 0,
                        "nodata_mismatch_pixel_count": 0,
                        "exceed_tolerance_pixel_count": 0,
                        "max_absolute_difference": None,
                        "mean_absolute_difference": None,
                    }
                nodata_mismatch = int(np.count_nonzero(target_valid ^ reference_valid))
                common_valid = target_valid & reference_valid
                if nodata_mismatch:
                    issues.append(f"NoData/有效像元掩膜不一致（{nodata_mismatch} 个像元）。")
                if not common_valid.any():
                    issues.append("没有共同有效像元。")
                    return {
                        "matched": False,
                        "issues": issues,
                        "common_valid_pixel_count": 0,
                        "nodata_mismatch_pixel_count": nodata_mismatch,
                        "exceed_tolerance_pixel_count": 0,
                        "max_absolute_difference": None,
                        "mean_absolute_difference": None,
                    }
                difference = np.abs(target_values - reference_values)
                threshold = absolute_tolerance + relative_tolerance * np.abs(reference_values)
                exceed = common_valid & (difference > threshold)
                exceed_count = int(np.count_nonzero(exceed))
                if exceed_count:
                    issues.append(f"超过容差的像元数为 {exceed_count}。")
                common_difference = difference[common_valid]
                return {
                    "matched": not issues,
                    "issues": issues,
                    "common_valid_pixel_count": int(np.count_nonzero(common_valid)),
                    "nodata_mismatch_pixel_count": nodata_mismatch,
                    "exceed_tolerance_pixel_count": exceed_count,
                    "max_absolute_difference": float(np.max(common_difference)),
                    "mean_absolute_difference": float(np.mean(common_difference)),
                }
        except (OSError, rasterio.errors.RasterioIOError) as exc:
            return {"matched": False, "issues": [f"栅格读取失败：{str(exc)[:300]}。"]}

    @staticmethod
    def _canonical_json(path: Path) -> str:
        value = json.loads(path.read_text(encoding="utf-8"))
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
