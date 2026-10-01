"""Build a deterministic SHA-256 manifest for a local source directory."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(source_dir: Path, output: Path) -> dict[str, object]:
    files: list[dict[str, object]] = []
    for path in sorted(item for item in source_dir.rglob("*") if item.is_file() and item.name != output.name):
        stat = path.stat()
        files.append(
            {
                "path": path.relative_to(source_dir).as_posix(),
                "bytes": stat.st_size,
                "sha256": sha256(path),
                "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            }
        )
    payload = {
        "manifest_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_dir": source_dir.as_posix(),
        "file_count": len(files),
        "files": files,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.source_dir / "source_manifest.json"
    payload = build(args.source_dir, output)
    print(json.dumps({"output": output.as_posix(), "file_count": payload["file_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

