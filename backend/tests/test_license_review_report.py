from pathlib import Path

from scripts.license_review_report import build_review_report


def test_license_report_keeps_oa_records_pending_without_explicit_clearance(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        "\n".join(
            [
                '{"file_name":"a.pdf","title":"A","doi":"https://doi.org/1","openalex_id":"W1","source_url":"https://publisher.example/a.pdf","publisher":"Example","sha256":"abc","license_status":"openalex_oa_flag__redistribution_terms_must_be_verified"}',
                '{"file_name":"b.pdf","title":"B","doi":"https://doi.org/2","openalex_id":"W2","source_url":"https://repo.example/b.pdf","publisher":"Repo","sha256":"def","license_status":"cleared_cc_by_4.0"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_review_report(manifest)

    assert report["total_records"] == 2
    assert report["cleared_records"] == 1
    assert report["pending_records"] == 1
    assert report["by_source_host"] == {"publisher.example": 1}
    assert report["records"][0]["review_evidence"]["redistribution_allowed"] is None


def test_license_report_preserves_explicit_reviewer_evidence(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        '{"file_name":"a.pdf","title":"A","source_url":"https://publisher.example/a.pdf",'
        '"license_status":"cleared_cc_by_4.0",'
        '"review_evidence":{"license_url":"https://publisher.example/license",'
        '"license_name":"CC BY 4.0","redistribution_allowed":true,"reviewer":"alice",'
        '"reviewed_at":"2026-10-02","evidence_sha256":"abc123","notes":"article page"}}\n',
        encoding="utf-8",
    )

    report = build_review_report(manifest)

    assert report["cleared_records"] == 1
    assert report["records"][0]["review_evidence"] == {
        "license_url": "https://publisher.example/license",
        "license_name": "CC BY 4.0",
        "redistribution_allowed": True,
        "reviewer": "alice",
        "reviewed_at": "2026-10-02",
        "evidence_sha256": "abc123",
        "notes": "article page",
    }


def test_license_report_merges_non_clearing_metadata_candidate(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    manifest.write_text(
        '{"file_name":"a.pdf","title":"A","doi":"https://doi.org/1",'
        '"license_status":"openalex_oa_flag__redistribution_terms_must_be_verified"}\n',
        encoding="utf-8",
    )
    candidates.write_text(
        '{"file_name":"a.pdf","candidate_status":"metadata_license_found",'
        '"license_candidates":[{"url":"https://creativecommons.org/licenses/by/4.0/"}],'
        '"crossref_url":"https://api.crossref.org/works/1","error":""}\n',
        encoding="utf-8",
    )

    report = build_review_report(manifest, candidates)

    assert report["pending_records"] == 1
    assert report["metadata_license_candidate_records"] == 1
    assert report["records"][0]["metadata_candidate"]["license_candidates"][0]["url"].endswith("by/4.0/")
