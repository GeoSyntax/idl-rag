from __future__ import annotations

import asyncio
import hashlib
import math
import re
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_app_settings
from app.services.settings_service import get_runtime_settings


_EMBEDDING_STATUS = {
    "last_embedding_ok": False,
    "last_embedding_fallback": False,
    "last_embedding_error": None,
    "last_embedding_checked_at": None,
}


def get_embedding_status() -> dict:
    return dict(_EMBEDDING_STATUS)


class EmbeddingService:
    def __init__(self) -> None:
        self.app_settings = get_app_settings()
        self.is_fallback = False

    def embed_texts(self, db: Session, texts: str | list[str]) -> tuple[list[list[float]], bool]:
        """返回 (embeddings, is_fallback)。"""
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return [], False

        settings = get_runtime_settings(db)
        fallback_error = "未配置模型 API Key。"
        if settings.api_key:
            try:
                with httpx.Client(timeout=60.0) as client:
                    response = client.post(
                        f"{settings.api_base_url.rstrip('/')}/embeddings",
                        headers={"Authorization": f"Bearer {settings.api_key}"},
                        json={
                            "model": settings.embedding_model,
                            "input": texts,
                        },
                    )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data", [])
                embeddings = [item.get("embedding") for item in data]
                if len(embeddings) == len(texts) and all(isinstance(item, list) for item in embeddings):
                    self.is_fallback = False
                    self._record_status(ok=True, fallback=False, error=None)
                    return [self._normalize_dimensions(item) for item in embeddings], False
                fallback_error = "Embedding 服务返回格式不正确。"
            except Exception as exc:  # noqa: BLE001
                fallback_error = str(exc)[:500]

        self.is_fallback = True
        self._record_status(ok=False, fallback=True, error=fallback_error)
        return [self._fallback_embedding(text) for text in texts], True

    def embed_query(self, db: Session, text: str) -> list[float]:
        embeddings, _ = self.embed_texts(db, text)
        return embeddings[0] if embeddings else self._fallback_embedding(text)

    def get_dimensions(self) -> int:
        return self.app_settings.embedding_dimensions

    def get_index_signature(self, db: Session) -> str:
        settings = get_runtime_settings(db)
        model = re.sub(r"[^a-z0-9]+", "_", settings.embedding_model.lower()).strip("_") or "default"
        return f"{model}_{self.get_dimensions()}"

    def get_table_name(self, db: Session) -> str:
        return f"chunks_{self.get_index_signature(db)}"

    def _normalize_dimensions(self, values: list[float]) -> list[float]:
        target = self.get_dimensions()
        normalized = [float(value) for value in values[:target]]
        if len(normalized) < target:
            normalized.extend([0.0] * (target - len(normalized)))
        norm = math.sqrt(sum(value * value for value in normalized)) or 1.0
        return [value / norm for value in normalized]

    def _fallback_embedding(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        while len(values) < self.get_dimensions():
            for byte in digest:
                values.append((byte / 127.5) - 1)
                if len(values) >= self.get_dimensions():
                    break
            digest = hashlib.sha256(digest).digest()
        return self._normalize_dimensions(values)

    def _record_status(self, *, ok: bool, fallback: bool, error: str | None) -> None:
        _EMBEDDING_STATUS["last_embedding_ok"] = ok
        _EMBEDDING_STATUS["last_embedding_fallback"] = fallback
        _EMBEDDING_STATUS["last_embedding_error"] = error
        _EMBEDDING_STATUS["last_embedding_checked_at"] = datetime.utcnow().isoformat()

    async def embed_texts_async(
        self,
        db: Session,
        texts: list[str],
        batch_size: int = 50,
    ) -> tuple[list[list[float]], bool]:
        """异步 embedding — 分批调用，批次间加 100ms 延迟避免限流。"""
        if not texts:
            return [], False
        settings = get_runtime_settings(db)
        if not settings.api_key:
            self.is_fallback = True
            self._record_status(ok=False, fallback=True, error="未配置模型 API Key。")
            return [self._fallback_embedding(text) for text in texts], True

        all_embeddings: list[list[float]] = []
        fallback_error = None
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                for i in range(0, len(texts), batch_size):
                    batch = texts[i : i + batch_size]
                    response = await client.post(
                        f"{settings.api_base_url.rstrip('/')}/embeddings",
                        headers={"Authorization": f"Bearer {settings.api_key}"},
                        json={"model": settings.embedding_model, "input": batch},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    data = payload.get("data", [])
                    batch_embeddings = [item.get("embedding") for item in data]
                    if len(batch_embeddings) != len(batch) or not all(isinstance(e, list) for e in batch_embeddings):
                        self.is_fallback = True
                        self._record_status(ok=False, fallback=True, error="Embedding 服务返回格式不正确。")
                        return [self._fallback_embedding(text) for text in texts], True
                    all_embeddings.extend(self._normalize_dimensions(e) for e in batch_embeddings)
                    if i + batch_size < len(texts):
                        await asyncio.sleep(0.1)
            self.is_fallback = False
            self._record_status(ok=True, fallback=False, error=None)
            return all_embeddings, False
        except Exception as exc:  # noqa: BLE001
            fallback_error = str(exc)[:500]
            self.is_fallback = True
            self._record_status(ok=False, fallback=True, error=fallback_error)
            return [self._fallback_embedding(text) for text in texts], True
