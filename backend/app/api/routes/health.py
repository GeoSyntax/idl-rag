from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.schemas import HealthResponse, ReadinessResponse
from app.core.config import get_app_settings
from app.db.database import get_db
from app.worker_healthcheck import check_worker_heartbeat

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    settings = get_app_settings()
    return HealthResponse(status="ok", app_name=settings.app_name)


def _external_worker_check() -> str:
    try:
        check_worker_heartbeat()
    except ValueError:
        return "unhealthy"
    return "ok"


def _worker_check(request: Request) -> str:
    state = getattr(request.app.state, "index_worker_state", {}) or {}
    mode = str(state.get("mode") or "external")
    if mode == "embedded":
        worker = getattr(request.app.state, "index_worker", None)
        return "ok" if worker is not None and worker.is_alive() and state.get("status") == "running" else "starting"
    return _external_worker_check()


@router.get("/ready", response_model=ReadinessResponse)
def readiness(request: Request, db: Session = Depends(get_db)) -> ReadinessResponse:
    checks: dict[str, str] = {"database": "ok", "worker": _worker_check(request)}
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        checks["database"] = "unhealthy"
    if any(value != "ok" for value in checks.values()):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "degraded", "checks": checks},
        )
    settings = get_app_settings()
    return ReadinessResponse(status="ready", app_name=settings.app_name, checks=checks)
