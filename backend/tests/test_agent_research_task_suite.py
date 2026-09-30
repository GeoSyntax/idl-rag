from __future__ import annotations

import json
from pathlib import Path


def test_agent_research_task_suite_is_versioned_and_complete() -> None:
    from scripts.validate_agent_task_suite import validate_suite
    from app.services.agent_tools import get_openai_tools

    summary = validate_suite(Path(__file__).parent / "eval" / "agent_research_tasks.json")
    assert summary["suite_id"] == "agent-research-poyang-v1"
    assert summary["version"] == 1
    assert summary["task_count"] == 10
    assert "safety_regression" in summary["classes"]
    assert "agent_native_confirmed_preview" in summary["classes"]

    task_payload = json.loads(
        (Path(__file__).parent / "eval" / "agent_research_tasks.json").read_text(encoding="utf-8")
    )
    exposed_tools = {item["function"]["name"] for item in get_openai_tools()}
    for task in task_payload["tasks"]:
        assert set(task["required_tools"]).issubset(exposed_tools), task["id"]


def test_task_contract_marks_agent_sse_error_for_review() -> None:
    from scripts.run_agent_task_suite import _evaluate_task

    task = {
        "id": "AR-ERR",
        "required_tools": ["research_project_context"],
        "must_not_call": [],
    }
    result = {
        "events": [{"type": "error", "message": "Gemini2API unavailable"}],
        "answer": "",
        "error": "Gemini2API unavailable",
    }

    evaluation = _evaluate_task(task, result)

    assert evaluation["status"] == "needs_review"
    assert evaluation["checks"]["answer_nonempty"] is False


def test_task_contract_accepts_explicit_preview_boundary() -> None:
    from scripts.run_agent_task_suite import _evaluate_task

    task = {
        "id": "AR-BOUNDARY",
        "required_tools": ["research_project_context"],
        "must_reject": ["把 preview 写成最终结论"],
    }
    result = {
        "events": [{"step": "tool_call", "tool": "research_project_context"}],
        "answer": "当前 preview 结果仅供探索，尚未完成正式验证，不等于正式结论，不能作为最终科学结论。",
        "error": None,
    }

    evaluation = _evaluate_task(task, result)

    assert evaluation["status"] == "passed"
