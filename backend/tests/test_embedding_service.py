from __future__ import annotations

import math

import pytest

from app.services.embedding_service import EmbeddingService


class _FakeResponse:
    def __init__(self, vector: list[float] | None = None, status_code: int = 200) -> None:
        self.vector = vector
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        if self.vector is None:
            return {"error": {"message": "NaN"}}
        return {"data": [{"embedding": self.vector}]}


class _InputSensitiveClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def post(self, url: str, *, headers: dict, json: dict) -> _FakeResponse:  # noqa: ARG002
        text = json["input"][0]
        self.calls.append(text)
        if text.startswith("IDL ENVI remote sensing documentation section."):
            return _FakeResponse([0.1, 0.2, 0.3, 0.4])
        return _FakeResponse(None, status_code=500)


def test_pathological_chunk_retries_with_safe_local_embedding() -> None:
    service = EmbeddingService()
    service.app_settings.embedding_dimensions = 4
    client = _InputSensitiveClient()

    vectors, is_fallback, error = service._request_individual_embeddings(
        client, "http://embedding.test/v1/embeddings", "ollama", "bge-m3", ["<empty markup chunk>"]
    )

    assert not is_fallback
    assert error
    assert len(vectors) == 1
    assert len(vectors[0]) == 4
    assert len(client.calls) == 2
    assert client.calls[1].startswith("IDL ENVI remote sensing documentation section.")


def test_normalize_dimensions_rejects_non_finite_values() -> None:
    service = EmbeddingService()
    with pytest.raises(ValueError, match="NaN/Inf"):
        service._normalize_dimensions([math.nan])
