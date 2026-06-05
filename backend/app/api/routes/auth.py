from collections import defaultdict
from time import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.schemas import AuthUserResponse, LoginRequest, LoginResponse, RegisterRequest
from app.db.database import get_db
from app.db.models import User
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])
service = AuthService()

_register_attempts: dict[str, list[float]] = defaultdict(list)
_login_failures: dict[str, list[float]] = defaultdict(list)
_REGISTER_RATE_LIMIT_WINDOW = 60.0
_REGISTER_RATE_LIMIT_MAX = 3
_LOGIN_RATE_LIMIT_WINDOW = 300.0
_LOGIN_RATE_LIMIT_MAX = 5


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _prune_attempts(store: dict[str, list[float]], key: str, window: float) -> list[float]:
    now = time()
    store[key] = [item for item in store[key] if now - item < window]
    return store[key]


def _clear_register_rate_limits() -> None:
    _register_attempts.clear()
    _login_failures.clear()


def _check_register_rate_limit(request: Request) -> None:
    ip = _client_ip(request)
    attempts = _prune_attempts(_register_attempts, ip, _REGISTER_RATE_LIMIT_WINDOW)
    if len(attempts) >= _REGISTER_RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="注册请求过于频繁，请稍后再试。",
        )
    attempts.append(time())


def _login_rate_limit_keys(request: Request, username: str) -> list[str]:
    ip = _client_ip(request)
    normalized = username.strip().lower()
    return [f"ip:{ip}", f"user:{normalized}", f"pair:{ip}:{normalized}"]


def _check_login_rate_limit(request: Request, username: str) -> None:
    for key in _login_rate_limit_keys(request, username):
        attempts = _prune_attempts(_login_failures, key, _LOGIN_RATE_LIMIT_WINDOW)
        if len(attempts) >= _LOGIN_RATE_LIMIT_MAX:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="登录请求过于频繁，请稍后再试。",
            )


def _record_login_failure(request: Request, username: str) -> None:
    now = time()
    for key in _login_rate_limit_keys(request, username):
        _prune_attempts(_login_failures, key, _LOGIN_RATE_LIMIT_WINDOW).append(now)


def _clear_login_failures(request: Request, username: str) -> None:
    for key in _login_rate_limit_keys(request, username):
        _login_failures.pop(key, None)


@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> LoginResponse:
    _check_register_rate_limit(request)
    try:
        return service.register(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    _check_login_rate_limit(request, payload.username)
    try:
        response = service.login(db, payload)
    except ValueError as exc:
        _record_login_failure(request, payload.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    _clear_login_failures(request, payload.username)
    return response


@router.get("/me", response_model=AuthUserResponse)
def read_current_user(current_user: User = Depends(get_current_user)) -> AuthUserResponse:
    return service.to_user_response(current_user)
