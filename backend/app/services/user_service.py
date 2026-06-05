from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import AuthUserResponse, ResetPasswordRequest, UserUpdateRequest
from app.core.security import hash_password
from app.db.models import User


class UserService:
    def list_users(self, db: Session) -> list[AuthUserResponse]:
        users = db.execute(select(User).order_by(User.created_at.desc(), User.id.desc())).scalars().all()
        return [self._to_response(user) for user in users]

    def update_user(
        self,
        db: Session,
        user_id: int,
        payload: UserUpdateRequest,
        current_user_id: int | None = None,
    ) -> AuthUserResponse:
        user = self._get_user_or_raise(db, user_id)
        if payload.is_active is False and user.id == current_user_id:
            raise PermissionError("不能禁用当前登录的管理员账号。")
        if self._would_remove_active_admin(db, user, payload):
            raise PermissionError("至少需要保留一个启用的管理员账号。")
        if payload.role is not None:
            user.role = payload.role
        if payload.is_active is not None:
            user.is_active = payload.is_active
        db.commit()
        db.refresh(user)
        return self._to_response(user)

    def reset_password(self, db: Session, user_id: int, payload: ResetPasswordRequest) -> AuthUserResponse:
        user = self._get_user_or_raise(db, user_id)
        user.password_hash = hash_password(payload.new_password)
        db.commit()
        db.refresh(user)
        return self._to_response(user)

    def _get_user_or_raise(self, db: Session, user_id: int) -> User:
        user = db.get(User, user_id)
        if user is None:
            raise ValueError("用户不存在。")
        return user

    def _would_remove_active_admin(self, db: Session, user: User, payload: UserUpdateRequest) -> bool:
        if user.role != "admin" or not user.is_active:
            return False
        next_role = payload.role if payload.role is not None else user.role
        next_active = payload.is_active if payload.is_active is not None else user.is_active
        if next_role == "admin" and next_active:
            return False
        return self._active_admin_count(db) <= 1

    def _active_admin_count(self, db: Session) -> int:
        return db.execute(
            select(func.count(User.id)).where(User.role == "admin", User.is_active.is_(True))
        ).scalar_one()

    def _to_response(self, user: User) -> AuthUserResponse:
        return AuthUserResponse.model_validate(user)
