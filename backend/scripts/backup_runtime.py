"""Create and verify a reproducible runtime backup archive.

The archive contains the SQLite database, parsed sources, indexes, generated
research artifacts and source manifests.  It intentionally excludes caches,
logs, worker heartbeats and previous backups.  Archives may contain private
documents and encrypted provider settings; store them in protected backup
storage and do not commit them to Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


EXCLUDED_DIRS = {"cache", "logs", "worker", "backups"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot_database(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def _iter_runtime_files(data_dir: Path, *, include_logs: bool) -> list[Path]:
    files: list[Path] = []
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(data_dir)
        if relative.parts and relative.parts[0] in EXCLUDED_DIRS:
            if relative.parts[0] != "logs" or not include_logs:
                continue
        files.append(path)
    return files


def create_backup(data_dir: Path, output: Path, *, include_logs: bool = False) -> dict[str, Any]:
    data_dir = data_dir.resolve()
    output = output.resolve()
    if not data_dir.exists():
        raise FileNotFoundError(data_dir)
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="idl-rag-backup-") as temporary:
        temporary_db = Path(temporary) / "app.db"
        database = data_dir / "app.db"
        _snapshot_database(database, temporary_db)
        source_files = _iter_runtime_files(data_dir, include_logs=include_logs)
        source_files = [
            path for path in source_files
            if path.resolve() != output and path.relative_to(data_dir).as_posix() != "app.db"
        ]
        entries: list[dict[str, Any]] = []
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(temporary_db, "app.db")
            entries.append({"path": "app.db", "bytes": temporary_db.stat().st_size, "sha256": _sha256(temporary_db)})
            for path in source_files:
                relative = path.relative_to(data_dir).as_posix()
                archive.write(path, relative)
                entries.append({"path": relative, "bytes": path.stat().st_size, "sha256": _sha256(path)})
            manifest = {
                "backup_version": 1,
                "created_at": datetime.now(UTC).isoformat(),
                "data_dir": data_dir.as_posix(),
                "include_logs": include_logs,
                "file_count": len(entries),
                "total_bytes": sum(int(item["bytes"]) for item in entries),
                "files": entries,
            }
            archive.writestr("backup_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest | {"output": output.as_posix()}


def verify_backup(archive_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    with zipfile.ZipFile(archive_path) as archive:
        try:
            manifest = json.loads(archive.read("backup_manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            return {"status": "fail", "errors": [f"invalid backup manifest: {exc}"]}
        names = set(archive.namelist())
        for item in manifest.get("files", []):
            relative = str(item.get("path") or "")
            if relative not in names:
                errors.append(f"missing archive member: {relative}")
                continue
            digest = hashlib.sha256(archive.read(relative)).hexdigest()
            if digest != item.get("sha256"):
                errors.append(f"SHA-256 mismatch: {relative}")
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "file_count": manifest.get("file_count", 0),
        "archive": archive_path.resolve().as_posix(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-logs", action="store_true")
    parser.add_argument("--verify", type=Path, help="verify an existing backup instead of creating one")
    args = parser.parse_args()
    if args.verify:
        result = verify_backup(args.verify)
    else:
        output = args.output or args.data_dir / "backups" / f"idl-rag-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.zip"
        result = create_backup(args.data_dir, output, include_logs=args.include_logs)
        result = {"status": "created", **{key: result[key] for key in ("output", "file_count", "total_bytes")}}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
