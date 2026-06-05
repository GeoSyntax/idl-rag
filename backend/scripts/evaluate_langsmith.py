from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.database import get_session_factory, init_database  # noqa: E402
from app.services.langsmith_eval_service import (  # noqa: E402
    DEFAULT_STRATEGIES,
    LangSmithConfigError,
    LangSmithEvalService,
    get_knowledge_base_id,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LangSmith evaluation for multiple IDL-RAG strategies.")
    parser.add_argument("--kb-name", default="IDL Manual")
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--strategies", nargs="+", default=DEFAULT_STRATEGIES)
    parser.add_argument("--top-k", type=int, default=6)
    args = parser.parse_args()

    init_database()
    db = get_session_factory()()
    try:
        kb_id = get_knowledge_base_id(db, args.kb_name)
        service = LangSmithEvalService(db)
        result = service.evaluate_strategies(
            knowledge_base_id=kb_id,
            strategies=args.strategies,
            dataset_name=args.dataset,
            top_k=args.top_k,
        )
    except (LangSmithConfigError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        db.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
