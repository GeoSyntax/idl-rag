"""Run the first end-to-end real-data research case.

The case is intentionally small and reproducible: Sentinel-2 L2A and
Sentinel-1 RTC scenes over Poyang Lake are searched through Planetary
Computer, B03/B11 and VV are materialized, VV is converted from linear power
to dB and aligned to the optical grid, an ESA WorldCover 2021 water-class
layer is converted to a binary reference, and formal MNDWI/SAR runs are
executed and compared.

This script is an evidence-producing probe, not a claim that WorldCover is
field truth.  The generated report records that limitation explicitly.

Usage (from the repository root)::

    uv run --project backend python backend/scripts/run_real_research_case.py

The default output directory is ``.tmp-real-poyang-case`` (ignored by Git).
Use ``--keep`` to avoid replacing an existing directory, or ``--base-dir``
to choose another isolated location.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _response(response: Any, label: str) -> dict[str, Any]:
    if not 200 <= response.status_code < 300:
        detail = response.text[:2000]
        raise RuntimeError(f"{label} failed with HTTP {response.status_code}: {detail}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} returned a non-object JSON payload")
    return payload


def _write_geojson(path: Path, bbox: list[float]) -> None:
    west, south, east, north = bbox
    document = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "Poyang Lake real-case ROI"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
                },
            }
        ],
    }
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


def _asset_candidate(search_payload: dict[str, Any], required_keys: set[str]) -> dict[str, Any]:
    candidates = search_payload.get("candidates", [])
    ranked = sorted(candidates, key=lambda item: item.get("cloud_cover") if item.get("cloud_cover") is not None else 999.0)
    for candidate in ranked:
        if required_keys.issubset(set((candidate.get("assets") or {}).keys())):
            return candidate
    raise RuntimeError(f"STAC search returned no candidate containing assets {sorted(required_keys)}")


def _download(client: Any, base: str, project_id: int, headers: dict[str, str], audit_id: int, candidate: dict[str, Any], asset_key: str, name: str, bbox: list[float]) -> dict[str, Any]:
    return _response(
        client.post(
            f"{base}/research/projects/{project_id}/stac-search/download",
            headers=headers,
            json={
                "audit_id": audit_id,
                "candidate": candidate,
                "asset_key": asset_key,
                "name": name,
                "crop_bbox": bbox,
                "target_resolution": 10,
            },
        ),
        f"download {name}",
    )["asset"]


def run_case(base_dir: Path, *, keep: bool = False) -> dict[str, Any]:
    if base_dir.exists() and not keep:
        shutil.rmtree(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    os.environ["IDLRAG_BASE_DIR"] = str(base_dir)
    os.environ["IDLRAG_IMPORT_ROOTS"] = str(base_dir)
    os.environ.setdefault("IDLRAG_AUTH_SECRET", "local-real-case-secret-change-me")

    # Import after environment setup so all application paths use this case.
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.services.research_asset_storage import ResearchAssetStorage

    bbox = [115.95, 28.95, 116.05, 29.05]
    base = "/api"
    username = f"realcase_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    roi_path = base_dir / "poyang_roi.geojson"
    _write_geojson(roi_path, bbox)

    with TestClient(create_app()) as client:
        auth = _response(
            client.post("/api/auth/register", json={"username": username, "password": "realcase-secret123"}),
            "register researcher",
        )
        headers = {"Authorization": f"Bearer {auth['access_token']}"}

        project = _response(
            client.post(
                f"{base}/research/projects",
                headers=headers,
                json={
                    "name": "鄱阳湖 2024-10 光学-SAR 真实案例",
                    "description": "真实 Planetary Computer Sentinel-2/Sentinel-1 数据链路与 WorldCover 代理参考对照；不是现场真值研究。",
                    "entry_mode": "template",
                    "protocol": {
                        "research_question": "在鄱阳湖指定秋季小区域内，Sentinel-2 MNDWI 与 Sentinel-1 VV 阈值基线相对 ESA WorldCover 水体类代理参考的表现有何差异？",
                        "hypothesis": "光学 MNDWI 与 SAR VV 阈值会在开放水体边界和湿地场景产生不同的误差结构；任何差异都需要在同期独立样本上复核。",
                    },
                },
            ),
            "create project",
        )
        project_id = project["id"]

        with roi_path.open("rb") as roi_file:
            roi_asset = _response(
                client.post(
                    f"{base}/research/projects/{project_id}/data-assets/upload",
                    headers=headers,
                    files={"file": (roi_path.name, roi_file, "application/geo+json")},
                    data={"asset_kind": "roi", "name": "鄱阳湖真实案例 ROI"},
                ),
                "upload ROI",
            )

        s2_search = _response(
            client.post(
                f"{base}/research/projects/{project_id}/stac-search",
                headers=headers,
                json={
                    "provider": "planetary_computer",
                    "collections": ["sentinel-2-l2a"],
                    "bbox": bbox,
                    "datetime_start": "2024-10-01",
                    "datetime_end": "2024-10-31",
                    "cloud_cover_max": 20,
                    "limit": 20,
                },
            ),
            "search Sentinel-2",
        )
        s2_candidate = _asset_candidate(s2_search, {"B03", "B11"})
        b03_asset = _download(client, base, project_id, headers, s2_search["audit_id"], s2_candidate, "B03", "Sentinel-2 B03 green", bbox)
        b11_asset = _download(client, base, project_id, headers, s2_search["audit_id"], s2_candidate, "B11", "Sentinel-2 B11 SWIR1", bbox)

        worldcover_search = _response(
            client.post(
                f"{base}/research/projects/{project_id}/stac-search",
                headers=headers,
                json={
                    "provider": "planetary_computer",
                    "collections": ["esa-worldcover"],
                    "bbox": bbox,
                    "limit": 5,
                },
            ),
            "search ESA WorldCover",
        )
        worldcover_candidate = _asset_candidate(worldcover_search, {"map"})
        worldcover_asset = _download(client, base, project_id, headers, worldcover_search["audit_id"], worldcover_candidate, "map", "ESA WorldCover 2021 map", bbox)

        s1_search = _response(
            client.post(
                f"{base}/research/projects/{project_id}/stac-search",
                headers=headers,
                json={
                    "provider": "planetary_computer",
                    "collections": ["sentinel-1-rtc"],
                    "bbox": bbox,
                    "datetime_start": "2024-10-01",
                    "datetime_end": "2024-10-31",
                    "limit": 10,
                },
            ),
            "search Sentinel-1 RTC",
        )
        s1_candidate = _asset_candidate(s1_search, {"vv"})
        vv_asset = _download(client, base, project_id, headers, s1_search["audit_id"], s1_candidate, "vv", "Sentinel-1 VV backscatter", bbox)

        stack = _response(
            client.post(
                f"{base}/research/projects/{project_id}/data-assets/stack",
                headers=headers,
                json={
                    "name": "Sentinel-2 B03/B11 10m aligned stack",
                    "asset_ids": [b03_asset["id"], b11_asset["id"]],
                    "reference_asset_id": b03_asset["id"],
                    "band_names": ["green_b03", "swir1_b11"],
                    "resampling": "bilinear",
                },
            ),
            "align B03/B11 stack",
        )

        # Reproject the SAR source onto the optical analysis grid.  This is
        # required both for the reference-raster validation contract and for
        # any later optical/SAR fusion; the raw RTC COG has a wider native
        # footprint even though it covers the same ROI.
        import numpy as np
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.warp import reproject

        storage = ResearchAssetStorage()
        stack_path = storage.resolve_asset_uri(stack["source_uri"])
        vv_path = storage.resolve_asset_uri(vv_asset["source_uri"])
        vv_aligned_path = base_dir / "sentinel1_vv_db_aligned_to_optical_grid.tif"
        with rasterio.open(stack_path) as target, rasterio.open(vv_path) as source:
            vv_aligned = np.full((target.height, target.width), np.nan, dtype=np.float32)
            reproject(
                source=rasterio.band(source, 1),
                destination=vv_aligned,
                src_transform=source.transform,
                src_crs=source.crs,
                src_nodata=source.nodata,
                dst_transform=target.transform,
                dst_crs=target.crs,
                dst_width=target.width,
                dst_height=target.height,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )
            profile = target.profile.copy()
            profile.update(count=1, dtype="float32", nodata=-9999.0, compress="lzw")
            # Sentinel-1 RTC stores backscatter as linear power.  Convert to
            # decibels before applying the documented -17 dB threshold.
            valid_vv = np.isfinite(vv_aligned) & (vv_aligned > 0)
            vv_aligned[valid_vv] = 10.0 * np.log10(vv_aligned[valid_vv])
            vv_aligned[~valid_vv] = -9999.0
            with rasterio.open(vv_aligned_path, "w", **profile) as destination:
                destination.write(vv_aligned, 1)

        with vv_aligned_path.open("rb") as vv_aligned_file:
            vv_aligned_asset = _response(
                client.post(
                    f"{base}/research/projects/{project_id}/data-assets/upload",
                    headers=headers,
                    files={"file": (vv_aligned_path.name, vv_aligned_file, "image/tiff")},
                    data={
                        "asset_kind": "raster",
                        "name": "Sentinel-1 VV dB aligned to Sentinel-2 grid",
                        "metadata_json": json.dumps(
                            {
                                "asset_role": "derived_analysis_input",
                                "derived_operation": "reproject_and_linear_power_to_db",
                                "source_asset_id": vv_asset["id"],
                                "source_units": "linear_power",
                                "output_units": "dB",
                                "conversion_formula": "10*log10(VV_linear)",
                                "resampling": "bilinear",
                                "target_grid_asset_id": stack["id"],
                                "threshold_units": "dB",
                            },
                            ensure_ascii=False,
                        ),
                    },
                ),
                "upload aligned Sentinel-1 VV",
            )

        # Convert WorldCover class 80 (permanent water) to a binary reference
        # on the stack grid. This is deliberately explicit and recorded as a
        # derived reference asset rather than silently treating all classes as water.
        worldcover_path = storage.resolve_asset_uri(worldcover_asset["source_uri"])
        reference_path = base_dir / "worldcover_water_reference.tif"
        with rasterio.open(stack_path) as target, rasterio.open(worldcover_path) as source:
            target_grid = np.zeros((target.height, target.width), dtype=np.uint8)
            worldcover_values = np.zeros((target.height, target.width), dtype=np.uint8)
            reproject(
                source=rasterio.band(source, 1),
                destination=worldcover_values,
                src_transform=source.transform,
                src_crs=source.crs,
                dst_transform=target.transform,
                dst_crs=target.crs,
                dst_width=target.width,
                dst_height=target.height,
                resampling=Resampling.nearest,
            )
            target_grid[:] = (worldcover_values == 80).astype(np.uint8)
            profile = target.profile.copy()
            profile.update(count=1, dtype="uint8", nodata=255, compress="lzw")
            with rasterio.open(reference_path, "w", **profile) as destination:
                destination.write(target_grid, 1)

        with reference_path.open("rb") as reference_file:
            reference_asset = _response(
                client.post(
                    f"{base}/research/projects/{project_id}/data-assets/upload",
                    headers=headers,
                    files={"file": (reference_path.name, reference_file, "image/tiff")},
                    data={
                        "asset_kind": "reference",
                        "name": "WorldCover 2021 class-80 water proxy",
                        "metadata_json": json.dumps(
                            {
                                "asset_role": "binary_validation_reference",
                                "source_asset_id": worldcover_asset["id"],
                                "source_class": 80,
                                "source_product": "ESA WorldCover 2021",
                                "reference_is_field_truth": False,
                                "reference_temporal_relation": "noncontemporaneous_proxy",
                                "target_grid_asset_id": stack["id"],
                                "resampling": "nearest",
                            },
                            ensure_ascii=False,
                        ),
                    },
                ),
                "upload binary WorldCover reference",
            )

        snapshot = _response(
            client.post(
                f"{base}/research/projects/{project_id}/data-snapshots",
                headers=headers,
                json={
                    "name": "2024-10 Sentinel-2 + Sentinel-1 + WorldCover reference frozen snapshot",
                    "description": "B03/B11 and Sentinel-1 VV-dB aligned inputs plus explicitly derived WorldCover class 80 binary reference.",
                    "asset_ids": [stack["id"], vv_asset["id"], vv_aligned_asset["id"], worldcover_asset["id"], reference_asset["id"]],
                },
            ),
            "freeze data snapshot",
        )

        evidence_mndwi = _response(
            client.post(
                f"{base}/research/projects/{project_id}/evidence-cards",
                headers=headers,
                json={
                    "title": "MNDWI open-water index method",
                    "status": "verified",
                    "source_type": "paper",
                    "doi": "10.1016/j.rse.2006.09.012",
                    "source_url": "https://doi.org/10.1016/j.rse.2006.09.012",
                    "applicability": "MNDWI green/SWIR water-index baseline; threshold remains scene-dependent.",
                    "limitations": "This case uses an ESA WorldCover proxy reference, not contemporaneous field labels.",
                },
            ),
            "create MNDWI evidence card",
        )
        evidence_worldcover = _response(
            client.post(
                f"{base}/research/projects/{project_id}/evidence-cards",
                headers=headers,
                json={
                    "title": "ESA WorldCover 2021 dataset reference",
                    "status": "verified",
                    "source_type": "dataset",
                    "source_url": "https://esa-worldcover.org/en/data-access",
                    "license_note": "Check ESA WorldCover terms before redistribution.",
                    "applicability": "Class 80 is used only as a binary water proxy for this engineering/scientific case.",
                    "limitations": "The product is not treated as field truth and is not contemporaneous with the 2024 scene.",
                },
            ),
            "create WorldCover evidence card",
        )
        evidence_s1 = _response(
            client.post(
                f"{base}/research/projects/{project_id}/evidence-cards",
                headers=headers,
                json={
                    "title": "Sentinel-1 RTC VV dataset reference",
                    "status": "verified",
                    "source_type": "dataset",
                    "source_url": "https://planetarycomputer.microsoft.com/dataset/sentinel-1-rtc",
                    "applicability": "VV backscatter threshold baseline for the same ROI and month.",
                    "limitations": "Threshold is scene-dependent; this case does not claim SAR superiority or calibrated field accuracy.",
                },
            ),
            "create Sentinel-1 evidence card",
        )

        protocol = {
            "research_question": project["protocol"]["research_question"],
            "hypothesis": project["protocol"]["hypothesis"],
            "study_area": {"roi_asset_id": roi_asset["id"], "bbox_wgs84": bbox, "name": "Poyang Lake subset"},
            "temporal_scope": {"start": "2024-10-01", "end": "2024-10-31", "scene_datetime": s2_candidate.get("datetime")},
            "data_plan": {"snapshot_id": snapshot["id"], "sentinel_scene": s2_candidate["external_id"], "sar_scene": s1_candidate["external_id"], "worldcover_item": worldcover_candidate["external_id"]},
            "method_plan": {"evidence_card_ids": [evidence_mndwi["id"], evidence_s1["id"], evidence_worldcover["id"]], "baselines": ["MNDWI=(B03-B11)/(B03+B11), threshold=0.0", "Sentinel-1 VV <= -17 dB"]},
            "validation_plan": {
                "split": "spatiotemporal-holdout",
                "independent_test_period": "2021 WorldCover reference layer (proxy, not field truth)",
                "reference_source": "ESA WorldCover 2021 class 80 raster reprojected to the 2024 Sentinel-2 grid",
                "spatial_blocks": ["roi-single-block; exploratory only", "formal case is not representative of all Poyang Lake"],
            },
            "visualization_contract": ["input", "preprocessing", "feature", "classification", "validation_error"],
            "conclusion_boundary": "Only report agreement with this small ROI and WorldCover class-80 proxy; do not generalize to all seasons, the whole lake, field accuracy, or superiority over SAR/fusion.",
        }
        project = _response(client.patch(f"{base}/research/projects/{project_id}", headers=headers, json={"protocol": protocol}), "save complete protocol")
        readiness = _response(client.get(f"{base}/research/projects/{project_id}/protocol-readiness", headers=headers), "protocol readiness")
        if not readiness.get("ready"):
            raise RuntimeError(f"formal protocol is not ready: {json.dumps(readiness, ensure_ascii=False)}")

        formula = _response(
            client.post(
                f"{base}/research/projects/{project_id}/formula-specs",
                headers=headers,
                json={
                    "name": "MNDWI fixed threshold 0.0 baseline",
                    "version": 1,
                    "status": "frozen",
                    "spec": {
                        "operation": "normalized_difference_threshold",
                        "input_asset_id": stack["id"],
                        "inputs": {"green": {"band": 1}, "swir1": {"band": 2}},
                        "parameters": {"threshold": 0.0},
                    },
                    "evidence_card_ids": [evidence_mndwi["id"]],
                },
            ),
            "freeze MNDWI formula",
        )
        experiment = _response(
            client.post(
                f"{base}/research/projects/{project_id}/experiments",
                headers=headers,
                json={
                    "name": "MNDWI 2024-10 formal proxy validation",
                    "formula_spec_id": formula["id"],
                    "data_snapshot_id": snapshot["id"],
                    "runner_type": "python",
                    "execution_mode": "formal",
                    "parameters": {"threshold": 0.0},
                    "validation_plan": {
                        "split": "spatiotemporal-holdout",
                        "metrics": ["overall_accuracy", "precision", "recall", "f1", "iou", "area_difference"],
                        "reference_asset_id": reference_asset["id"],
                        "reference_temporal_relation": "noncontemporaneous_proxy",
                        "reference_is_field_truth": False,
                        "reference_limitations": "ESA WorldCover 2021 class 80 is a proxy layer for the 2024-10-05 scene, not同期现场标签。",
                    },
                    "visualization_contract": ["input", "preprocessing", "feature", "classification", "validation_error"],
                },
            ),
            "create formal experiment",
        )
        run = _response(
            client.post(f"{base}/research/projects/{project_id}/experiments/{experiment['id']}/runs?mode=sync", headers=headers),
            "run formal Python experiment",
        )
        verification = _response(
            client.get(f"{base}/research/projects/{project_id}/experiments/{experiment['id']}/runs/{run['id']}/verification", headers=headers),
            "verify evidence package",
        )
        experiment_list_response = client.get(f"{base}/research/projects/{project_id}/experiments", headers=headers)
        if not 200 <= experiment_list_response.status_code < 300:
            raise RuntimeError(f"refresh experiment status failed with HTTP {experiment_list_response.status_code}: {experiment_list_response.text[:2000]}")
        experiment = next(item for item in experiment_list_response.json() if item["id"] == experiment["id"])

        sar_formula = _response(
            client.post(
                f"{base}/research/projects/{project_id}/formula-specs",
                headers=headers,
                json={
                    "name": "Sentinel-1 VV threshold -17 dB baseline",
                    "version": 1,
                    "status": "frozen",
                    "spec": {
                        "operation": "sar_backscatter_threshold",
                        "input_asset_id": vv_aligned_asset["id"],
                        "inputs": {"vv": {"band": 1}},
                        "parameters": {"threshold": -17.0},
                    },
                    "evidence_card_ids": [evidence_s1["id"]],
                },
            ),
            "freeze SAR formula",
        )
        sar_experiment = _response(
            client.post(
                f"{base}/research/projects/{project_id}/experiments",
                headers=headers,
                json={
                    "name": "Sentinel-1 VV 2024-10 formal proxy validation",
                    "formula_spec_id": sar_formula["id"],
                    "data_snapshot_id": snapshot["id"],
                    "runner_type": "python",
                    "execution_mode": "formal",
                    "parameters": {"threshold": -17.0},
                    "validation_plan": {
                        "split": "spatiotemporal-holdout",
                        "metrics": ["overall_accuracy", "precision", "recall", "f1", "iou", "area_difference"],
                        "reference_asset_id": reference_asset["id"],
                        "reference_temporal_relation": "noncontemporaneous_proxy",
                        "reference_is_field_truth": False,
                        "reference_limitations": "ESA WorldCover 2021 class 80 is a proxy layer for the 2024-10-05 scene, not同期现场标签。",
                    },
                    "visualization_contract": ["input", "preprocessing", "feature", "classification", "validation_error"],
                },
            ),
            "create SAR formal experiment",
        )
        sar_run = _response(
            client.post(f"{base}/research/projects/{project_id}/experiments/{sar_experiment['id']}/runs?mode=sync", headers=headers),
            "run formal Sentinel-1 experiment",
        )
        sar_verification = _response(
            client.get(f"{base}/research/projects/{project_id}/experiments/{sar_experiment['id']}/runs/{sar_run['id']}/verification", headers=headers),
            "verify SAR evidence package",
        )
        comparison = _response(
            client.post(
                f"{base}/research/projects/{project_id}/experiments/{sar_experiment['id']}/runs/{sar_run['id']}/comparison",
                headers=headers,
                json={"reference_run_id": run["id"]},
            ),
            "compare SAR against MNDWI",
        )

    output_dir = base_dir / "data" / "research" / "runs" / f"project-{project_id}" / run["run_token"]
    summary = {
        "case": "poyang_lake_sentinel2_sentinel1_water_proxy_comparison",
        "generated_at": datetime.now(UTC).isoformat(),
        "base_dir": str(base_dir),
        "project_id": project_id,
        "experiment_id": experiment["id"],
        "run_id": run["id"],
        "run_token": run["run_token"],
        "run_status": run["status"],
        "verification": verification,
        "scene": s2_candidate,
        "worldcover_item": worldcover_candidate,
        "bbox_wgs84": bbox,
        "assets": {"b03": b03_asset, "b11": b11_asset, "stack": stack, "vv": vv_asset, "vv_aligned": vv_aligned_asset, "worldcover": worldcover_asset, "reference": reference_asset},
        "snapshot": snapshot,
        "formula": formula,
        "experiment": experiment,
        "sar": {"scene": s1_candidate, "asset": vv_asset, "formula": sar_formula, "experiment": sar_experiment, "run": sar_run, "verification": sar_verification},
        "comparison": comparison,
        "readiness": readiness,
        "output_dir": str(output_dir),
        "output_files": sorted(path.name for path in output_dir.iterdir()) if output_dir.is_dir() else [],
        "scientific_boundary": "WorldCover class 80 is a proxy reference, not contemporaneous field truth; this small ROI does not support lake-wide, seasonal, or MNDWI-versus-SAR superiority claims.",
    }
    summary_path = base_dir / "real_case_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=REPO_ROOT / ".tmp-real-poyang-case")
    parser.add_argument("--keep", action="store_true", help="do not remove an existing base directory")
    args = parser.parse_args()
    summary = run_case(args.base_dir.resolve(), keep=args.keep)
    print(json.dumps({
        "case": summary["case"],
        "project_id": summary["project_id"],
        "optical_run_id": summary["run_id"],
        "optical_status": summary["run_status"],
        "sar_run_id": summary["sar"]["run"]["id"],
        "sar_status": summary["sar"]["run"]["status"],
        "comparison": summary["comparison"],
        "base_dir": summary["base_dir"],
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
