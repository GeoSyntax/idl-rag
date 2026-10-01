"""Download a curated, auditable subset of open-access remote-sensing papers.

The existing OpenAlex corpus is intentionally metadata-first.  This script
promotes only records that have an OpenAlex OA flag, a PDF location and an
allow-listed publisher/repository host.  Every downloaded file gets a SHA-256
record and an explicit ``license_status`` so a local research installation can
use the file without silently claiming redistribution rights.

Example::

    python backend/scripts/collect_open_access_papers.py \
      --manifest data/sources/collected/openalex_remote_sensing_manifest.jsonl \
      --output-dir data/sources/open_access_papers --max-files 30
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx


ALLOWLISTED_HOSTS = (
    "arxiv.org",
    "acp.copernicus.org",
    "amt.copernicus.org",
    "essd.copernicus.org",
    "hess.copernicus.org",
    "mdpi.com",
    "hindawi.com",
    "plos.org",
    "frontiersin.org",
    "usgs.gov",
    "nasa.gov",
    "ametsoc.org",
    "int-res.com",
    "biomedcentral.com",
    "springer.com",
)

RELEVANCE_TERMS = {
    "ndvi": 7,
    "evi": 5,
    "ndwi": 5,
    "mndwi": 5,
    "ndbi": 5,
    "land surface temperature": 8,
    "lst": 4,
    "atmospheric correction": 8,
    "reflectance": 5,
    "landsat": 8,
    "sentinel": 8,
    "modis": 7,
    "hyperspectral": 6,
    "classification": 5,
    "change detection": 6,
    "sar": 6,
    "cloud": 4,
    "brdf": 8,
    "radiative": 6,
    "surface energy": 4,
    "google earth engine": 7,
    "validation": 4,
}


def _slug(text: str, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9一-鿿._-]+", "_", text).strip("._-")
    value = re.sub(r"_+", "_", value)
    return (value[:110] or fallback).strip("._-")


def _host_allowed(url: str) -> bool:
    host = urlparse(url).netloc.lower().split(":", 1)[0]
    return any(host == item or host.endswith("." + item) for item in ALLOWLISTED_HOSTS)


def _score(record: dict[str, object]) -> int:
    text = " ".join(
        str(record.get(key) or "")
        for key in ("title", "abstract", "concepts", "keywords")
    ).lower()
    return sum(weight for term, weight in RELEVANCE_TERMS.items() if term in text)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_records(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def collect(
    records: list[dict[str, object]],
    output_dir: Path,
    max_files: int,
    max_mb: int,
    pause: float,
) -> list[dict[str, object]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "open_access_papers_manifest.jsonl"
    existing: dict[str, dict[str, object]] = {}
    if manifest_path.exists():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                existing[str(item.get("doi") or item.get("source_url"))] = item

    candidates = [
        record
        for record in records
        if record.get("is_open_access")
        and record.get("oa_pdf_url")
        and _host_allowed(str(record["oa_pdf_url"]))
    ]
    candidates.sort(key=_score, reverse=True)
    selected: list[dict[str, object]] = []
    seen_doi: set[str] = set()
    for record in candidates:
        doi = str(record.get("doi") or "")
        if doi and doi in seen_doi:
            continue
        seen_doi.add(doi)
        selected.append(record)
        if len(selected) >= max_files:
            break

    client = httpx.Client(
        timeout=httpx.Timeout(90.0, connect=30.0),
        follow_redirects=True,
        headers={"User-Agent": "IDL-RAG research corpus collector/1.0"},
    )
    new_items: list[dict[str, object]] = []
    for index, record in enumerate(selected, start=1):
        source_url = str(record["oa_pdf_url"])
        key = str(record.get("doi") or source_url)
        if key in existing:
            new_items.append(existing[key])
            continue
        file_name = f"paper_{index:03d}_{_slug(str(record.get('title') or ''), f'paper_{index:03d}')}.pdf"
        path = output_dir / file_name
        try:
            with client.stream("GET", source_url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                with path.open("wb") as handle:
                    total = 0
                    for block in response.iter_bytes(1024 * 1024):
                        total += len(block)
                        if total > max_mb * 1024 * 1024:
                            raise ValueError(f"response exceeded {max_mb} MiB")
                        handle.write(block)
            if path.read_bytes()[:4] != b"%PDF":
                raise ValueError(f"download is not a PDF (content-type={content_type})")
            item = {
                "file_name": file_name,
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
                "title": record.get("title"),
                "doi": record.get("doi"),
                "openalex_id": record.get("openalex_id"),
                "source_url": source_url,
                "publisher": record.get("source_name"),
                "year": record.get("year"),
                "relevance_score": _score(record),
                "collected_at": datetime.now(UTC).isoformat(),
                "license_status": "openalex_oa_flag__redistribution_terms_must_be_verified",
            }
            new_items.append(item)
            existing[key] = item
            print(json.dumps({"downloaded": file_name, "bytes": item["bytes"]}, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001 - continue collecting other valid records
            path.unlink(missing_ok=True)
            print(json.dumps({"skipped": source_url, "error": str(exc)}, ensure_ascii=False))
        if pause:
            time.sleep(pause)
    client.close()

    # Keep one deterministic JSONL record per DOI/source URL.
    unique: dict[str, dict[str, object]] = {}
    for item in new_items:
        unique[str(item.get("doi") or item.get("source_url"))] = item
    manifest_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in unique.values()),
        encoding="utf-8",
    )
    return list(unique.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=30)
    parser.add_argument("--max-mb", type=int, default=50)
    parser.add_argument("--pause", type=float, default=0.3)
    args = parser.parse_args()
    items = collect(_load_records(args.manifest), args.output_dir, args.max_files, args.max_mb, args.pause)
    print(json.dumps({"records": len(items), "manifest": (args.output_dir / 'open_access_papers_manifest.jsonl').as_posix()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

