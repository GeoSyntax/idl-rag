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
