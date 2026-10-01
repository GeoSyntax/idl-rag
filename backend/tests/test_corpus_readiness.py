from __future__ import annotations

from datetime import datetime, timedelta
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


def test_readiness_blocks_when_documents_are_newer_than_last_evaluation(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.db.database import get_session_factory
    from app.db.models import Document, EvaluationReport, KnowledgeBase
    from app.services.corpus_readiness_service import build_corpus_readiness

    db = get_session_factory()()
    try:
        kb = KnowledgeBase(name="Freshness KB", owner_user_id=1)
        db.add(kb)
        db.flush()
        now = datetime.utcnow()
        db.add(
            Document(
                knowledge_base_id=kb.id,
                file_name="new.md",
                file_path="new.md",
                media_type="text/markdown",
                sha256="new-doc",
                status="ready",
                embedding_model="bge-m3",
                embedding_dimensions=1024,
                last_indexed_at=now,
            )
        )
        db.add(
            EvaluationReport(
                knowledge_base_id=kb.id,
                created_by_user_id=1,
                report_type="local",
                status="completed",
                summary_json={},
                report_json={},
                created_at=now - timedelta(minutes=5),
            )
        )
        db.commit()

        payload = build_corpus_readiness(db, 1)
        item = payload["knowledge_bases"][0]
        assert payload["production_ready"] is False
        assert item["evaluation_completed"] is False
        assert item["evaluation_stale"] is True
        assert any("重新评测" in blocker for blocker in item["blockers"])
    finally:
        db.close()
