"""Build an auditable redistribution-license review queue for OA PDFs.

OpenAlex's ``is_open_access`` flag is a discovery signal, not a redistribution
grant.  This script intentionally does not infer a license from a publisher or
URL.  It produces a deterministic queue containing the provenance needed for a
human reviewer to record an explicit license decision and evidence URL.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse
from typing import Any


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: record must be an object")
        records.append(value)
    return records


def _host(url: str) -> str:
    return (urlparse(url).hostname or "unknown").lower()


def _review_evidence(record: dict[str, Any]) -> dict[str, Any]:
    """Load reviewer evidence without inferring permission from OA metadata.

    Older manifests have no evidence fields.  Newer manifests may store them
    either under ``review_evidence`` or as top-level fields so a reviewer can
    edit JSONL with a simple line-oriented workflow.  The report keeps the
    evidence visible and leaves missing values explicit instead of silently
    converting an OA flag into a clearance.
    """
    nested = record.get("review_evidence")
    nested_values = nested if isinstance(nested, dict) else {}

    def value(*keys: str, default: Any = "") -> Any:
        for key in keys:
            if key in nested_values and nested_values[key] not in (None, ""):
                return nested_values[key]
            if key in record and record[key] not in (None, ""):
                return record[key]
        return default

    return {
        "license_url": value("license_url"),
        "license_name": value("license_name"),
        "redistribution_allowed": value("redistribution_allowed", default=None),
        "reviewer": value("reviewer"),
        "reviewed_at": value("reviewed_at"),
        "evidence_sha256": value("evidence_sha256"),
        "notes": value("notes", "license_notes"),
    }


def _load_candidates(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    candidates: dict[str, dict[str, Any]] = {}
    for item in _load_jsonl(path):
        file_name = str(item.get("file_name") or "")
        if file_name:
            candidates[file_name] = item
    return candidates


def build_review_report(manifest: Path, candidates_path: Path | None = None) -> dict[str, Any]:
    records = _load_jsonl(manifest)
    candidates = _load_candidates(candidates_path)
    queue: list[dict[str, Any]] = []
    for record in records:
        license_status = str(record.get("license_status") or "")
        cleared = license_status.startswith("cleared_")
        candidate = candidates.get(str(record.get("file_name") or ""), {})
        queue.append(
            {
                "file_name": record.get("file_name") or "",
                "title": record.get("title") or "",
                "doi": record.get("doi") or "",
                "openalex_id": record.get("openalex_id") or "",
                "source_url": record.get("source_url") or "",
                "source_host": _host(str(record.get("source_url") or "")),
                "publisher": record.get("publisher") or "",
                "year": record.get("year"),
                "sha256": record.get("sha256") or "",
                "license_status": license_status,
                "review_state": "cleared" if cleared else "needs_manual_review",
                "review_evidence": _review_evidence(record),
                "metadata_candidate": {
                    "status": candidate.get("candidate_status", "not_collected"),
                    "license_candidates": candidate.get("license_candidates", []),
                    "crossref_url": candidate.get("crossref_url", ""),
                    "retrieved_at": candidate.get("retrieved_at", ""),
                    "error": candidate.get("error", ""),
                },
            }
        )

    pending = [item for item in queue if item["review_state"] != "cleared"]
    return {
        "report_version": 1,
        "manifest": manifest.as_posix(),
        "total_records": len(queue),
        "cleared_records": len(queue) - len(pending),
        "pending_records": len(pending),
        "metadata_candidate_records": sum(bool(candidates.get(str(item.get("file_name") or ""))) for item in queue),
        "metadata_license_candidate_records": sum(
            bool(item["metadata_candidate"]["license_candidates"]) for item in queue
        ),
        "metadata_error_records": sum(bool(item["metadata_candidate"]["error"]) for item in queue),
        "metadata_without_license_records": sum(
            item["metadata_candidate"]["status"] == "no_license_metadata" for item in queue
        ),
        "by_source_host": dict(sorted(Counter(item["source_host"] for item in pending).items())),
        "by_publisher": dict(sorted(Counter(item["publisher"] or "unknown" for item in pending).items())),
        "review_policy": {
            "openalex_oa_is_not_redistribution_permission": True,
            "required_before_public_release": [
                "license_url",
                "license_name",
                "redistribution_allowed",
                "reviewer",
                "reviewed_at",
                "evidence_sha256",
            ],
            "cleared_status_prefix": "cleared_",
        },
        "records": queue,
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# OA PDF redistribution-license review queue",
        "",
        f"- Manifest: `{report['manifest']}`",
        f"- Total records: {report['total_records']}",
        f"- Cleared records: {report['cleared_records']}",
        f"- Pending manual review: {report['pending_records']}",
        f"- Crossref metadata candidates: {report.get('metadata_license_candidate_records', 0)}",
        f"- Crossref metadata errors: {report.get('metadata_error_records', 0)}",
        "",
        "This queue deliberately does not infer permission from an OpenAlex OA flag, publisher, or domain.",
        "A reviewer must record the license page, license name, redistribution decision, identity/date and evidence hash.",
        "",
        "## Pending records by source host",
        "",
    ]
    for host, count in report["by_source_host"].items():
        lines.append(f"- `{host}`: {count}")
    lines.extend(["", "## Review table", "", "| File | Publisher | DOI | Status | License URL | Reviewer |", "| --- | --- | --- | --- | --- | --- |"])
    for item in report["records"]:
        evidence = item["review_evidence"]
        lines.append(
            "| {file} | {publisher} | {doi} | {status} | {license_url} | {reviewer} |".format(
                file=item["file_name"].replace("|", "\\|"),
                publisher=(item["publisher"] or "unknown").replace("|", "\\|"),
                doi=item["doi"].replace("|", "\\|"),
                status=item["review_state"],
                license_url=evidence["license_url"],
                reviewer=evidence["reviewer"],
            )
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("data/sources/open_access_papers/open_access_papers_manifest.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("data/logs/license_review_report.json"))
    parser.add_argument("--markdown", type=Path, default=Path("data/logs/license_review_report.md"))
    parser.add_argument("--candidates", type=Path, default=Path("data/logs/license_candidates.jsonl"))
    args = parser.parse_args()
    report = build_review_report(args.manifest, args.candidates)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("total_records", "cleared_records", "pending_records")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
