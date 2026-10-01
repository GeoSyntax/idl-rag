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
from pypdf import PdfReader


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


def _validate_pdf(path: Path) -> None:
    """Reject HTML/partial downloads before they enter the corpus manifest."""
    if path.read_bytes()[:4] != b"%PDF":
        raise ValueError("download is not a PDF")
    try:
        if len(PdfReader(path, strict=False).pages) < 1:
            raise ValueError("PDF contains no pages")
    except Exception as exc:  # noqa: BLE001 - expose a stable collector error
        raise ValueError(f"PDF parse failed: {exc}") from exc


def _load_records(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _manifest_key(item: dict[str, object]) -> str:
    return str(item.get("doi") or item.get("source_url") or item.get("file_name") or "")


def _write_manifest(path: Path, items: list[dict[str, object]] | dict[str, dict[str, object]]) -> list[dict[str, object]]:
    """Atomically persist the manifest so Ctrl-C cannot truncate the source of truth."""
    values = items.values() if isinstance(items, dict) else items
    unique: dict[str, dict[str, object]] = {}
    for item in values:
        key = _manifest_key(item)
        if key:
            unique[key] = item
    ordered = list(unique.values())
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in ordered),
        encoding="utf-8",
    )
    temporary.replace(path)
    return ordered


def _paper_item(record: dict[str, object], path: Path, *, collected_at: str | None = None) -> dict[str, object]:
    return {
        "file_name": path.name,
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "title": record.get("title"),
        "doi": record.get("doi"),
        "openalex_id": record.get("openalex_id"),
        "source_url": str(record["oa_pdf_url"]),
        "publisher": record.get("source_name"),
        "year": record.get("year"),
        "relevance_score": _score(record),
        "collected_at": collected_at or datetime.now(UTC).isoformat(),
        "license_status": "openalex_oa_flag__redistribution_terms_must_be_verified",
    }


def _selected_candidates(records: list[dict[str, object]], max_files: int) -> list[dict[str, object]]:
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
    return selected


def _reconcile_orphan_pdfs(
    selected: list[dict[str, object]],
    output_dir: Path,
    existing: dict[str, dict[str, object]],
) -> int:
    """Recover PDFs downloaded before a process interruption.

    Filenames are deterministic (`paper_<rank>_...pdf`), so a valid orphan can
    be joined back to the same ranked OpenAlex candidate without guessing its
    provenance. Files with an unknown rank or invalid PDF header remain
    untouched and are reported by the source audit instead of being silently
    imported.
    """
    existing_files = {str(item.get("file_name") or "") for item in existing.values()}
    recovered = 0
    for index, record in enumerate(selected, start=1):
        file_name = f"paper_{index:03d}_{_slug(str(record.get('title') or ''), f'paper_{index:03d}')}.pdf"
        path = output_dir / file_name
        key = str(record.get("doi") or record.get("oa_pdf_url") or "")
        if not path.exists() or key in existing or file_name in existing_files:
            continue
        try:
            _validate_pdf(path)
        except ValueError:
            continue
        existing[key] = _paper_item(
            record,
            path,
            collected_at=datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
        )
        existing_files.add(file_name)
        recovered += 1
    return recovered


def collect(
    records: list[dict[str, object]],
    output_dir: Path,
    max_files: int,
    max_mb: int,
    pause: float,
    timeout_seconds: float = 90.0,
    connect_timeout_seconds: float = 30.0,
    retries: int = 0,
) -> list[dict[str, object]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "open_access_papers_manifest.jsonl"
    existing: dict[str, dict[str, object]] = {}
    if manifest_path.exists():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                existing[_manifest_key(item)] = item

    selected = _selected_candidates(records, max_files)
    recovered = _reconcile_orphan_pdfs(selected, output_dir, existing)
    if recovered:
        _write_manifest(manifest_path, existing)
        print(json.dumps({"reconciled": recovered}, ensure_ascii=False))

    client = httpx.Client(
        timeout=httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds),
        follow_redirects=True,
        headers={"User-Agent": "IDL-RAG research corpus collector/1.0"},
    )
    for index, record in enumerate(selected, start=1):
        source_url = str(record["oa_pdf_url"])
        key = str(record.get("doi") or source_url)
        if key in existing:
            continue
        file_name = f"paper_{index:03d}_{_slug(str(record.get('title') or ''), f'paper_{index:03d}')}.pdf"
        path = output_dir / file_name
        try:
            last_error: Exception | None = None
            for attempt in range(retries + 1):
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
                    try:
                        _validate_pdf(path)
                    except ValueError as exc:
                        raise ValueError(f"{exc} (content-type={content_type})") from exc
                    last_error = None
                    break
                except Exception as exc:  # noqa: BLE001 - retry then report the source
                    last_error = exc
                    path.unlink(missing_ok=True)
                    if attempt < retries:
                        time.sleep(min(2 ** attempt, 8))
            if last_error is not None:
                raise last_error
            item = _paper_item(record, path)
            existing[key] = item
            _write_manifest(manifest_path, existing)
            print(json.dumps({"downloaded": file_name, "bytes": item["bytes"]}, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001 - continue collecting other valid records
            path.unlink(missing_ok=True)
            print(json.dumps({"skipped": source_url, "error": str(exc)}, ensure_ascii=False))
        if pause:
            time.sleep(pause)
    client.close()

    # Final atomic write also normalizes a manifest produced by older versions.
    return _write_manifest(manifest_path, existing)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=30)
    parser.add_argument("--max-mb", type=int, default=50)
    parser.add_argument("--pause", type=float, default=0.3)
    parser.add_argument("--timeout", type=float, default=90.0, help="per-request read timeout in seconds")
    parser.add_argument("--connect-timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=0, help="additional attempts for transient download errors")
    args = parser.parse_args()
    items = collect(
        _load_records(args.manifest),
        args.output_dir,
        args.max_files,
        args.max_mb,
        args.pause,
        timeout_seconds=args.timeout,
        connect_timeout_seconds=args.connect_timeout,
        retries=max(args.retries, 0),
    )
    print(json.dumps({"records": len(items), "manifest": (args.output_dir / 'open_access_papers_manifest.jsonl').as_posix()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
