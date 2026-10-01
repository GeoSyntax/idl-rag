"""Collect publisher license metadata without silently clearing redistribution.

OpenAlex OA flags and Crossref license metadata are discovery evidence, not a
legal decision.  This script creates a separate, reviewable JSONL queue.  It
never edits the source manifest and never changes ``license_status``.  A human
reviewer must still verify the article page/license terms and write explicit
``review_evidence`` plus a ``cleared_*`` status before public release.

The local Windows environment uses ``curl.exe`` for HTTPS because the desktop
proxy is configured for curl while Python's default TLS path may fail.  Each
record is fetched independently; one timeout does not discard the rest.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _doi(record: dict[str, Any]) -> str:
    return str(record.get("doi") or "").strip().removeprefix("https://doi.org/")


def _fetch_crossref(doi: str, *, timeout_seconds: int = 20, retries: int = 3) -> dict[str, Any]:
    if not shutil.which("curl.exe"):
        raise RuntimeError("curl.exe is required to collect Crossref license metadata")
    url = f"https://api.crossref.org/works/{quote(doi, safe="")}"  # noqa: S603 - fixed executable, URL is encoded
    last_error = ""
    for attempt in range(retries):
        result = subprocess.run(  # noqa: S603 - fixed curl executable and argument list
            [
                "curl.exe",
                "-L",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                str(timeout_seconds),
                "-H",
                "User-Agent: idl-rag-license-audit/1.0 (local research installation)",
                url,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode == 0:
            payload = json.loads(result.stdout)
            message = payload.get("message")
            if isinstance(message, dict):
                return message
            raise ValueError("Crossref response has no message object")
        last_error = result.stderr.strip() or f"curl exit {result.returncode}"
        if attempt + 1 < retries:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last_error)


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_candidate(record: dict[str, Any], metadata: dict[str, Any] | None, error: str = "") -> dict[str, Any]:
    licenses = metadata.get("license") if isinstance(metadata, dict) else None
    licenses = licenses if isinstance(licenses, list) else []
    entries: list[dict[str, Any]] = []
    for item in licenses:
        if not isinstance(item, dict):
            continue
        entries.append(
            {
                "url": str(item.get("URL") or ""),
                "content_version": str(item.get("content-version") or ""),
                "start": item.get("start"),
                "delay_in_days": item.get("delay-in-days"),
            }
        )
    payload = {
        "file_name": record.get("file_name") or "",
        "title": record.get("title") or "",
        "doi": record.get("doi") or "",
        "source_url": record.get("source_url") or "",
        "publisher": record.get("publisher") or "",
        "year": record.get("year"),
        "source_sha256": record.get("sha256") or "",
        "crossref_url": f"https://api.crossref.org/works/{quote(_doi(record), safe="")}" if _doi(record) else "",
        "license_candidates": entries,
        "candidate_status": "metadata_license_found" if entries else "no_license_metadata",
        "error": error,
        "retrieved_at": datetime.now(UTC).isoformat(),
    }
    payload["metadata_sha256"] = _sha256_json(metadata or {"error": error})
    return payload


def collect(
    manifest: Path,
    output: Path,
    *,
    sleep_seconds: float = 0.2,
    timeout_seconds: int = 20,
    retries: int = 3,
    workers: int = 4,
) -> dict[str, int | str]:
    records = _load_jsonl(manifest)
    def one(record: dict[str, Any]) -> dict[str, Any]:
        doi = _doi(record)
        if not doi:
            return build_candidate(record, None, "missing DOI")
        try:
            metadata = _fetch_crossref(doi, timeout_seconds=timeout_seconds, retries=retries)
            return build_candidate(record, metadata)
        except Exception as exc:  # noqa: BLE001 - one provider failure must not lose the queue
            return build_candidate(record, None, str(exc))

    # Preserve manifest order while allowing slow DOI endpoints to run in
    # parallel.  The small inter-submit delay keeps the local proxy and
    # Crossref from receiving a burst when a large corpus is audited.
    worker_count = max(1, min(int(workers), 16))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="license-candidate") as pool:
        futures = []
        for index, record in enumerate(records):
            futures.append(pool.submit(one, record))
            if index + 1 < len(records):
                time.sleep(max(0.0, sleep_seconds))
        results = [future.result() for future in futures]
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in results),
        encoding="utf-8",
    )
    temporary.replace(output)
    return {
        "records": len(results),
        "license_metadata_found": sum(item["candidate_status"] == "metadata_license_found" for item in results),
        "no_license_metadata": sum(item["candidate_status"] == "no_license_metadata" for item in results),
        "errors": sum(bool(item["error"]) for item in results),
        "output": output.as_posix(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("data/sources/open_access_papers/open_access_papers_manifest.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("data/logs/license_candidates.jsonl"))
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(collect(args.manifest, args.output, sleep_seconds=args.sleep_seconds, timeout_seconds=args.timeout_seconds, retries=args.retries, workers=args.workers), ensure_ascii=False))


if __name__ == "__main__":
    main()
