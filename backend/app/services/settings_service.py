from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.schemas import SystemSettingsPayload, SystemSettingsResponse, TestConnectionResponse
from app.core.config import get_app_settings
from app.core.security import decrypt_secret_with_rotation, encrypt_secret
from app.db.models import Document, SystemSetting

logger = logging.getLogger(__name__)

_SENSITIVE_KEYS = {"api_key", "embedding_api_key", "rerank_api_key", "langsmith_api_key"}

_SETTING_KEYS = [
    "provider_name",
    "api_base_url",
    "api_key",
    "chat_model",
    "embedding_api_base_url",
    "embedding_api_key",
    "embedding_model",
    "system_prompt",
    "temperature",
    "rerank_api_url",
    "rerank_api_key",
    "rerank_model",
    "langsmith_enabled",
    "langsmith_api_key",
    "langsmith_project",
    "langsmith_dataset",
    "langsmith_endpoint",
]


def get_runtime_settings(db: Session) -> SystemSettingsPayload:
    defaults = get_app_settings()
    values: dict[str, Any] = {
        "provider_name": defaults.default_provider_name,
        "api_base_url": defaults.default_api_base_url,
        "api_key": "",
        "chat_model": defaults.default_chat_model,
        "embedding_api_base_url": "",
        "embedding_api_key": "",
        "embedding_model": defaults.default_embedding_model,
        "system_prompt": defaults.default_system_prompt,
        "temperature": 0.2,
        "rerank_api_url": "",
        "rerank_api_key": "",
        "rerank_model": "",
        "langsmith_enabled": False,
        "langsmith_api_key": "",
        "langsmith_project": "IDL-RAG",
        "langsmith_dataset": "idl-rag-golden-qa",
        "langsmith_endpoint": "https://api.smith.langchain.com",
    }

    stored = db.query(SystemSetting).all()
    for item in stored:
        if item.key == "temperature" and item.value is not None:
            values[item.key] = float(item.value)
        elif item.key == "langsmith_enabled" and item.value is not None:
            values[item.key] = item.value.lower() == "true"
        elif item.key in values:
            raw = item.value or values[item.key]
            if item.key in _SENSITIVE_KEYS and raw:
                raw = _decrypt_setting(raw, db, item.key)
            values[item.key] = raw
    return SystemSettingsPayload(**values)


def _decrypt_setting(value: str, db: Session | None = None, setting_key: str | None = None) -> str:
    """解密设置值，支持密钥轮转。旧密钥解密成功时惰性重新加密。"""
    settings = get_app_settings()
    try:
        plaintext, used_old_key = decrypt_secret_with_rotation(
            value, settings.auth_secret, settings.previous_auth_secrets_list,
        )
        if used_old_key and db is not None and setting_key is not None:
            # 惰性迁移：用新密钥重新加密并更新 DB
            re_encrypted = encrypt_secret(plaintext, settings.auth_secret)
            db.query(SystemSetting).filter(SystemSetting.key == setting_key).update(
                {"value": re_encrypted}
            )
            db.commit()
            logger.info("已用新密钥重新加密设置项 '%s'。", setting_key)
        return plaintext
    except ValueError:
        # 解密失败说明是旧的明文数据
        return value


def _encrypt_setting(value: str) -> str:
    """加密设置值。"""
    secret = get_app_settings().auth_secret
    return encrypt_secret(value, secret)


def get_current_settings(db: Session) -> SystemSettingsResponse:
    runtime_settings = get_runtime_settings(db)
    data = runtime_settings.model_dump()
    data["has_api_key"] = bool(data.get("api_key"))
    data["has_embedding_api_key"] = bool(data.get("embedding_api_key"))
    data["has_rerank_api_key"] = bool(data.get("rerank_api_key"))
    data["has_langsmith_api_key"] = bool(data.get("langsmith_api_key"))
    for key in _SENSITIVE_KEYS:
        data[key] = ""
    return SystemSettingsResponse(**data)


def save_settings(db: Session, payload: SystemSettingsPayload) -> SystemSettingsResponse:
    previous_settings = get_runtime_settings(db)
    current = {item.key: item for item in db.query(SystemSetting).all()}
    data = payload.model_dump()
    for key in _SETTING_KEYS:
        value = data[key]
        if key in _SENSITIVE_KEYS and not value and key in current:
            continue
        if key in _SENSITIVE_KEYS and value:
            value = _encrypt_setting(value)
        if key in current:
            current[key].value = str(value)
        else:
            db.add(SystemSetting(key=key, value=str(value)))

    if (
        previous_settings.embedding_model != payload.embedding_model
        or previous_settings.embedding_api_base_url != payload.embedding_api_base_url
    ):
        db.execute(
            update(Document)
            .where(Document.status == "ready")
            .values(status="stale")
        )
    db.commit()
    return get_current_settings(db)


def test_connection(db: Session, payload: SystemSettingsPayload) -> TestConnectionResponse:
    runtime_payload = payload.model_copy()
    if not runtime_payload.api_key:
        runtime_payload.api_key = get_runtime_settings(db).api_key
    if not runtime_payload.api_key:
        return TestConnectionResponse(ok=False, message="请先填写 API Key。")

    base_url = runtime_payload.api_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {runtime_payload.api_key}"}
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{base_url}/models", headers=headers)
        if response.is_success:
            return TestConnectionResponse(ok=True, message="模型服务连接成功。")
        return TestConnectionResponse(
            ok=False,
            message=f"连接失败：HTTP {response.status_code} {response.text[:120]}",
        )
    except Exception as exc:  # noqa: BLE001
        return TestConnectionResponse(ok=False, message=f"连接失败：{exc}")


def test_embedding_connection(db: Session, payload: SystemSettingsPayload) -> TestConnectionResponse:
    """用最小输入验证独立 embedding 服务，而不是只探测聊天 /models。"""
    runtime = payload.model_copy()
    current = get_runtime_settings(db)
    base_url = (runtime.embedding_api_base_url or current.embedding_api_base_url or runtime.api_base_url or current.api_base_url).rstrip("/")
    api_key = runtime.embedding_api_key or current.embedding_api_key or runtime.api_key or current.api_key
    if not api_key:
        return TestConnectionResponse(ok=False, message="请先填写 Embedding API Key，或复用聊天 API Key。")
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                f"{base_url}/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": runtime.embedding_model or current.embedding_model, "input": ["embedding health check"]},
            )
        if not response.is_success:
            return TestConnectionResponse(
                ok=False,
                message=f"Embedding 连接失败：HTTP {response.status_code} {response.text[:160]}",
            )
        payload_data = response.json().get("data", [])
        vector = payload_data[0].get("embedding") if payload_data else None
        if not isinstance(vector, list) or not vector:
            return TestConnectionResponse(ok=False, message="Embedding 服务返回格式不正确。")
        return TestConnectionResponse(ok=True, message=f"Embedding 连接成功，返回 {len(vector)} 维向量。")
    except Exception as exc:  # noqa: BLE001
        return TestConnectionResponse(ok=False, message=f"Embedding 连接失败：{exc}")


def test_langsmith_connection(db: Session, payload: SystemSettingsPayload) -> TestConnectionResponse:
    runtime_payload = payload.model_copy()
    if not runtime_payload.langsmith_api_key:
        runtime_payload.langsmith_api_key = get_runtime_settings(db).langsmith_api_key
    if not runtime_payload.langsmith_api_key:
        return TestConnectionResponse(ok=False, message="请先填写 LangSmith API Key。")

    try:
        from langsmith import Client

        client = Client(
            api_key=runtime_payload.langsmith_api_key,
            api_url=runtime_payload.langsmith_endpoint,
        )
        next(client.list_projects(), None)
        return TestConnectionResponse(ok=True, message="LangSmith 连接成功。")
    except Exception as exc:  # noqa: BLE001
        return TestConnectionResponse(ok=False, message=f"LangSmith 连接失败：{exc}")
