from pathlib import Path


def _prepare_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))

    from app.core.config import get_app_settings
    from app.db.database import get_engine, get_index_engine, get_index_session_factory, get_session_factory, init_database

    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_index_engine.cache_clear()
    get_session_factory.cache_clear()
    get_index_session_factory.cache_clear()
    init_database()


def _settings_payload(**overrides):
    from app.api.schemas import SystemSettingsPayload

    values = SystemSettingsPayload().model_dump()
    values.update(overrides)
    return SystemSettingsPayload(**values)


def test_sensitive_settings_are_hidden_encrypted_and_preserved(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.db.database import get_session_factory
    from app.db.models import SystemSetting
    from app.services.settings_service import get_current_settings, get_runtime_settings, save_settings

    db = get_session_factory()()
    try:
        response = save_settings(
            db,
            _settings_payload(
                api_key="model-secret",
                embedding_api_base_url="http://127.0.0.1:9000/v1",
                embedding_api_key="embedding-secret",
                rerank_api_key="rerank-secret",
                langsmith_enabled=True,
                langsmith_api_key="langsmith-secret",
            ),
        )

        assert response.api_key == ""
        assert response.embedding_api_key == ""
        assert response.rerank_api_key == ""
        assert response.langsmith_api_key == ""
        assert response.has_api_key is True
        assert response.has_embedding_api_key is True
        assert response.has_rerank_api_key is True
        assert response.has_langsmith_api_key is True

        runtime = get_runtime_settings(db)
        assert runtime.api_key == "model-secret"
        assert runtime.embedding_api_base_url == "http://127.0.0.1:9000/v1"
        assert runtime.embedding_api_key == "embedding-secret"
        assert runtime.rerank_api_key == "rerank-secret"
        assert runtime.langsmith_api_key == "langsmith-secret"
        assert runtime.langsmith_enabled is True

        stored = {item.key: item.value for item in db.query(SystemSetting).all()}
        assert stored["api_key"] != "model-secret"
        assert stored["embedding_api_key"] != "embedding-secret"
        assert stored["rerank_api_key"] != "rerank-secret"
        assert stored["langsmith_api_key"] != "langsmith-secret"

        save_settings(
            db,
            _settings_payload(
                api_key="",
                embedding_api_key="",
                rerank_api_key="",
                langsmith_api_key="",
                langsmith_project="IDL-RAG-Updated",
            ),
        )

        runtime = get_runtime_settings(db)
        assert runtime.api_key == "model-secret"
        assert runtime.embedding_api_key == "embedding-secret"
        assert runtime.rerank_api_key == "rerank-secret"
        assert runtime.langsmith_api_key == "langsmith-secret"
        assert runtime.langsmith_project == "IDL-RAG-Updated"

        current = get_current_settings(db)
        assert current.api_key == ""
        assert current.embedding_api_key == ""
        assert current.rerank_api_key == ""
        assert current.langsmith_api_key == ""
    finally:
        db.close()


def test_embedding_connection_checks_the_separate_channel(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.db.database import get_session_factory
    from app.services.settings_service import test_embedding_connection

    class FakeResponse:
        is_success = True
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, **kwargs):
            assert url == "http://embedding.local/v1/embeddings"
            assert kwargs["json"]["model"] == "bge-m3"
            assert kwargs["headers"]["Authorization"] == "Bearer embedding-secret"
            return FakeResponse()

    monkeypatch.setattr("app.services.settings_service.httpx.Client", lambda **_kwargs: FakeClient())

    db = get_session_factory()()
    try:
        result = test_embedding_connection(
            db,
            _settings_payload(
                api_base_url="http://chat.local/v1",
                api_key="chat-secret",
                embedding_api_base_url="http://embedding.local/v1",
                embedding_api_key="embedding-secret",
                embedding_model="bge-m3",
            ),
        )
    finally:
        db.close()

    assert result.ok is True
    assert "3 维" in result.message
