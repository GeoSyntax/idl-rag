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
