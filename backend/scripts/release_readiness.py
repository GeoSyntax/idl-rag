"""Run the release gate for source, index, evaluation and backup state.

This command is deliberately conservative: a release is allowed only when the
source audit passes, every selected knowledge base is current and indexed, and
an independently verified runtime backup is supplied.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from app.db.database import get_session_factory  # noqa: E402
from backup_runtime import verify_backup  # noqa: E402
from app.services.corpus_readiness_service import build_corpus_readiness  # noqa: E402

from audit_corpus import audit  # noqa: E402


def _uncleared_license_count(source_result: dict[str, Any]) -> int:
    """Count OA PDFs that still carry the local-only license review marker."""
    total = 0
    for value in (source_result.get("source_classes") or {}).values():
        if not isinstance(value, dict):
            continue
        open_access = value.get("open_access")
        if isinstance(open_access, dict):
            total += int(open_access.get("license_review_records") or 0)
    return total


def run_release_readiness(
    *,
    source_root: Path,
    backup: Path,
    owner_user_id: int,
    require_cleared_licenses: bool = False,
) -> dict[str, Any]:
    source_result = audit(source_root, verify_hashes=True)
    backup_result = verify_backup(backup) if backup.exists() else {
        "status": "fail",
        "errors": [f"backup does not exist: {backup}"],
    }
    session_factory = get_session_factory()
    db = session_factory()
    try:
        corpus_result = build_corpus_readiness(db, owner_user_id)
    finally:
        db.close()
    errors = list(source_result.get("errors", []))
    uncleared_licenses = _uncleared_license_count(source_result)
    if require_cleared_licenses and uncleared_licenses:
        errors.append(
            f"{uncleared_licenses} open-access PDFs still require redistribution-license review"
        )
    errors.extend(str(item) for item in backup_result.get("errors", []))
    errors.extend(str(item) for item in corpus_result.get("blockers", []))
    return {
        "release_ready": not errors and corpus_result.get("production_ready") is True,
        "errors": errors,
        "source_audit": source_result,
        "corpus_readiness": corpus_result,
        "backup_verification": backup_result,
        "uncleared_license_count": uncleared_licenses,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("data/sources"))
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--owner-id", type=int, default=1)
    parser.add_argument(
        "--require-cleared-licenses",
        action="store_true",
        help="fail when any OA PDF still has a redistribution-license review marker",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_release_readiness(
        source_root=args.source_root,
        backup=args.backup,
        owner_user_id=args.owner_id,
        require_cleared_licenses=args.require_cleared_licenses,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"release_ready": result["release_ready"], "errors": len(result["errors"])}, ensure_ascii=False))
    if not result["release_ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
