from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_app_settings  # noqa: E402
from app.db.database import get_session_factory, init_database  # noqa: E402
from app.db.models import KnowledgeBase, User  # noqa: E402
from app.services.eval_runner import EvalRunner  # noqa: E402


def _get_user_id(db, username: str | None) -> int | None:
    if not username:
        return None
    user = db.query(User).filter(User.username == username).one_or_none()
    return user.id if user is not None else None


def _get_knowledge_base_id(db, kb_name: str, owner_username: str | None) -> int:
    query = db.query(KnowledgeBase).filter(KnowledgeBase.name == kb_name)
    owner_user_id = _get_user_id(db, owner_username)
    if owner_user_id is not None:
        query = query.filter(KnowledgeBase.owner_user_id == owner_user_id)
    kb = query.order_by(KnowledgeBase.id.desc()).first()
    if kb is None:
        owner_hint = f" for owner {owner_username}" if owner_username else ""
        raise ValueError(f"knowledge base not found: {kb_name}{owner_hint}")
    return kb.id


def _write_report(payload: dict[str, Any], output: Path | None) -> Path:
    settings = get_app_settings()
    if output is None:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        output = settings.logs_dir / f"rag_eval_{timestamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def run_evaluation(
    *,
    kb_name: str,
    owner_username: str | None = "admin",
    categories: list[str] | None = None,
    top_k: int = 6,
    output: Path | None = None,
) -> dict[str, Any]:
    init_database()
    session_factory = get_session_factory()
    db = session_factory()
    try:
        kb_id = _get_knowledge_base_id(db, kb_name, owner_username)
        runner = EvalRunner(db, kb_id)
        reports = runner.run_all(categories, top_k=top_k)
        summary = runner.summary(reports)
        payload = {
            "knowledge_base_id": kb_id,
            "knowledge_base_name": kb_name,
            "categories": categories,
            "top_k": top_k,
            "summary": summary,
            "reports": [asdict(report) for report in reports],
        }
        report_path = _write_report(payload, output)
        payload["report_path"] = report_path.as_posix()
        return payload
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run IDL-RAGAS-lite evaluation against a knowledge base.")
    parser.add_argument("--kb-name", default="IDL Manual")
    parser.add_argument("--owner", default="admin")
    parser.add_argument("--category", action="append", dest="categories")
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    payload = run_evaluation(
        kb_name=args.kb_name,
        owner_username=args.owner,
        categories=args.categories,
        top_k=args.top_k,
        output=args.output,
    )
    print(json.dumps({"summary": payload["summary"], "report_path": payload["report_path"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
