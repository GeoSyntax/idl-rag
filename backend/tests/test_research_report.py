from __future__ import annotations

from pathlib import Path

from app.services.research_report_service import ResearchReportService


def test_formal_report_is_deterministic_and_review_first(tmp_path: Path) -> None:
    service = ResearchReportService()
    manifest = {
        "run_token": "run-one",
        "experiment": {"id": 1, "name": "MNDWI formal", "execution_mode": "formal", "parameters": {"threshold": 0.0}},
        "formula_spec": {"id": 2, "version": 1, "status": "frozen", "operation": "normalized_difference_threshold"},
        "data_snapshot": {"id": 3, "snapshot_hash": "snapshot-hash", "asset_ids": [4]},
        "project_protocol": {"revision_id": 5, "sha256": "protocol-hash", "research_question": "如何提取季节性水体？"},
        "validation": {"status": "completed", "metrics": {"overall_accuracy": 0.9}},
    }
    outputs = [
        {"kind": "classification_preview", "file_name": "classification_preview.png", "sha256": "a" * 64},
        {"kind": "run_manifest", "file_name": "run_manifest.json", "sha256": "b" * 64, "uri": "research://runs/run-one/run_manifest.json"},
    ]
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = service.build(output_dir=first_dir, run_token="run-one", run_manifest=manifest, outputs=outputs)
    second_manifest = {**manifest, "run_token": "run-two"}
    second = service.build(output_dir=second_dir, run_token="run-two", run_manifest=second_manifest, outputs=outputs)

    assert first["sha256"] == second["sha256"]
    report = (first_dir / "research_report.md").read_text(encoding="utf-8")
    assert "如何提取季节性水体？" in report
    assert "not an automatic scientific conclusion" in report
    assert "superiority" in report
    assert "research://runs/" not in report
    assert "Required researcher review" in report
