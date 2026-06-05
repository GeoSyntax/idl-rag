from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.database import get_session_factory, init_database  # noqa: E402
from app.services.langsmith_eval_service import LangSmithConfigError, LangSmithEvalService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync IDL-RAG Golden QA cases to a LangSmith dataset.")
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    init_database()
    db = get_session_factory()()
    try:
        service = LangSmithEvalService(db)
        result = service.sync_dataset(dataset_name=args.dataset, dry_run=args.dry_run)
    except LangSmithConfigError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        db.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
