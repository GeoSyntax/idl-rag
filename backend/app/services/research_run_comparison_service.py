from __future__ import annotations

import json
import math
from typing import Any


class ResearchRunComparisonService:
    """Compare formal baseline/candidate metrics without claiming significance."""

    _METRIC_NAMES = ("overall_accuracy", "precision", "recall", "f1", "iou")

    def compare(
        self,
        *,
        run_id: int,
        reference_run_id: int,
        run_manifest: dict[str, Any],
        reference_manifest: dict[str, Any],
        run_validation_plan: dict[str, Any],
        reference_validation_plan: dict[str, Any],
    ) -> dict[str, Any]:
        issues: list[str] = []
        metrics: list[dict[str, Any]] = []
        if run_id == reference_run_id:
            issues.append("基线运行不能是目标运行自身。")
        self._validate_formal_run(run_manifest, "候选")
        self._validate_formal_run(reference_manifest, "基线")
        run_snapshot = (run_manifest.get("data_snapshot") or {}).get("snapshot_hash")
        reference_snapshot = (reference_manifest.get("data_snapshot") or {}).get("snapshot_hash")
        if not run_snapshot or run_snapshot != reference_snapshot:
            issues.append("基线与候选必须使用同一个冻结 DataSnapshot。")
        if self._canonical(run_validation_plan) != self._canonical(reference_validation_plan):
            issues.append("基线与候选的验证方案不一致；请固定相同的时空划分、参考来源和样本设计。")

        run_sections = self._metric_sections(run_manifest)
        reference_sections = self._metric_sections(reference_manifest)
        for scope in sorted(set(run_sections) & set(reference_sections)):
            run_metrics = run_sections[scope]
            reference_metrics = reference_sections[scope]
            for metric in self._METRIC_NAMES:
                candidate_value = run_metrics.get(metric)
                baseline_value = reference_metrics.get(metric)
                if not self._finite_number(candidate_value) or not self._finite_number(baseline_value):
                    continue
                metrics.append(
                    {
                        "scope": scope,
                        "metric": metric,
                        "baseline": float(baseline_value),
                        "candidate": float(candidate_value),
                        "delta": float(candidate_value) - float(baseline_value),
                        "direction": "higher_is_better",
                    }
                )
        if not metrics:
            issues.append("基线与候选没有共同的有限验证指标。")
        comparable = not issues
        return {
            "run_id": run_id,
            "reference_run_id": reference_run_id,
            "run_kind": "formal_comparison",
            "status": "compared" if comparable else "failed",
            "comparable": comparable,
            "snapshot_hash": run_snapshot if run_snapshot == reference_snapshot else None,
            "compared_metric_count": len(metrics),
            "metrics": metrics,
            "issues": issues,
            "notice": (
                "已计算候选减基线的描述性指标差异；差异不等于统计显著性或科学优越性。"
                if comparable
                else "基线/候选比较未通过；请先修复冻结数据、验证方案或指标覆盖问题。"
            ),
        }

    @classmethod
    def _validate_formal_run(cls, manifest: dict[str, Any], label: str) -> None:
        execution_mode = ((manifest.get("experiment") or {}).get("execution_mode"))
        if execution_mode != "formal":
            raise ValueError(f"{label}运行必须是 formal。")
        if manifest.get("run_kind") == "parameter_sweep":
            raise ValueError(f"{label}参数候选实验不能作为正式基线/候选比较。")

    @classmethod
    def _metric_sections(cls, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
        validation = manifest.get("validation") or {}
        sections: dict[str, dict[str, Any]] = {}
        direct_metrics = validation.get("metrics")
        if isinstance(direct_metrics, dict) and any(name in direct_metrics for name in cls._METRIC_NAMES):
            sections["raster_or_run"] = direct_metrics
        point_samples = validation.get("point_samples")
        if isinstance(point_samples, dict):
            point_metrics = (point_samples.get("metrics") or {}).get("metrics")
            if isinstance(point_metrics, dict):
                sections["point_samples"] = point_metrics
            area_metrics = ((point_samples.get("metrics") or {}).get("area_adjusted") or {}).get("metrics")
            if isinstance(area_metrics, dict):
                sections["area_adjusted"] = area_metrics
        return sections

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _finite_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
