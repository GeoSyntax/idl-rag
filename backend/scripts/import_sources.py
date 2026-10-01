from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Callable, TypeVar

from sqlalchemy.exc import OperationalError

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.schemas import KnowledgeBaseCreate, RegisterRequest  # noqa: E402
from app.core.config import get_app_settings  # noqa: E402
from app.db.database import get_session_factory, init_database  # noqa: E402
from app.db.models import IndexJob, KnowledgeBase, User  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from app.services.ingest_service import IngestService  # noqa: E402
from app.services.knowledge_base_service import KnowledgeBaseService  # noqa: E402


SUPPORTED_IMPORT_SUFFIXES = {".md", ".markdown", ".txt", ".pro", ".idl", ".pdf"}
T = TypeVar("T")


def _is_database_locked(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


def _retry_database_lock(operation: Callable[[], T], timeout_seconds: float) -> T:
    """Retry transient SQLite contention while the API worker is indexing."""
    deadline = time.monotonic() + timeout_seconds
    delay = 0.2
    while True:
        try:
            return operation()
        except OperationalError as exc:
            if not _is_database_locked(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 2.0)


def _get_or_create_user(db, username: str, password: str) -> User:
    user = db.query(User).filter(User.username == username).one_or_none()
    if user is not None:
        return user
    auth = AuthService()
    login_response = auth.register(db, RegisterRequest(username=username, password=password))
    return db.get(User, login_response.user.id)


def _get_or_create_knowledge_base(db, name: str, description: str, owner_user_id: int) -> KnowledgeBase:
    kb = (
        db.query(KnowledgeBase)
        .filter(KnowledgeBase.owner_user_id == owner_user_id, KnowledgeBase.name == name)
        .one_or_none()
    )
    if kb is not None:
        return kb
    service = KnowledgeBaseService()
    response = service.create_knowledge_base(
        db,
        KnowledgeBaseCreate(name=name, description=description),
        owner_user_id=owner_user_id,
    )
    created = db.get(KnowledgeBase, response.id)
    if created is None:
        raise RuntimeError("failed to create knowledge base")
    return created


def _wait_for_indexing(db, timeout_seconds: float) -> None:
    service = IngestService()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            processed = service.process_next_job(db)
        except OperationalError as exc:
            if not _is_database_locked(exc):
                raise
            time.sleep(0.5)
            continue
        if not processed:
            pending = db.query(IndexJob).filter(IndexJob.status.in_(["queued", "processing"])).count()
            if pending == 0:
                return
            time.sleep(0.2)
    raise TimeoutError("indexing did not finish before timeout")


def import_sources(
    *,
    kb_name: str,
    owner_username: str,
    owner_password: str,
    source_dir: Path | None = None,
    wait: bool = True,
    timeout_seconds: float = 120.0,
) -> dict[str, int | str]:
    init_database()
    settings = get_app_settings()
    source_dir = source_dir or settings.source_dir
    if not source_dir.exists():
        raise FileNotFoundError(f"source directory does not exist: {source_dir}")

    session_factory = get_session_factory()
    db = session_factory()
    try:
        user = _get_or_create_user(db, owner_username, owner_password)
        kb = _get_or_create_knowledge_base(
            db,
            kb_name,
            "ENVI/IDL reference materials and evaluation corpus.",
            user.id,
        )
        files = sorted(
            file for file in source_dir.rglob("*")
            if file.is_file() and file.suffix.lower() in SUPPORTED_IMPORT_SUFFIXES
        )
        result = _retry_database_lock(
            lambda: IngestService().import_path(
                db,
                knowledge_base_id=kb.id,
                path=source_dir.as_posix(),
                recursive=True,
                owner_user_id=user.id,
            ),
            timeout_seconds,
        )
        if wait:
            _wait_for_indexing(db, timeout_seconds)
        return {
            "knowledge_base_id": kb.id,
            "knowledge_base_name": kb.name,
            "source_files": len(files),
            "imported": len(result.imported),
            "skipped": len(result.skipped),
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import data/sources documents into a knowledge base.")
    parser.add_argument("--kb-name", default="IDL Manual")
    parser.add_argument("--owner", default="admin")
    parser.add_argument("--owner-password", default=os.environ.get("IDLRAG_IMPORT_OWNER_PASSWORD", ""))
    parser.add_argument("--source-dir", type=Path, default=None)
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    if not args.owner_password:
        raise ValueError("Provide --owner-password or IDLRAG_IMPORT_OWNER_PASSWORD when the owner may need to be created.")

    summary = import_sources(
        kb_name=args.kb_name,
        owner_username=args.owner,
        owner_password=args.owner_password,
        source_dir=args.source_dir,
        wait=not args.no_wait,
        timeout_seconds=args.timeout,
    )
    print(summary)


if __name__ == "__main__":
    main()
