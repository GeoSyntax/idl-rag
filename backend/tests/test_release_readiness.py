from __future__ import annotations

from scripts.release_readiness import _uncleared_license_count


def test_uncleared_license_count_aggregates_open_access_sources() -> None:
    payload = {
        "source_classes": {
            "open_access_papers": {"open_access": {"license_review_records": 186}},
            "remote_sensing_official": {"open_access": {"license_review_records": 0}},
        }
    }

    assert _uncleared_license_count(payload) == 186


def test_uncleared_license_count_tolerates_missing_source_classes() -> None:
    assert _uncleared_license_count({}) == 0

