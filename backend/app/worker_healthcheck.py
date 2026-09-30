"""Small process healthcheck for the standalone Compose worker.

The API readiness endpoint and the container healthcheck intentionally use the
same heartbeat contract: a worker is healthy only while its state is
``running`` and its last heartbeat is within the configured timeout.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_app_settings


def check_worker_heartbeat(path: Path | None = None, timeout_seconds: float | None = None) -> None:
    """Raise ``ValueError`` unless the standalone worker heartbeat is fresh."""

    settings = get_app_settings()
    heartbeat_path = (path or settings.index_worker_heartbeat_path).resolve()
    timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else float(os.getenv("IDLRAG_INDEX_WORKER_HEARTBEAT_TIMEOUT_SECONDS", settings.index_worker_heartbeat_timeout_seconds))
    )
    if not heartbeat_path.is_file():
        raise ValueError(f"worker heartbeat missing: {heartbeat_path}")
    try:
        payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        timestamp = datetime.fromisoformat(str(payload["last_heartbeat_at"]).replace("Z", "+00:00"))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"worker heartbeat invalid: {heartbeat_path}") from exc
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    age_seconds = (datetime.now(UTC) - timestamp).total_seconds()
    if payload.get("status") != "running" or age_seconds < 0 or age_seconds > timeout:
        raise ValueError(f"worker heartbeat stale or stopped: status={payload.get('status')!r}, age={age_seconds:.1f}s")


def main() -> int:
    try:
        check_worker_heartbeat()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
