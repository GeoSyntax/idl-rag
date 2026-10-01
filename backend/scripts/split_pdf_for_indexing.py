"""Split a large source PDF into provenance-preserving indexing parts.

The original PDF is never modified.  Each generated part records its source
SHA-256 and inclusive page range so citations can be mapped back to the
authoritative document.  This is intended for PDFs that exceed the ingestion
page limit while still needing full-text retrieval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def split_pdf(source: Path, output_dir: Path, max_pages: int = 250) -> dict[str, Any]:
    if max_pages <= 0:
        raise ValueError("max_pages must be positive")
    source = source.resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError(f"source is not a PDF: {source}")

    reader = PdfReader(str(source))
    page_count = len(reader.pages)
    source_hash = _sha256(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    parts: list[dict[str, Any]] = []

    for start in range(0, page_count, max_pages):
        end = min(page_count, start + max_pages)
        part_number = (start // max_pages) + 1
        output = output_dir / f"{source.stem}.part-{part_number:03d}.pdf"
        writer = PdfWriter()
        for page_index in range(start, end):
            writer.add_page(reader.pages[page_index])
        with output.open("wb") as handle:
            writer.write(handle)
        parts.append(
            {
                "path": output.relative_to(output_dir).as_posix(),
                "page_start": start + 1,
                "page_end": end,
                "page_count": end - start,
                "bytes": output.stat().st_size,
                "sha256": _sha256(output),
            }
        )

    manifest = {
        "manifest_version": 1,
        "source_file": source.as_posix(),
        "source_sha256": source_hash,
        "source_page_count": page_count,
        "max_pages_per_part": max_pages,
        "parts": parts,
    }
    manifest_path = output_dir / "split_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest | {"manifest": manifest_path.as_posix()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--max-pages", type=int, default=250)
    args = parser.parse_args()
    result = split_pdf(args.source, args.output_dir, args.max_pages)
    print(
        json.dumps(
            {
                "source_page_count": result["source_page_count"],
                "parts": len(result["parts"]),
                "manifest": result["manifest"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
