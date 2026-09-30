from __future__ import annotations

import mimetypes
import re
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.api.schemas import IdlRunResponse
from app.core.config import get_app_settings
from app.db.models import ChatMessage
from app.services.agent_service import AgentService
from app.services.idl_runtime import validate_project_pro_executable

_ENTRYPOINT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROCEDURE_RE = re.compile(r"^\s*pro\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.IGNORECASE | re.MULTILINE)
_PRO_SUFFIX = ".pro"


class IdlExecutionService:
    def __init__(self) -> None:
        self.agent_service = AgentService()

    def run_artifact(
        self,
        db: Session,
        session_id: int,
        artifact_id: str,
        owner_user_id: int,
        entrypoint: str | None = None,
        timeout_seconds: int | None = None,
        input_artifact_ids: list[str] | None = None,
    ) -> IdlRunResponse:
        settings = get_app_settings()
        unsupported_reason = validate_project_pro_executable(settings.idl_executable)
        if unsupported_reason:
            raise ValueError(unsupported_reason)
        source_artifact = self.agent_service.get_artifact_metadata(db, session_id, artifact_id, owner_user_id)
        source_path, file_name, _media_type = self.agent_service.get_artifact_file(
            db, session_id, artifact_id, owner_user_id
        )
        if source_path.suffix.lower() != _PRO_SUFFIX and not file_name.lower().endswith(_PRO_SUFFIX):
            raise ValueError("只能运行 .pro 文件。")

        code = source_path.read_text(encoding="utf-8", errors="ignore")
        resolved_entrypoint = self._resolve_entrypoint(code, entrypoint)
        run_id = uuid4().hex
        run_dir = settings.chat_artifacts_dir / f"user-{owner_user_id}" / f"session-{session_id}" / "runs" / run_id
        outputs_dir = run_dir / "outputs"
        inputs_dir = run_dir / "inputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        inputs_dir.mkdir(parents=True, exist_ok=True)
        self._stage_input_artifacts(
            db,
            session_id,
            owner_user_id,
            inputs_dir,
            list(source_artifact.get("input_artifact_ids") or []) + list(input_artifact_ids or []),
        )
        run_source = run_dir / "source.pro"
        runner_path = run_dir / "__idlrag_runner.pro"
        shutil.copy2(source_path, run_source)
        runner_path.write_text(self._build_runner(run_source, outputs_dir, resolved_entrypoint), encoding="utf-8")

        timeout = min(timeout_seconds or settings.idl_run_timeout_seconds, settings.idl_run_timeout_seconds)
        started = time.perf_counter()
        exit_code: int | None = None
        stdout = ""
        stderr = ""
        timed_out = False

        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        try:
            with stdout_path.open("w", encoding="utf-8", errors="ignore") as stdout_file, stderr_path.open(
                "w", encoding="utf-8", errors="ignore"
            ) as stderr_file:
                completed = subprocess.run(
                    [settings.idl_executable, "-batch", str(runner_path)],
                    cwd=run_dir,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=timeout,
                    shell=False,
                )
            exit_code = completed.returncode
        except FileNotFoundError as exc:
            raise ValueError(f"IDL 可执行文件不可用：{settings.idl_executable}") from exc
        except subprocess.TimeoutExpired:
            timed_out = True
            stderr = f"IDL 执行超过 {timeout} 秒后已停止。"
        duration_ms = int((time.perf_counter() - started) * 1000)

        stdout = self._truncate(self._read_output_file(stdout_path) or stdout, settings.idl_run_max_stdout_chars)
        stderr = self._truncate(self._clean_workbench_log(self._read_output_file(stderr_path) or stderr), settings.idl_run_max_stderr_chars)
        artifacts_json = self._collect_output_artifacts(outputs_dir, run_id, session_id)
        content = self._build_message_content(exit_code, timed_out, duration_ms, stdout, stderr, artifacts_json)
        message = ChatMessage(
            session_id=session_id,
            role="assistant",
            content=content,
            citations_json=[],
            artifacts_json=artifacts_json,
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        response_message = self.agent_service.to_message_response(message)
        return IdlRunResponse(
            run_id=run_id,
            session_id=session_id,
            message=response_message,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            duration_ms=duration_ms,
            artifacts=response_message.artifacts,
        )

    def _resolve_entrypoint(self, code: str, entrypoint: str | None) -> str:
        if entrypoint:
            value = entrypoint.strip()
            if not _ENTRYPOINT_RE.fullmatch(value):
                raise ValueError("IDL 入口过程名称不合法。")
            return value
        match = _PROCEDURE_RE.search(code)
        if not match:
            raise ValueError("未找到可自动运行的 IDL procedure。")
        return match.group(1)

    def _build_runner(self, run_source: Path, outputs_dir: Path, entrypoint: str) -> str:
        source_literal = run_source.name.replace("'", "''")
        outputs_literal = outputs_dir.as_posix().replace("'", "''")
        return "\n".join(
            [
                f"output_dir = '{outputs_literal}'",
                "FILE_MKDIR, output_dir",
                "CD, output_dir",
                f".compile '../{source_literal}'",
                entrypoint,
                "exit",
                "",
            ]
        )

    def _stage_input_artifacts(
        self,
        db: Session,
        session_id: int,
        owner_user_id: int,
        inputs_dir: Path,
        artifact_ids: list[str],
    ) -> None:
        settings = get_app_settings()
        allowed_suffixes = {
            item.strip().lower()
            for item in settings.idl_run_allowed_input_suffixes.split(",")
            if item.strip()
        }
        max_size = settings.idl_run_max_input_file_mb * 1024 * 1024
        seen: set[str] = set()
        staged_count = 0
        for artifact_id in artifact_ids:
            value = str(artifact_id or "").strip()
            if not value or value in seen:
                continue
            if staged_count >= settings.idl_run_max_input_files:
                raise ValueError("IDL 输入文件数量超过限制。")
            source_path, file_name, _media_type = self.agent_service.get_artifact_file(
                db, session_id, value, owner_user_id
            )
            resolved = source_path.resolve()
            sandbox = settings.chat_artifacts_dir.resolve()
            if source_path.is_symlink() or not resolved.is_relative_to(sandbox):
                raise ValueError("IDL 输入附件路径不合法。")
            if resolved.suffix.lower() not in allowed_suffixes:
                raise ValueError(f"IDL 输入文件格式不支持：{resolved.suffix}")
            if resolved.stat().st_size > max_size:
                raise ValueError("IDL 输入文件超过大小限制。")
            target_name = self._safe_input_file_name(file_name)
            target_path = inputs_dir / target_name
            if target_path.exists():
                target_path = inputs_dir / f"{value[:8]}_{target_name}"
            shutil.copy2(resolved, target_path)
            seen.add(value)
            staged_count += 1

    def _safe_input_file_name(self, file_name: str) -> str:
        name = Path(file_name).name.strip()
        normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
        return normalized[:120] or "input.dat"

    def _collect_output_artifacts(self, run_dir: Path, run_id: str, session_id: int) -> list[dict[str, str | int | bool]]:
        settings = get_app_settings()
        allowed_suffixes = {item.strip().lower() for item in settings.idl_run_allowed_output_suffixes.split(",") if item.strip()}
        max_size = settings.idl_run_max_output_file_mb * 1024 * 1024
        artifacts: list[dict[str, str | int | bool]] = []
        for path in sorted(run_dir.iterdir()):
            if len(artifacts) >= settings.idl_run_max_output_files:
                break
            if path.name.startswith(".") or path.suffix.lower() not in allowed_suffixes:
                continue
            resolved = path.resolve()
            if path.is_symlink() or not path.is_file() or not resolved.is_relative_to(run_dir.resolve()):
                continue
            size = path.stat().st_size
            if size > max_size:
                continue
            artifact_id = uuid4().hex
            media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            artifacts.append(
                {
                    "id": artifact_id,
                    "file_name": path.name,
                    "media_type": media_type,
                    "size": size,
                    "storage_path": resolved.as_posix(),
                    "kind": "idl_output",
                    "previewable": media_type.startswith("image/"),
                    "run_id": run_id,
                }
            )
        return artifacts

    def _build_message_content(
        self,
        exit_code: int | None,
        timed_out: bool,
        duration_ms: int,
        stdout: str,
        stderr: str,
        artifacts: list[dict[str, str | int | bool]],
    ) -> str:
        status = "超时" if timed_out else ("成功" if exit_code == 0 else "失败")
        lines = [
            f"IDL 运行{status}。",
            f"exit_code: {exit_code if exit_code is not None else 'timeout'}",
            f"duration_ms: {duration_ms}",
            f"output_files: {len(artifacts)}",
        ]
        if stdout.strip():
            lines.extend(["", "stdout:", "```text", stdout.strip(), "```"])
        if stderr.strip():
            lines.extend(["", "stderr:", "```text", stderr.strip(), "```"])
        return "\n".join(lines)

    def _truncate(self, value: str, max_chars: int) -> str:
        if len(value) <= max_chars:
            return value
        return value[:max_chars] + "\n...[truncated]"

    def _clean_workbench_log(self, value: str) -> str:
        noisy_prefixes = (
            "Warning: NLS unused message:",
            "Warning. Use the ",
            "log4j:WARN ",
            "Job found still running after platform shutdown.",
        )
        cleaned: list[str] = []
        for line in value.splitlines():
            stripped = line.strip()
            if line.startswith(noisy_prefixes):
                continue
            if "java.lang.NullPointerException" in line:
                continue
            if stripped.startswith("at com.rsi.idldt.") or stripped.startswith("at java.base/"):
                continue
            cleaned.append(line)
        return "\n".join(cleaned).strip()

    def _read_output_file(self, path: Path) -> str:
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
