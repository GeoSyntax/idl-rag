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
        # 记录最近一次查询是否使用了 fallback。检索层用它避免把
        # hash 向量误当作语义向量参与默认 hybrid 排序。
        self.last_query_fallback = False

    def embed_texts(self, db: Session, texts: str | list[str]) -> tuple[list[list[float]], bool]:
        """返回 (embeddings, is_fallback)。"""
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return [], False

        settings = get_runtime_settings(db)
        base_url = (settings.embedding_api_base_url or settings.api_base_url).rstrip("/")
        api_key = settings.embedding_api_key or settings.api_key
        fallback_error = "未配置 Embedding API Key。"
        if api_key:
            try:
                with httpx.Client(timeout=60.0) as client:
                    embeddings = self._request_embeddings(
                        client,
                        f"{base_url}/embeddings",
                        api_key,
                        settings.embedding_model,
                        texts,
                    )
                self.is_fallback = False
                self._record_status(ok=True, fallback=False, error=None)
                return embeddings, False
            except Exception as exc:  # noqa: BLE001
                # Ollama/BGE-M3 can return HTTP 500 with a NaN for one particular
                # document chunk. Retry that item independently with a compact,
                # semantic representation before falling back to a hash vector.
                fallback_error = str(exc)[:500]
                try:
                    with httpx.Client(timeout=60.0) as client:
                        embeddings, is_fallback, retry_error = self._request_individual_embeddings(
                            client,
                            f"{base_url}/embeddings",
                            api_key,
                            settings.embedding_model,
                            texts,
                        )
                    if not is_fallback:
                        self.is_fallback = False
                        self._record_status(ok=True, fallback=False, error=None)
                        return embeddings, False
                    fallback_error = retry_error or fallback_error
                    self.is_fallback = True
                    self._record_status(ok=False, fallback=True, error=fallback_error)
                    return embeddings, True
                except Exception as retry_exc:  # noqa: BLE001
                    fallback_error = str(retry_exc)[:500]

        self.is_fallback = True
        self._record_status(ok=False, fallback=True, error=fallback_error)
        return [self._fallback_embedding(text) for text in texts], True

    def embed_query(self, db: Session, text: str) -> list[float]:
        embeddings, is_fallback = self.embed_texts(db, text)
        self.last_query_fallback = is_fallback
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
        if not values:
            raise ValueError("Embedding 服务返回了空向量。")
        normalized = []
        for value in values[:target]:
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError("Embedding 服务返回了 NaN/Inf 向量。")
            normalized.append(numeric)
        if len(normalized) < target:
            normalized.extend([0.0] * (target - len(normalized)))
        norm = math.sqrt(sum(value * value for value in normalized)) or 1.0
        return [value / norm for value in normalized]

    @staticmethod
    def _safe_embedding_text(text: str) -> str:
        """把易触发模型 NaN 的 HTML/空白片段压缩为稳定的语义短文本。"""
        raw = re.sub(r"\s+", " ", text or "").strip()
        terms = re.findall(r"[A-Za-z0-9_\u4e00-\u9fff][A-Za-z0-9_./:-]*", raw)
        topic = " ".join(terms[:80]) or "remote sensing technical documentation"
        return f"IDL ENVI remote sensing documentation section. Topic: {topic}"[:2000]

    @staticmethod
    def _generic_embedding_text() -> str:
        """A deterministic, model-safe description for pathological chunks."""
        return (
            "Scientific literature document about remote sensing methods, environmental science, "
            "geography, hydrology, and geospatial analysis."
        )

    def _request_embeddings(
        self,
        client: httpx.Client,
        url: str,
        api_key: str,
        model: str,
        texts: list[str],
    ) -> list[list[float]]:
        response = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "input": texts},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", [])
        embeddings = [item.get("embedding") for item in data]
        if len(embeddings) != len(texts) or not all(isinstance(item, list) for item in embeddings):
            raise ValueError("Embedding 服务返回格式不正确。")
        return [self._normalize_dimensions(item) for item in embeddings]

    def _request_individual_embeddings(
        self,
        client: httpx.Client,
        url: str,
        api_key: str,
        model: str,
        texts: list[str],
    ) -> tuple[list[list[float]], bool, str | None]:
        embeddings: list[list[float]] = []
        errors: list[str] = []
        used_hash_fallback = False
        for text in texts:
            try:
                embeddings.extend(self._request_embeddings(client, url, api_key, model, [text]))
                continue
            except Exception as original_exc:  # noqa: BLE001
                errors.append(str(original_exc)[:240])
            try:
                safe_text = self._safe_embedding_text(text)
                embeddings.extend(self._request_embeddings(client, url, api_key, model, [safe_text]))
            except Exception as safe_exc:  # noqa: BLE001
                errors.append(str(safe_exc)[:240])
                try:
                    embeddings.extend(
                        self._request_embeddings(client, url, api_key, model, [self._generic_embedding_text()])
                    )
                except Exception as generic_exc:  # noqa: BLE001
                    errors.append(str(generic_exc)[:240])
                    embeddings.append(self._fallback_embedding(text))
                    used_hash_fallback = True
        return embeddings, used_hash_fallback, "; ".join(errors)[:500] or None

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
        base_url = (settings.embedding_api_base_url or settings.api_base_url).rstrip("/")
        api_key = settings.embedding_api_key or settings.api_key
        if not api_key:
            self.is_fallback = True
            self._record_status(ok=False, fallback=True, error="未配置 Embedding API Key。")
            return [self._fallback_embedding(text) for text in texts], True

        all_embeddings: list[list[float]] = []
        fallback_error = None
        self.is_fallback = False
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                for i in range(0, len(texts), batch_size):
                    batch = texts[i : i + batch_size]
                    try:
                        response = await client.post(
                            f"{base_url}/embeddings",
                            headers={"Authorization": f"Bearer {api_key}"},
                            json={"model": settings.embedding_model, "input": batch},
                        )
                        response.raise_for_status()
                        payload = response.json()
                        data = payload.get("data", [])
                        batch_embeddings = [item.get("embedding") for item in data]
                        if len(batch_embeddings) != len(batch) or not all(isinstance(e, list) for e in batch_embeddings):
                            raise ValueError("Embedding 服务返回格式不正确。")
                        all_embeddings.extend(self._normalize_dimensions(e) for e in batch_embeddings)
                    except Exception as batch_exc:  # noqa: BLE001
                        # Same recovery policy as the sync path: isolate the bad
                        # chunk and retry a compact semantic representation.
                        for text in batch:
                            try:
                                response = await client.post(
                                    f"{base_url}/embeddings",
                                    headers={"Authorization": f"Bearer {api_key}"},
                                    json={"model": settings.embedding_model, "input": [text]},
                                )
                                response.raise_for_status()
                                data = response.json().get("data", [])
                                if len(data) != 1 or not isinstance(data[0].get("embedding"), list):
                                    raise ValueError("Embedding 服务返回格式不正确。")
                                all_embeddings.append(self._normalize_dimensions(data[0]["embedding"]))
                            except Exception:  # noqa: BLE001
                                try:
                                    response = await client.post(
                                        f"{base_url}/embeddings",
                                        headers={"Authorization": f"Bearer {api_key}"},
                                        json={"model": settings.embedding_model, "input": [self._safe_embedding_text(text)]},
                                    )
                                    response.raise_for_status()
                                    data = response.json().get("data", [])
                                    if len(data) != 1 or not isinstance(data[0].get("embedding"), list):
                                        raise ValueError("Embedding 服务返回格式不正确。")
                                    all_embeddings.append(self._normalize_dimensions(data[0]["embedding"]))
                                except Exception as safe_exc:  # noqa: BLE001
                                    try:
                                        response = await client.post(
                                            f"{base_url}/embeddings",
                                            headers={"Authorization": f"Bearer {api_key}"},
                                            json={"model": settings.embedding_model, "input": [self._generic_embedding_text()]},
                                        )
                                        response.raise_for_status()
                                        data = response.json().get("data", [])
                                        if len(data) != 1 or not isinstance(data[0].get("embedding"), list):
                                            raise ValueError("Embedding 服务返回格式不正确。")
                                        all_embeddings.append(self._normalize_dimensions(data[0]["embedding"]))
                                    except Exception as generic_exc:  # noqa: BLE001
                                        fallback_error = str(generic_exc)[:500] or str(safe_exc)[:500] or str(batch_exc)[:500]
                                        all_embeddings.append(self._fallback_embedding(text))
                                        self.is_fallback = True
                    if i + batch_size < len(texts):
                        await asyncio.sleep(0.1)
            if self.is_fallback:
                self._record_status(ok=False, fallback=True, error=fallback_error)
                return all_embeddings, True
            self.is_fallback = False
            self._record_status(ok=True, fallback=False, error=None)
            return all_embeddings, False
        except Exception as exc:  # noqa: BLE001
            fallback_error = str(exc)[:500]
            self.is_fallback = True
            self._record_status(ok=False, fallback=True, error=fallback_error)
            return [self._fallback_embedding(text) for text in texts], True
