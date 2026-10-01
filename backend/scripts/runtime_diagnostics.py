"""Check external runtime dependencies without exposing secrets.

This diagnostic is intentionally separate from liveness/readiness.  ``/api/ready``
only proves that the API, database and worker can serve requests; this command
checks the model services and optional GEE/IDL integrations that a real
research run depends on.
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_app_settings  # noqa: E402
from app.db.database import get_session_factory  # noqa: E402
from app.services.corpus_readiness_service import build_corpus_readiness  # noqa: E402
from app.services.idl_runtime import validate_project_pro_executable  # noqa: E402
from app.services.settings_service import get_runtime_settings  # noqa: E402


def _safe_host(url: str) -> str | None:
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


def _check_embedding(base_url: str, api_key: str, model: str, expected_dimensions: int) -> dict[str, Any]:
    if not base_url or not model:
        return {"status": "fail", "message": "embedding endpoint or model is missing"}
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                f"{base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                json={"model": model, "input": ["runtime diagnostics embedding check"]},
            )
        response.raise_for_status()
        data = response.json().get("data", [])
        vector = data[0].get("embedding") if data else None
        if not isinstance(vector, list) or not vector or not all(math.isfinite(float(value)) for value in vector):
            return {"status": "fail", "message": "embedding response is empty or non-finite"}
        if len(vector) != expected_dimensions:
            return {
                "status": "fail",
                "message": f"expected {expected_dimensions} dimensions, got {len(vector)}",
                "dimensions": len(vector),
            }
        return {"status": "ok", "host": _safe_host(base_url), "model": model, "dimensions": len(vector)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "fail", "host": _safe_host(base_url), "message": str(exc)[:300]}


def _check_chat(base_url: str, api_key: str, model: str) -> dict[str, Any]:
    if not base_url or not model:
        return {"status": "fail", "message": "chat endpoint or model is missing"}
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            )
        response.raise_for_status()
        return {"status": "ok", "host": _safe_host(base_url), "model": model}
    except Exception as exc:  # noqa: BLE001
        return {"status": "fail", "host": _safe_host(base_url), "message": str(exc)[:300]}


def _check_idl(executable: str, required: bool, probe: bool = False) -> dict[str, Any]:
    if not executable:
        return {"status": "fail" if required else "warn", "message": "IDL executable is not configured"}
    reason = validate_project_pro_executable(executable)
    resolved = shutil.which(executable) or (executable if Path(executable).exists() else None)
    if reason or not resolved:
        return {
            "status": "fail" if required else "warn",
            "message": reason or f"executable not found: {executable}",
        }
    if not probe:
        return {
            "status": "fail" if required else "warn",
            "executable": str(resolved),
            "message": "IDL binary exists; runtime/license probe was not run",
        }
    try:
        completed = subprocess.run(
            [str(resolved), "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "fail" if required else "warn",
            "executable": str(resolved),
            "message": "IDL process did not finish its startup probe within 5 seconds; license/runtime is not verified",
            "stdout": (exc.stdout or "")[-300:] if isinstance(exc.stdout, str) else "",
        }
    output = f"{completed.stdout}\n{completed.stderr}".strip()
    if completed.returncode != 0:
        return {
            "status": "fail" if required else "warn",
            "executable": str(resolved),
            "message": f"IDL startup probe exited with code {completed.returncode}",
            "output": output[-300:],
        }
    return {"status": "ok", "executable": str(resolved), "runtime_probe": output[-300:]}


def _check_gee(enabled: bool, auth_mode: str, project: str, has_credentials: bool, required: bool) -> dict[str, Any]:
    if not enabled:
        return {"status": "fail" if required else "warn", "message": "GEE is disabled"}
    if not project:
        return {"status": "fail", "message": "GEE project is missing"}
    if auth_mode != "adc" and not has_credentials:
        return {"status": "fail", "message": "GEE service-account credentials are missing"}
    return {"status": "ok", "auth_mode": auth_mode, "project": project}


def diagnose(
    *,
    owner_user_id: int,
    require_gee: bool = False,
    require_idl: bool = False,
    probe_idl: bool = False,
) -> dict[str, Any]:
    app_settings = get_app_settings()
    session_factory = get_session_factory()
    db = session_factory()
    try:
        runtime = get_runtime_settings(db)
        corpus = build_corpus_readiness(db, owner_user_id)
    finally:
        db.close()

    gee_credentials = bool(app_settings.gee_service_account_email and app_settings.gee_service_account_key_json)
    checks = {
        "corpus": {
            "status": "ok" if corpus.get("production_ready") else "fail",
            "production_ready": corpus.get("production_ready"),
            "blockers": corpus.get("blockers", []),
        },
        "embedding": _check_embedding(
            runtime.embedding_api_base_url or runtime.api_base_url,
            runtime.embedding_api_key or runtime.api_key,
            runtime.embedding_model,
            app_settings.embedding_dimensions,
        ),
        "chat": _check_chat(runtime.api_base_url, runtime.api_key, runtime.chat_model),
        "gee": _check_gee(
            app_settings.gee_enabled,
            app_settings.gee_auth_mode,
            app_settings.gee_project,
            gee_credentials or app_settings.gee_auth_mode == "adc",
            require_gee,
        ),
        "idl": _check_idl(app_settings.idl_executable, require_idl, probe=probe_idl),
    }
    required_failures = [name for name, value in checks.items() if value.get("status") == "fail"]
    return {
        "status": "fail" if required_failures else "pass",
        "required_failures": required_failures,
        "checks": checks,
        "embedding_dimensions": app_settings.embedding_dimensions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", type=int, default=1)
    parser.add_argument("--require-gee", action="store_true")
    parser.add_argument("--require-idl", action="store_true")
    parser.add_argument("--probe-idl", action="store_true", help="launch IDL with -version and enforce a 5-second timeout")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = diagnose(
        owner_user_id=args.owner_id,
        require_gee=args.require_gee,
        require_idl=args.require_idl,
        probe_idl=args.probe_idl,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json_dumps(result), encoding="utf-8")
    print(json_dumps({"status": result["status"], "required_failures": result["required_failures"], "checks": result["checks"]}))
    if result["status"] == "fail":
        raise SystemExit(1)


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"


if __name__ == "__main__":
    main()
