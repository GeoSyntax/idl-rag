from scripts.collect_license_candidates import build_candidate


def test_license_candidate_is_evidence_only_and_does_not_clear_record() -> None:
    record = {
        "file_name": "paper.pdf",
        "title": "Example",
        "doi": "https://doi.org/10.1234/example",
        "source_url": "https://publisher.example/paper.pdf",
        "publisher": "Example",
        "sha256": "source-hash",
        "license_status": "openalex_oa_flag__redistribution_terms_must_be_verified",
    }
    candidate = build_candidate(
        record,
        {
            "license": [
                {
                    "URL": "https://creativecommons.org/licenses/by/4.0/",
                    "content-version": "vor",
                    "delay-in-days": 0,
                }
            ]
        },
    )

    assert candidate["candidate_status"] == "metadata_license_found"
    assert candidate["license_candidates"][0]["url"].endswith("by/4.0/")
    assert "license_status" not in candidate
    assert candidate["source_sha256"] == "source-hash"
