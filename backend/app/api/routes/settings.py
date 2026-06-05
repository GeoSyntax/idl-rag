from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin
from app.api.schemas import SystemSettingsPayload, SystemSettingsResponse, TestConnectionResponse
from app.db.database import get_db
from app.db.models import User
from app.services.settings_service import (
    get_current_settings,
    save_settings,
    test_connection,
    test_langsmith_connection,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=SystemSettingsResponse)
def read_settings(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> SystemSettingsResponse:
    return get_current_settings(db)


@router.put("", response_model=SystemSettingsResponse)
def update_settings(
    payload: SystemSettingsPayload,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> SystemSettingsResponse:
    return save_settings(db, payload)


@router.post("/test-connection", response_model=TestConnectionResponse)
def test_settings_connection(
    payload: SystemSettingsPayload,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> TestConnectionResponse:
    return test_connection(db, payload)


@router.post("/test-langsmith-connection", response_model=TestConnectionResponse)
def test_settings_langsmith_connection(
    payload: SystemSettingsPayload,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> TestConnectionResponse:
    return test_langsmith_connection(db, payload)
