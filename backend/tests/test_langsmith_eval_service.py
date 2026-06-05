from pathlib import Path
from types import SimpleNamespace

import pytest


def _prepare_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))

    from app.core.config import get_app_settings
    from app.db.database import get_engine, get_index_engine, get_index_session_factory, get_session_factory, init_database

    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_index_engine.cache_clear()
    get_session_factory.cache_clear()
    get_index_session_factory.cache_clear()
    init_database()


def _payload(**overrides):
    from app.api.schemas import SystemSettingsPayload

    values = SystemSettingsPayload().model_dump()
    values.update(overrides)
    return SystemSettingsPayload(**values)


class FakeLangSmithClient:
    instances = []

    def __init__(self, *, api_key: str, api_url: str) -> None:
        self.api_key = api_key
        self.api_url = api_url
        self.created_examples = None
        FakeLangSmithClient.instances.append(self)

    def read_dataset(self, *, dataset_name: str):
        raise RuntimeError("missing dataset")

    def create_dataset(self, name: str, description: str, data_type: str):
        self.created_dataset = {"name": name, "description": description, "data_type": data_type}
        return SimpleNamespace(id="dataset-id", name=name)

    def list_examples(self, *, dataset_id: str):
        assert dataset_id == "dataset-id"
        return [SimpleNamespace(metadata={"id": "fts_keyword_01"})]

    def create_examples(self, *, inputs, outputs, metadata, dataset_id: str):
        self.created_examples = {
            "inputs": inputs,
            "outputs": outputs,
            "metadata": metadata,
            "dataset_id": dataset_id,
        }


def test_langsmith_sync_dry_run_does_not_require_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.db.database import get_session_factory
    from app.services.langsmith_eval_service import LangSmithEvalService

    db = get_session_factory()()
    try:
        result = LangSmithEvalService(db).sync_dataset(dry_run=True)
    finally:
        db.close()

    assert result["dataset"] == "idl-rag-golden-qa"
    assert result["total_cases"] == 33
    assert result["dry_run"] is True


def test_langsmith_sync_requires_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.db.database import get_session_factory
    from app.services.langsmith_eval_service import LangSmithConfigError, LangSmithEvalService

    db = get_session_factory()()
    try:
        with pytest.raises(LangSmithConfigError):
            LangSmithEvalService(db).sync_dataset()
    finally:
        db.close()


def test_langsmith_sync_creates_missing_examples(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    FakeLangSmithClient.instances = []

    import langsmith

    monkeypatch.setattr(langsmith, "Client", FakeLangSmithClient)

    from app.db.database import get_session_factory
    from app.services.langsmith_eval_service import LangSmithEvalService
    from app.services.settings_service import save_settings

    db = get_session_factory()()
    try:
        save_settings(
            db,
            _payload(
                langsmith_api_key="ls-test",
                langsmith_endpoint="https://api.smith.langchain.com",
                langsmith_dataset="idl-rag-golden-qa-test",
            ),
        )
        result = LangSmithEvalService(db).sync_dataset()
    finally:
        db.close()

    client = FakeLangSmithClient.instances[0]
    assert client.api_key == "ls-test"
    assert client.created_dataset["name"] == "idl-rag-golden-qa-test"
    assert result["total_cases"] == 33
    assert result["created"] == 32
    assert client.created_examples["dataset_id"] == "dataset-id"
    assert client.created_examples["inputs"][0]["question"]
    assert client.created_examples["outputs"][0]["expected_keywords"]
    assert client.created_examples["metadata"][0]["id"] != "fts_keyword_01"
