import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Event, Thread

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    auth,
    chat,
    dashboard,
    documents,
    evaluation,
    health,
    knowledge_bases,
    research,
    settings,
    users,
)
from app.core.config import get_app_settings
from app.db.database import init_database
from app.index_worker import run_index_worker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_database()
    settings = get_app_settings()
    worker_state = {
        "mode": "embedded" if settings.index_worker_enabled else "external",
        "status": "disabled" if not settings.index_worker_enabled else "starting",
        "started_at": None,
        "last_heartbeat_at": None,
        "last_error": None,
        "processed_count": 0,
    }
    app.state.index_worker_state = worker_state
    app.state.index_worker = None
    app.state.index_worker_stop = None
    if settings.index_worker_enabled:
        stop_event = Event()
        worker = Thread(
            target=run_index_worker,
            kwargs={"stop_event": stop_event, "state": worker_state},
            name="idl-rag-index-worker",
            daemon=True,
        )
        app.state.index_worker_stop = stop_event
        app.state.index_worker = worker
        worker.start()
    try:
        yield
    finally:
        stop_event = getattr(app.state, "index_worker_stop", None)
        worker = getattr(app.state, "index_worker", None)
        if stop_event is not None and worker is not None:
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
    app.include_router(research.router, prefix=app_settings.api_prefix)

    return app


app = create_app()
