from __future__ import annotations

from scripts.runtime_diagnostics import _check_gee, _check_idl


def test_optional_integrations_report_warnings_without_blocking() -> None:
    assert _check_gee(False, "adc", "", False, required=False)["status"] == "warn"
    assert _check_idl("", required=False)["status"] == "warn"


def test_required_integrations_fail_when_not_configured() -> None:
    assert _check_gee(False, "adc", "", False, required=True)["status"] == "fail"
    assert _check_idl("", required=True)["status"] == "fail"
