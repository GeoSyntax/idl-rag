from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import AuthUserResponse, LoginRequest, LoginResponse, RegisterRequest
from app.core.config import get_app_settings
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
from app.db.models import ChatSession, KnowledgeBase, User


class AuthService:
    def register(self, db: Session, payload: RegisterRequest) -> LoginResponse:
        username = self._normalize_username(payload.username)
        first_user = self._is_first_user(db)
        user = User(
            username=username,
            password_hash=hash_password(payload.password),
            role="admin" if first_user else "user",
            is_active=True,
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ValueError("用户名已存在。") from exc
        db.refresh(user)

        if first_user:
            self._claim_legacy_data(db, user.id)

        return self._build_login_response(user)

    def login(self, db: Session, payload: LoginRequest) -> LoginResponse:
        username = self._normalize_username(payload.username)
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None or not verify_password(payload.password, user.password_hash):
            raise ValueError("用户名或密码错误。")
        if not user.is_active:
            raise ValueError("账号已被禁用。")
        return self._build_login_response(user)

    def get_current_user(self, db: Session, token: str) -> User:
        settings = get_app_settings()
        token_payload = decode_access_token(token, settings.auth_secret)
        user = db.get(User, token_payload.user_id)
        if user is None or not user.is_active:
            raise ValueError("登录已失效，请重新登录。")
        return user

    def to_user_response(self, user: User) -> AuthUserResponse:
        return AuthUserResponse.model_validate(user)

    def _build_login_response(self, user: User) -> LoginResponse:
        settings = get_app_settings()
        return LoginResponse(
            access_token=create_access_token(
                user_id=user.id,
                username=user.username,
                role=user.role,
                secret=settings.auth_secret,
                expires_minutes=settings.auth_token_expire_minutes,
            ),
            user=self.to_user_response(user),
        )

    def _is_first_user(self, db: Session) -> bool:
        return db.execute(select(func.count(User.id))).scalar_one() == 0

    def _claim_legacy_data(self, db: Session, user_id: int) -> None:
        db.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.owner_user_id.is_(None))
            .values(owner_user_id=user_id)
        )
        db.execute(
            update(ChatSession)
            .where(ChatSession.owner_user_id.is_(None))
            .values(owner_user_id=user_id)
        )
        db.commit()

    def _normalize_username(self, username: str) -> str:
        normalized = username.strip()
        if not normalized:
            raise ValueError("用户名不能为空。")
        return normalized
