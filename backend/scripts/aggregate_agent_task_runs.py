"""Aggregate the newest per-task Agent traces into one evaluation report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate local Agent task-suite traces.")
    parser.add_argument("--suite", type=Path, default=BACKEND_DIR / "tests" / "eval" / "agent_research_tasks.json")
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(BACKEND_DIR))
    from scripts.run_agent_task_suite import _evaluate_task

    tasks = json.loads(args.suite.resolve().read_text(encoding="utf-8"))["tasks"]
    latest: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in args.trace_dir.resolve().glob("*_AR-*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        task_id = payload.get("task_id")
        if not isinstance(task_id, str):
            continue
        previous = latest.get(task_id)
        if previous is None or path.stat().st_mtime > previous[0].stat().st_mtime:
            latest[task_id] = (path, payload)

    results: list[dict[str, Any]] = []
    missing: list[str] = []
    for task in tasks:
        item = latest.get(str(task["id"]))
        if item is None:
            missing.append(str(task["id"]))
            continue
        path, payload = item
        payload["evaluation"] = _evaluate_task(task, payload)
        payload["trace_path"] = str(path)
        results.append(payload)

    report = {
        "suite": str(args.suite.resolve()),
        "trace_dir": str(args.trace_dir.resolve()),
        "task_count": len(tasks),
        "trace_count": len(results),
        "missing_tasks": missing,
        "passed_contract": sum(item["evaluation"]["status"] == "passed" for item in results),
        "needs_review": sum(item["evaluation"]["status"] != "passed" for item in results),
        "failed_with_exception": sum(bool(item.get("error")) for item in results),
        "results": results,
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("task_count", "trace_count", "missing_tasks", "passed_contract", "needs_review", "failed_with_exception")}, ensure_ascii=False, indent=2))
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
