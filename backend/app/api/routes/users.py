from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin
from app.api.schemas import AuthUserResponse, ResetPasswordRequest, UserUpdateRequest
from app.db.database import get_db
from app.db.models import User
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])
service = UserService()


@router.get("", response_model=list[AuthUserResponse])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[AuthUserResponse]:
    return service.list_users(db)


@router.patch("/{user_id}", response_model=AuthUserResponse)
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> AuthUserResponse:
    try:
        return service.update_user(db, user_id, payload, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{user_id}/reset-password", response_model=AuthUserResponse)
def reset_password(
    user_id: int,
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> AuthUserResponse:
    try:
        return service.reset_password(db, user_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
