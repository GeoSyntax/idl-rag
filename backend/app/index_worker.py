"""Standalone document-index worker used by Docker Compose.

The development server keeps the same worker in-process by default.  A
production deployment can disable that thread and run this module as a
separate container while sharing only the application data volume.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from app.core.config import get_app_settings
from app.db.database import get_index_session_factory, init_database
from app.services.ingest_service import IngestService
from app.services.research_run_service import ResearchRunService

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_heartbeat(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def run_index_worker(
    *,
    stop_event: Event,
    state: dict[str, object] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Process queued document jobs until ``stop_event`` is set.

    ``sleep`` is injectable for deterministic lifecycle tests; production uses
    ``stop_event.wait`` so shutdown remains responsive.
    """
    settings = get_app_settings()
    worker_state: dict[str, object] = state if state is not None else {}
    worker_state.setdefault("mode", "external")
    worker_state.setdefault("started_at", _now())
    worker_state.setdefault("processed_count", 0)
    worker_state.setdefault("last_error", None)
    worker_state["status"] = "running"
    heartbeat_path = settings.index_worker_heartbeat_path
    service = IngestService()
    research_run_service = ResearchRunService()
    session_factory = get_index_session_factory()
    wait = sleep or (lambda seconds: stop_event.wait(seconds))

    try:
        init_database()
        while not stop_event.is_set():
            worker_state["last_heartbeat_at"] = _now()
            _write_heartbeat(heartbeat_path, worker_state)
            db = session_factory()
            processed = False
            try:
                processed = service.process_next_job(db)
                if not processed:
                    processed = research_run_service.process_next_queued_run(db)
                if processed:
                    worker_state["processed_count"] = int(worker_state.get("processed_count") or 0) + 1
                    worker_state["last_error"] = None
            except Exception as exc:  # noqa: BLE001 - worker must survive one bad job
                db.rollback()
                worker_state["last_error"] = str(exc)[:500]
                logger.exception("Standalone index worker failed while processing a job.")
            finally:
                db.close()
            if not processed:
                wait(0.2)
    finally:
        worker_state["status"] = "stopped"
        worker_state["last_heartbeat_at"] = _now()
        _write_heartbeat(heartbeat_path, worker_state)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop_event = Event()
    try:
        run_index_worker(stop_event=stop_event)
    except KeyboardInterrupt:
        stop_event.set()


if __name__ == "__main__":
    main()
