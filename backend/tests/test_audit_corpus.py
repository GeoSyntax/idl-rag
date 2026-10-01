from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_corpus import audit
from scripts.build_source_manifest import build


def test_audit_corpus_validates_open_access_provenance_and_hashes(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    papers = source_root / "open_access_papers"
    papers.mkdir(parents=True)
    pdf = papers / "paper_001.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")

    import hashlib

    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    (papers / "open_access_papers_manifest.jsonl").write_text(
        json.dumps(
            {
                "file_name": pdf.name,
                "doi": "10.1234/example",
                "source_url": "https://example.org/example.pdf",
                "sha256": digest,
                "license_status": "openalex_oa_flag__redistribution_terms_must_be_verified",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    build(papers, papers / "source_manifest.json")

    payload = audit(source_root)
    assert payload["status"] == "pass"
    assert payload["source_classes"]["open_access_papers"]["open_access"]["records"] == 1

    pdf.write_bytes(b"%PDF-1.4 changed")
    changed = audit(source_root)
    assert changed["status"] == "fail"
    assert changed["source_classes"]["open_access_papers"]["open_access"]["hash_mismatches"] == [pdf.name]
