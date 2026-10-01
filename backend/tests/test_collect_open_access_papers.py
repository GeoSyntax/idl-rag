from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter

from scripts.collect_open_access_papers import (
    _manifest_key,
    _reconcile_orphan_pdfs,
    _write_manifest,
)


def _record() -> dict[str, object]:
    return {
        "title": "A remote sensing validation paper",
        "doi": "https://doi.org/10.1234/example",
        "openalex_id": "https://openalex.org/W1",
        "oa_pdf_url": "https://arxiv.org/pdf/1234.5678",
        "source_name": "Example venue",
        "year": 2025,
        "abstract": "validation landsat",
        "is_open_access": True,
    }


def test_reconcile_orphan_pdf_restores_provenance_and_hash(tmp_path: Path) -> None:
    record = _record()
    orphan = tmp_path / "paper_001_A_remote_sensing_validation_paper.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    orphan.write_bytes(output.getvalue())

    existing: dict[str, dict[str, object]] = {}
    recovered = _reconcile_orphan_pdfs([record], tmp_path, existing)

    assert recovered == 1
    item = existing[record["doi"]]
    assert item["file_name"] == orphan.name
    assert item["source_url"] == record["oa_pdf_url"]
    assert item["bytes"] == orphan.stat().st_size
    assert item["sha256"]
    assert item["license_status"].startswith("openalex_oa_flag")


def test_write_manifest_is_deterministic_and_atomic(tmp_path: Path) -> None:
    path = tmp_path / "open_access_papers_manifest.jsonl"
    items = {
        "b": {"doi": "b", "file_name": "b.pdf"},
        "a": {"doi": "a", "file_name": "a.pdf"},
    }

    written = _write_manifest(path, items)

    assert [_manifest_key(item) for item in written] == ["b", "a"]
    assert [json.loads(line)["doi"] for line in path.read_text(encoding="utf-8").splitlines()] == ["b", "a"]
    assert not (tmp_path / ".open_access_papers_manifest.jsonl.tmp").exists()
