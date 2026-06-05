from datetime import datetime, timedelta
from pathlib import Path


def _prepare_state(monkeypatch, tmp_path: Path, **env: str) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    from app.core.config import get_app_settings
    from app.db.database import get_engine, get_index_engine, get_index_session_factory, get_session_factory, init_database

    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_index_engine.cache_clear()
    get_session_factory.cache_clear()
    get_index_session_factory.cache_clear()
    init_database()


def _create_job_fixture(tmp_path: Path):
    from app.db.database import get_session_factory
    from app.db.models import Document, IndexJob, KnowledgeBase, User

    db = get_session_factory()()
    source = tmp_path / "source.md"
    source.write_text("# Source\n\ncontent", encoding="utf-8")
    user = User(username="admin", password_hash="hash", role="admin", is_active=True)
    db.add(user)
    db.flush()
    kb = KnowledgeBase(name="KB", owner_user_id=user.id)
    db.add(kb)
    db.flush()
    document = Document(
        knowledge_base_id=kb.id,
        file_name=source.name,
        file_path=source.as_posix(),
        media_type="text/markdown",
        sha256="abc",
        status="processing",
    )
    db.add(document)
    db.flush()
    job = IndexJob(
        knowledge_base_id=kb.id,
        document_id=document.id,
        job_type="ingest",
        status="processing",
        attempt_count=1,
        payload_json={"file_path": source.as_posix()},
        started_at=datetime.utcnow() - timedelta(minutes=10),
    )
    db.add(job)
    db.commit()
    return db, document.id, job.id


def test_stale_processing_job_is_requeued(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(
        monkeypatch,
        tmp_path,
        IDLRAG_INDEX_JOB_TIMEOUT_MINUTES="1",
        IDLRAG_INDEX_JOB_MAX_ATTEMPTS="3",
    )

    from app.db.models import Document, IndexJob
    from app.services.ingest_service import IngestService

    db, document_id, job_id = _create_job_fixture(tmp_path)
    try:
        recovered = IngestService().recover_stale_processing_jobs(db)
        document = db.get(Document, document_id)
        job = db.get(IndexJob, job_id)

        assert recovered == 1
        assert document.status == "queued"
        assert job.status == "queued"
        assert job.started_at is None
    finally:
        db.close()


def test_stale_processing_job_fails_after_max_attempts(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(
        monkeypatch,
        tmp_path,
        IDLRAG_INDEX_JOB_TIMEOUT_MINUTES="1",
        IDLRAG_INDEX_JOB_MAX_ATTEMPTS="1",
    )

    from app.db.models import Document, IndexJob
    from app.services.ingest_service import IngestService

    db, document_id, job_id = _create_job_fixture(tmp_path)
    try:
        recovered = IngestService().recover_stale_processing_jobs(db)
        document = db.get(Document, document_id)
        job = db.get(IndexJob, job_id)

        assert recovered == 0
        assert document.status == "failed"
        assert job.status == "failed"
    finally:
        db.close()


def test_failed_job_retries_before_max_attempts(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path, IDLRAG_INDEX_JOB_MAX_ATTEMPTS="3")

    from app.db.models import Document, IndexJob
    from app.services.ingest_service import IngestService

    db, document_id, job_id = _create_job_fixture(tmp_path)
    try:
        job = db.get(IndexJob, job_id)
        document = db.get(Document, document_id)
        job.status = "processing"
        job.attempt_count = 1
        document.status = "processing"
        db.commit()

        IngestService()._handle_job_failure(db, job_id, document_id, "boom")
        assert db.get(IndexJob, job_id).status == "queued"
        assert db.get(Document, document_id).status == "queued"
    finally:
        db.close()


def test_failed_job_fails_at_max_attempts(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path, IDLRAG_INDEX_JOB_MAX_ATTEMPTS="2")

    from app.db.models import Document, IndexJob
    from app.services.ingest_service import IngestService

    db, document_id, job_id = _create_job_fixture(tmp_path)
    try:
        job = db.get(IndexJob, job_id)
        document = db.get(Document, document_id)
        job.status = "processing"
        job.attempt_count = 2
        document.status = "processing"
        db.commit()

        IngestService()._handle_job_failure(db, job_id, document_id, "boom")
        assert db.get(IndexJob, job_id).status == "failed"
        assert db.get(Document, document_id).status == "failed"
    finally:
        db.close()
