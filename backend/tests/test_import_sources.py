from __future__ import annotations

from sqlalchemy.exc import OperationalError

from scripts.import_sources import _is_database_locked, _retry_database_lock


def _locked_error() -> OperationalError:
    return OperationalError("INSERT", {}, Exception("database is locked"))


def test_database_lock_retry_waits_then_returns() -> None:
    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _locked_error()
        return "ok"

    assert _retry_database_lock(operation, timeout_seconds=2) == "ok"
    assert attempts == 3


def test_non_lock_database_errors_are_not_hidden() -> None:
    error = OperationalError("INSERT", {}, Exception("constraint failed"))
    assert not _is_database_locked(error)
