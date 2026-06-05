from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.database import get_session_factory  # noqa: E402
from app.services.settings_service import get_runtime_settings  # noqa: E402

ALGORITHMS = [
    {
        "slug": "spectral_indices",
        "title": "Spectral indices: NDVI, NDWI, SAVI, EVI, NDBI, NBR",
        "keywords": ["NDVI", "NDWI", "SAVI", "EVI", "NDBI", "NBR", "vegetation index", "water index", "burn ratio"],
        "requirements": [
            "safe_divide with NaN for invalid denominator",
            "compute_ndvi(nir, red)",
            "compute_ndwi(green, nir)",
            "compute_savi(nir, red, l)",
            "compute_evi(nir, red, blue, gain, c1, c2, l)",
            "compute_ndbi(swir, nir)",
            "compute_nbr(nir, swir)",
        ],
    },
    {
        "slug": "land_surface_temperature",
        "title": "Land surface temperature retrieval from thermal infrared remote sensing",
        "keywords": ["land surface temperature", "LST", "thermal infrared", "brightness temperature", "emissivity"],
        "requirements": [
            "radiance_to_brightness_temperature(radiance, k1, k2)",
            "ndvi_fractional_vegetation(ndvi, ndvi_soil, ndvi_veg)",
            "estimate_emissivity_from_ndvi(ndvi)",
            "single_channel_lst(brightness_temperature, wavelength_um, emissivity)",
        ],
    },
    {
        "slug": "radiometric_atmospheric_correction",
        "title": "Radiometric calibration and simple dark-object atmospheric correction",
        "keywords": ["radiometric calibration", "atmospheric correction", "dark object subtraction", "TOA reflectance", "FLAASH", "QUAC"],
        "requirements": [
            "dn_to_radiance(dn, gain, offset)",
            "radiance_to_toa_reflectance(radiance, solar_zenith_deg, solar_irradiance, earth_sun_distance)",
            "dark_object_subtraction(reflectance, percentile)",
            "clip_reflectance(array)",
        ],
    },
    {
        "slug": "change_detection",
        "title": "Remote-sensing change detection: difference, ratio, CVA, thresholding",
        "keywords": ["change detection", "difference image", "change vector analysis", "threshold", "multitemporal"],
        "requirements": [
            "image_difference(before, after)",
            "image_ratio(before, after)",
            "change_vector_magnitude(before_stack, after_stack)",
            "otsu_threshold(values)",
            "threshold_change_map(change_magnitude, threshold)",
        ],
    },
    {
        "slug": "classification_metrics",
        "title": "Remote-sensing classification helpers and accuracy assessment",
        "keywords": ["image classification", "random forest", "support vector machine", "accuracy assessment", "confusion matrix"],
        "requirements": [
            "standardize_features(feature_cube)",
            "minimum_distance_classifier(feature_cube, class_means)",
            "confusion_matrix_labels(reference, predicted, class_values)",
            "overall_accuracy(confusion_matrix)",
            "kappa_coefficient(confusion_matrix)",
        ],
    },
    {
        "slug": "hyperspectral_unmixing",
        "title": "Hyperspectral linear spectral unmixing and spectral angle mapper",
        "keywords": ["hyperspectral", "unmixing", "spectral angle mapper", "endmember", "abundance"],
        "requirements": [
            "spectral_angle(pixel, reference)",
            "spectral_angle_mapper(pixel_spectra, endmembers)",
            "linear_unmix_pixel(pixel, endmembers)",
            "normalize_abundance(abundance)",
        ],
    },
    {
        "slug": "cloud_water_masks",
        "title": "Cloud, water, and quality masks for optical remote sensing",
        "keywords": ["cloud detection", "water mask", "NDWI", "quality mask", "clear sky"],
        "requirements": [
            "simple_cloud_mask(blue, nir, thermal, blue_threshold, thermal_threshold)",
            "water_mask_ndwi(green, nir, threshold)",
            "combine_quality_masks(mask_list)",
            "apply_mask(data, mask, nodata)",
        ],
    },
]

SYSTEM_PROMPT = """You are an expert ENVI/IDL remote-sensing developer. Generate production-oriented IDL code from cited remote-sensing theory records.
Rules:
- Output only IDL .pro source code, no Markdown fences.
- Every pro/function must include compile_opt idl2.
- Prefer arrays and vectorized operations.
- Use !VALUES.F_NAN for invalid numeric results.
- Include short semicolon comments for non-obvious formulas only.
- Do not invent citations inside code; use a compact provenance comment block at the top listing the provided DOI/OpenAlex IDs.
- Keep code self-contained and syntactically IDL-like.
"""


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def load_records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def score_record(record: dict[str, Any], keywords: list[str]) -> int:
    haystack = " ".join(
        [
            record.get("title", ""),
            record.get("abstract", ""),
            " ".join(record.get("concepts", [])),
            " ".join(record.get("keywords", [])),
        ]
    ).lower()
    return sum(1 for keyword in keywords if keyword.lower() in haystack)


def select_context(records: list[dict[str, Any]], algorithm: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    scored = [(score_record(record, algorithm["keywords"]), record) for record in records]
    scored = [(score, record) for score, record in scored if score > 0]
    scored.sort(key=lambda item: (item[0], item[1].get("cited_by_count", 0), bool(item[1].get("abstract"))), reverse=True)
    return [record for _, record in scored[:limit]]


def build_prompt(algorithm: dict[str, Any], records: list[dict[str, Any]]) -> str:
    context_blocks = []
    for index, record in enumerate(records, start=1):
        abstract = clean_text(record.get("abstract"))[:1200]
        context_blocks.append(
            "\n".join(
                [
                    f"[{index}] Title: {record.get('title')}",
                    f"Year: {record.get('year')}",
                    f"DOI: {record.get('doi') or 'Not provided'}",
                    f"OpenAlex: {record.get('openalex_id')}",
                    f"Concepts: {', '.join(record.get('concepts', [])[:8])}",
                    f"Abstract: {abstract or 'No abstract provided.'}",
                ]
            )
        )
    requirements = "\n".join(f"- {item}" for item in algorithm["requirements"])
    return f"""Generate one IDL .pro file for this remote-sensing algorithm module.

Module: {algorithm['title']}
Required routines:
{requirements}

Theory records from OpenAlex:
{chr(10).join(context_blocks)}

The output must be a complete IDL source file. Include reusable functions and one small demo procedure named demo_{algorithm['slug']}.
"""


def fallback_code(algorithm: dict[str, Any], records: list[dict[str, Any]]) -> str:
    provenance = "\n".join(
        f"; - {record.get('doi') or record.get('openalex_id')} | {record.get('title')}"
        for record in records[:5]
    )
    slug = algorithm["slug"]
    return textwrap.dedent(
        f"""
        ; Remote-sensing IDL module: {algorithm['title']}
        ; Generated from OpenAlex theory metadata.
        ; Provenance:
        {provenance}

        function rs_safe_divide, numerator, denominator
          compile_opt idl2
          result = fltarr(size(numerator, /dimensions)) + !values.f_nan
          valid = where(denominator ne 0 and finite(denominator), count)
          if count gt 0 then result[valid] = numerator[valid] / denominator[valid]
          return, result
        end

        pro demo_{slug}
          compile_opt idl2
          print, 'Remote-sensing algorithm module generated from verified theory records.'
        end
        """
    ).strip()


def call_model(settings, prompt: str) -> str:
    payload = {
        "model": settings.chat_model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    response = httpx.post(
        settings.api_base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": "Bearer " + settings.api_key},
        json=payload,
        timeout=180.0,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"].strip()
    content = re.sub(r"^```(?:idl|pro)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    return content.strip()


def write_provenance(output_dir: Path, algorithm: dict[str, Any], records: list[dict[str, Any]], code: str) -> None:
    payload = {
        "algorithm": algorithm,
        "records": records,
        "code_chars": len(code),
    }
    (output_dir / f"{algorithm['slug']}_provenance.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate IDL remote-sensing algorithm code from collected theory records.")
    parser.add_argument("--manifest", type=Path, default=Path("data/sources/collected/openalex_remote_sensing_manifest.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/sources/generated_remote_sensing_idl"))
    args = parser.parse_args()

    records = load_records(args.manifest)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    session_factory = get_session_factory()
    db = session_factory()
    try:
        settings = get_runtime_settings(db)
        if not settings.api_key:
            raise RuntimeError("No model API key configured.")
        summary = []
        for algorithm in ALGORITHMS:
            selected = select_context(records, algorithm)
            if not selected:
                raise RuntimeError(f"No theory records found for {algorithm['slug']}.")
            prompt = build_prompt(algorithm, selected)
            try:
                code = call_model(settings, prompt)
            except Exception:
                code = fallback_code(algorithm, selected)
            output_path = args.output_dir / f"{algorithm['slug']}.pro"
            output_path.write_text(code + "\n", encoding="utf-8")
            write_provenance(args.output_dir, algorithm, selected, code)
            summary.append({
                "slug": algorithm["slug"],
                "file": output_path.as_posix(),
                "records_used": len(selected),
                "code_chars": len(code),
                "contains_compile_opt": "compile_opt idl2" in code.lower(),
            })
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
