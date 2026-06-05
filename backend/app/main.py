import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from threading import Event, Thread

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, chat, dashboard, documents, evaluation, health, knowledge_bases, settings, users
from app.core.config import get_app_settings
from app.db.database import get_index_session_factory, init_database
from app.services.ingest_service import IngestService

logger = logging.getLogger(__name__)


def _run_index_worker(*, stop_event: Event, state: dict) -> None:
    service = IngestService()
    session_factory = get_index_session_factory()
    state["started_at"] = datetime.utcnow().isoformat()
    while not stop_event.is_set():
        state["last_heartbeat_at"] = datetime.utcnow().isoformat()
        db = session_factory()
        processed = False
        try:
            processed = service.process_next_job(db)
            if processed:
                state["processed_count"] = int(state.get("processed_count") or 0) + 1
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            state["last_error"] = str(exc)[:500]
            logger.exception("Index worker failed while processing a job.")
        finally:
            db.close()
        if not processed:
            stop_event.wait(0.2)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_database()
    stop_event = Event()
    worker_state = {
        "started_at": None,
        "last_heartbeat_at": None,
        "last_error": None,
        "processed_count": 0,
    }
    worker = Thread(
        target=_run_index_worker,
        kwargs={"stop_event": stop_event, "state": worker_state},
        name="idl-rag-index-worker",
        daemon=True,
    )
    app.state.index_worker_stop = stop_event
    app.state.index_worker_state = worker_state
    app.state.index_worker = worker
    worker.start()
    try:
        yield
    finally:
        stop_event.set()
        worker.join(timeout=2)


def create_app() -> FastAPI:
    app_settings = get_app_settings()
    app_settings.validate_auth_secret()
    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    cors_origins = getattr(app_settings, "cors_origins", None) or [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    if isinstance(cors_origins, str):
        cors_origins = [o.strip() for o in cors_origins.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix=app_settings.api_prefix)
    app.include_router(auth.router, prefix=app_settings.api_prefix)
    app.include_router(users.router, prefix=app_settings.api_prefix)
    app.include_router(settings.router, prefix=app_settings.api_prefix)
    app.include_router(dashboard.router, prefix=app_settings.api_prefix)
    app.include_router(knowledge_bases.router, prefix=app_settings.api_prefix)
    app.include_router(documents.router, prefix=app_settings.api_prefix)
    app.include_router(chat.router, prefix=app_settings.api_prefix)
    app.include_router(evaluation.router, prefix=app_settings.api_prefix)

    return app


app = create_app()
