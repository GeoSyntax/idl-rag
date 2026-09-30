from __future__ import annotations

import pytest

from app.services.research_run_comparison_service import ResearchRunComparisonService


def _manifest(*, snapshot_hash: str = "same-snapshot", execution_mode: str = "formal") -> dict[str, object]:
    return {
        "experiment": {"execution_mode": execution_mode},
        "formula_spec": {"id": 1, "version": 1, "status": "frozen", "operation": "baseline"},
        "data_snapshot": {"snapshot_hash": snapshot_hash},
        "validation": {
            "metrics": {"overall_accuracy": 0.8, "precision": 0.7, "recall": 0.6, "f1": 0.64, "iou": 0.47}
        },
    }


def _plan() -> dict[str, object]:
    return {
        "split": "spatiotemporal-holdout",
        "reference_asset_id": 10,
        "metrics": ["f1", "iou"],
    }


def test_formal_run_comparison_reports_candidate_minus_baseline() -> None:
    result = ResearchRunComparisonService().compare(
        run_id=2,
        reference_run_id=1,
        run_manifest={
            **_manifest(),
            "formula_spec": {"id": 2, "version": 1, "status": "frozen", "operation": "candidate"},
            "validation": {
                "metrics": {"overall_accuracy": 0.85, "precision": 0.75, "recall": 0.65, "f1": 0.70, "iou": 0.54}
            },
        },
        reference_manifest=_manifest(),
        run_validation_plan=_plan(),
        reference_validation_plan=_plan(),
    )
    assert result["status"] == "compared"
    assert result["comparable"] is True
    f1 = next(item for item in result["metrics"] if item["metric"] == "f1")
    assert f1["baseline"] == 0.64
    assert f1["candidate"] == 0.70
    assert f1["delta"] == pytest.approx(0.06)
    assert "科学优越性" in result["notice"]


def test_formal_run_comparison_rejects_snapshot_or_validation_mismatch() -> None:
    result = ResearchRunComparisonService().compare(
        run_id=2,
        reference_run_id=1,
        run_manifest=_manifest(snapshot_hash="candidate-snapshot"),
        reference_manifest=_manifest(snapshot_hash="baseline-snapshot"),
        run_validation_plan=_plan(),
        reference_validation_plan={**_plan(), "reference_asset_id": 11},
    )
    assert result["status"] == "failed"
    assert result["comparable"] is False
    assert any("同一个冻结 DataSnapshot" in issue for issue in result["issues"])
    assert any("验证方案不一致" in issue for issue in result["issues"])


def test_formal_run_comparison_rejects_preview() -> None:
    with pytest.raises(ValueError, match="必须是 formal"):
        ResearchRunComparisonService().compare(
            run_id=2,
            reference_run_id=1,
            run_manifest=_manifest(execution_mode="preview"),
            reference_manifest=_manifest(),
            run_validation_plan=_plan(),
            reference_validation_plan=_plan(),
        )
