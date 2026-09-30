from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from app.services.research_reproducibility_service import ResearchReproducibilityService


def _write_raster(path: Path, values: np.ndarray) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(110, 30, 0.1, 0.1),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(values.astype(np.float32), 1)


def _manifest(*, formula_id: int = 1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment": {"id": 10, "execution_mode": "formal", "parameters": {"threshold": 0.0}},
        "formula_spec": {"id": formula_id, "version": 1, "status": "frozen", "operation": "safe_band_math_threshold"},
        "data_snapshot": {"id": 20, "snapshot_hash": "snapshot-hash", "asset_ids": [30]},
        "runner": {"type": "python", "python_version": "3.12", "numpy_version": "2"},
    }


def _compare(
    target_dir: Path,
    reference_dir: Path,
    *,
    target_manifest: dict[str, object] | None = None,
    absolute_tolerance: float = 1e-6,
) -> dict[str, object]:
    descriptor = {"kind": "feature_raster", "file_name": "feature.tif"}
    return ResearchReproducibilityService().compare(
        target_run_id=2,
        reference_run_id=1,
        target_manifest=target_manifest or _manifest(),
        reference_manifest=_manifest(),
        target_outputs=[descriptor],
        reference_outputs=[descriptor],
        target_dir=target_dir,
        reference_dir=reference_dir,
        absolute_tolerance=absolute_tolerance,
        relative_tolerance=0.0,
    )


def test_reproducibility_matches_exact_and_tolerance_within_rasters(tmp_path: Path) -> None:
    reference_dir = tmp_path / "reference"
    target_dir = tmp_path / "target"
    reference_dir.mkdir()
    target_dir.mkdir()
    base = np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32)
    _write_raster(reference_dir / "feature.tif", base)
    _write_raster(target_dir / "feature.tif", base + np.array([[0.0, 0.0005], [0.0, 0.0]], dtype=np.float32))

    result = _compare(target_dir, reference_dir, absolute_tolerance=0.001)
    assert result["matched"] is True
    assert result["status"] == "matched"
    assert result["compared_output_count"] == 1
    assert result["raster_comparisons"][0]["exceed_tolerance_pixel_count"] == 0


def test_reproducibility_rejects_exceeding_difference_and_context_mismatch(tmp_path: Path) -> None:
    reference_dir = tmp_path / "reference"
    target_dir = tmp_path / "target"
    reference_dir.mkdir()
    target_dir.mkdir()
    _write_raster(reference_dir / "feature.tif", np.zeros((2, 2), dtype=np.float32))
    _write_raster(target_dir / "feature.tif", np.full((2, 2), 0.1, dtype=np.float32))

    difference = _compare(target_dir, reference_dir, absolute_tolerance=0.001)
    assert difference["matched"] is False
    assert difference["status"] == "failed"
    assert any("超过容差" in issue for issue in difference["issues"])

    mismatch = _compare(target_dir, reference_dir, target_manifest=_manifest(formula_id=999))
    assert mismatch["matched"] is False
    assert any("冻结上下文不一致" in issue for issue in mismatch["issues"])
