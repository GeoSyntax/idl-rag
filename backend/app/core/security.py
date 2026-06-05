from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

PBKDF2_ITERATIONS = 390000


@dataclass(slots=True)
class TokenPayload:
    user_id: int
    username: str
    role: str
    exp: int


def _derive_fernet_key(secret: str) -> bytes:
    """从 auth_secret 派生 Fernet 密钥（32 url-safe base64 字节）。"""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_secret(plaintext: str, secret: str) -> str:
    """用 Fernet 对称加密保护敏感字段（如 API Key）。"""
    from cryptography.fernet import Fernet

    key = _derive_fernet_key(secret)
    f = Fernet(key)
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str, secret: str) -> str:
    """解密 Fernet 加密的字段。"""
    from cryptography.fernet import Fernet

    key = _derive_fernet_key(secret)
    f = Fernet(key)
    return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")


def decrypt_secret_with_rotation(ciphertext: str, current_secret: str, previous_secrets: list[str] | None = None) -> tuple[str, bool]:
    """解密字段，支持多密钥轮转。

    返回 (plaintext, used_old_key)。
    - 先用当前密钥尝试解密
    - 失败则依次尝试旧密钥
    - 全部失败抛出 ValueError
    - used_old_key=True 表示用了旧密钥解密成功，调用方应重新加密
    """
    from cryptography.fernet import Fernet

    # 尝试当前密钥
    try:
        key = _derive_fernet_key(current_secret)
        f = Fernet(key)
        return f.decrypt(ciphertext.encode("ascii")).decode("utf-8"), False
    except Exception:  # noqa: BLE001
        pass

    # 尝试旧密钥
    if previous_secrets:
        for old_secret in previous_secrets:
            if not old_secret.strip():
                continue
            try:
                key = _derive_fernet_key(old_secret.strip())
                f = Fernet(key)
                return f.decrypt(ciphertext.encode("ascii")).decode("utf-8"), True
            except Exception:  # noqa: BLE001
                continue

    raise ValueError("所有密钥均无法解密该数据。")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        expected = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        ).hex()
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, digest_hex)


def create_access_token(*, user_id: int, username: str, role: str, secret: str, expires_minutes: int) -> str:
    expires_at = datetime.now(UTC) + timedelta(minutes=expires_minutes)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "exp": int(expires_at.timestamp()),
    }
    payload_segment = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature_segment = _sign_segment(payload_segment, secret)
    return f"{payload_segment}.{signature_segment}"


def decode_access_token(token: str, secret: str) -> TokenPayload:
    try:
        payload_segment, signature_segment = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("登录状态无效，请重新登录。") from exc

    expected_signature = _sign_segment(payload_segment, secret)
    if not hmac.compare_digest(signature_segment, expected_signature):
        raise ValueError("登录状态无效，请重新登录。")

    try:
        payload = json.loads(_b64url_decode(payload_segment))
        user_id = int(payload["sub"])
        username = str(payload["username"])
        role = str(payload["role"])
        exp = int(payload["exp"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("登录状态无效，请重新登录。") from exc

    if int(datetime.now(UTC).timestamp()) >= exp:
        raise ValueError("登录已过期，请重新登录。")

    return TokenPayload(user_id=user_id, username=username, role=role, exp=exp)


def _sign_segment(payload_segment: str, secret: str) -> str:
    signature = hmac.new(secret.encode("utf-8"), payload_segment.encode("utf-8"), hashlib.sha256).digest()
    return _b64url_encode(signature)


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
