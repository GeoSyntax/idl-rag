"""Audit source manifests, provenance metadata and duplicate content.

This is intentionally independent from the web application so it can run in a
release job or a backup verification job.  It audits the *source of truth*
under ``data/sources``; the database readiness endpoint separately audits the
parsed/indexed copy.

Examples::

    python backend/scripts/audit_corpus.py data/sources --strict
    python backend/scripts/audit_corpus.py data/sources --output data/logs/corpus_audit.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader

MANIFEST_NAME = "source_manifest.json"
OPEN_ACCESS_MANIFEST = "open_access_papers_manifest.jsonl"
PROVENANCE_FIELDS = ("doi", "source_url", "sha256", "license_status")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
            raise ValueError(f"{path}:{line_number}: expected an object")
        records.append(value)
    return records


def _audit_manifest(directory: Path, files: list[Path], errors: list[str], verify_hashes: bool) -> dict[str, Any]:
    path = directory / MANIFEST_NAME
    if not path.exists():
        errors.append(f"{directory}: missing {MANIFEST_NAME}")
        return {"present": False, "file_count": 0, "hash_mismatches": []}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{path}: invalid JSON: {exc}")
        return {"present": True, "file_count": 0, "hash_mismatches": []}

    entries = payload.get("files", [])
    if not isinstance(entries, list):
        errors.append(f"{path}: files must be a list")
        return {"present": True, "file_count": 0, "hash_mismatches": []}

    actual = {item.relative_to(directory).as_posix(): item for item in files if item != path}
    listed = {str(item.get("path")): item for item in entries if isinstance(item, dict)}
    missing = sorted(set(listed) - set(actual))
    unlisted = sorted(set(actual) - set(listed))
    if missing:
        errors.append(f"{path}: missing files: {', '.join(missing[:5])}")
    if unlisted:
        errors.append(f"{path}: unlisted files: {', '.join(unlisted[:5])}")

    mismatches: list[str] = []
    if verify_hashes:
        for relative, entry in listed.items():
            file_path = actual.get(relative)
            expected = str(entry.get("sha256") or "")
            if file_path is not None and expected and _sha256(file_path) != expected:
                mismatches.append(relative)
        if mismatches:
            errors.append(f"{path}: SHA-256 mismatch: {', '.join(mismatches[:5])}")

    expected_count = payload.get("file_count")
    if expected_count != len(entries):
        errors.append(f"{path}: file_count={expected_count!r} but listed {len(entries)} files")
    return {
        "present": True,
        "file_count": len(entries),
        "missing_files": missing,
        "unlisted_files": unlisted,
        "hash_mismatches": mismatches,
    }


def _audit_open_access(directory: Path, errors: list[str], verify_hashes: bool) -> dict[str, Any]:
    path = directory / OPEN_ACCESS_MANIFEST
    if not path.exists():
        errors.append(f"{directory}: missing {OPEN_ACCESS_MANIFEST}")
        return {"manifest_present": False, "records": 0, "license_review": 0}
    try:
        records = _load_jsonl(path)
    except ValueError as exc:
        errors.append(str(exc))
        return {"manifest_present": True, "records": 0, "license_review": 0}

    pdfs = {file.name: file for file in directory.glob("*.pdf")}
    seen_files: set[str] = set()
    missing_fields = 0
    license_review = 0
    hash_mismatches: list[str] = []
    invalid_pdfs: list[str] = []
    for record in records:
        file_name = str(record.get("file_name") or "")
        missing = [field for field in PROVENANCE_FIELDS if not record.get(field)]
        if missing:
            missing_fields += 1
            errors.append(f"{path}: {file_name or '<unknown>'} missing {', '.join(missing)}")
        if str(record.get("license_status", "")).startswith("openalex_oa_flag"):
            license_review += 1
        file_path = pdfs.get(file_name)
        if file_path is None:
            errors.append(f"{path}: recorded PDF does not exist: {file_name}")
            continue
        seen_files.add(file_name)
        if verify_hashes and record.get("sha256") and _sha256(file_path) != record["sha256"]:
            hash_mismatches.append(file_name)
        try:
            if len(PdfReader(file_path, strict=False).pages) < 1:
                invalid_pdfs.append(file_name)
        except Exception:  # noqa: BLE001 - audit records invalid third-party PDFs
            invalid_pdfs.append(file_name)
    unrecorded = sorted(set(pdfs) - seen_files)
    if unrecorded:
        errors.append(f"{path}: unrecorded PDFs: {', '.join(unrecorded[:5])}")
    if hash_mismatches:
        errors.append(f"{path}: SHA-256 mismatch: {', '.join(hash_mismatches[:5])}")
    if invalid_pdfs:
        errors.append(f"{path}: invalid or unreadable PDFs: {', '.join(invalid_pdfs[:5])}")
    return {
        "manifest_present": True,
        "records": len(records),
        "pdfs": len(pdfs),
        "missing_provenance_records": missing_fields,
        "license_review_records": license_review,
        "unrecorded_pdfs": unrecorded,
        "hash_mismatches": hash_mismatches,
        "invalid_pdfs": invalid_pdfs,
    }


def audit(source_root: Path, *, verify_hashes: bool = True) -> dict[str, Any]:
    source_root = source_root.resolve()
    errors: list[str] = []
    classes: dict[str, Any] = {}
    hashes: defaultdict[str, list[str]] = defaultdict(list)
    if not source_root.exists():
        errors.append(f"source root does not exist: {source_root}")
    else:
        for directory in sorted(item for item in source_root.iterdir() if item.is_dir()):
            files = sorted(item for item in directory.rglob("*") if item.is_file())
            manifest = _audit_manifest(directory, files, errors, verify_hashes)
            class_result: dict[str, Any] = {
                "file_count": len(files),
                "manifest": manifest,
            }
            if directory.name == "open_access_papers":
                class_result["open_access"] = _audit_open_access(directory, errors, verify_hashes)
            for file_path in files:
                if file_path.name == MANIFEST_NAME:
                    continue
                digest = _sha256(file_path) if verify_hashes else ""
                if digest:
                    hashes[digest].append(file_path.relative_to(source_root).as_posix())
            classes[directory.name] = class_result

    duplicate_hashes = {digest: paths for digest, paths in hashes.items() if len(paths) > 1}
    return {
        "audit_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_root": source_root.as_posix(),
        "verify_hashes": verify_hashes,
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "errors": errors,
        "duplicate_hashes": duplicate_hashes,
        "source_classes": classes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-hash", action="store_true", help="skip SHA-256 verification")
    parser.add_argument("--strict", action="store_true", help="exit 1 when any audit error is found")
    args = parser.parse_args()
    payload = audit(args.source_root, verify_hashes=not args.no_hash)
    output = args.output
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "errors": payload["error_count"], "output": output.as_posix() if output else None}, ensure_ascii=False))
    if args.strict and payload["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
