"""Validate the versioned Agent research task suite without contacting a model or network."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SUITE_PATH = ROOT / "backend" / "tests" / "eval" / "agent_research_tasks.json"


def validate_suite(path: Path = SUITE_PATH) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("任务集必须包含非空 tasks 数组。")
    ids: set[str] = set()
    allowed_classes = {
        "agent_native_read_only",
        "agent_native_research",
        "agent_native_confirmed_preview",
        "agent_native_read_only_or_preview",
        "agent_native_research_with_handoff",
        "research_page_handoff",
        "safety_regression",
    }
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("每个任务必须是对象。")
        task_id = str(task.get("id") or "")
        if not task_id or task_id in ids:
            raise ValueError(f"任务 ID 缺失或重复：{task_id!r}")
        ids.add(task_id)
        if not str(task.get("prompt") or "").strip():
            raise ValueError(f"{task_id} 缺少 prompt。")
        if task.get("class") not in allowed_classes:
            raise ValueError(f"{task_id} 的 class 无效。")
        if not isinstance(task.get("required_tools"), list) or not task["required_tools"]:
            raise ValueError(f"{task_id} 必须声明 required_tools。")
        if task["class"] in {"research_page_handoff", "agent_native_research_with_handoff"} and not task.get("handoff"):
            raise ValueError(f"{task_id} 是 handoff 任务但没有 handoff 说明。")
        if task["class"] == "safety_regression" and not task.get("must_reject"):
            raise ValueError(f"{task_id} 必须声明 must_reject。")
    return {
        "suite_id": payload.get("suite_id"),
        "version": payload.get("version"),
        "task_count": len(tasks),
        "classes": sorted({str(task["class"]) for task in tasks}),
        "task_ids": sorted(ids),
    }


def main() -> int:
    try:
        summary = validate_suite()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Agent task suite invalid: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
