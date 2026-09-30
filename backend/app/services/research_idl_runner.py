from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_app_settings
from app.db.models import (
    FormulaSpec,
    ResearchDataAsset,
    ResearchDataSnapshot,
    ResearchExperiment,
)
from app.services.research_asset_storage import ResearchAssetStorage
from app.services.idl_runtime import validate_project_pro_executable


_ENTRYPOINT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROCEDURE_RE = re.compile(r"^\s*pro\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.IGNORECASE | re.MULTILINE)


class IdlRunnerUnavailableError(RuntimeError):
    """The optional local IDL/ENVI runtime is not installed or configured."""


@dataclass(frozen=True)
class IdlRunResult:
    manifest: dict[str, Any]
    outputs: list[dict[str, Any]]


class ResearchIdlRunner:
    """Run an explicitly approved project ``.pro`` source in a bounded sandbox.

    This runner deliberately does not accept a command line, executable path,
    shell fragment, or arbitrary local path from experiment parameters.  The
    only executable is the configured ``IDLRAG_IDL_EXECUTABLE`` and the only
    source is a project-private asset uploaded through the dedicated endpoint.
    Snapshot assets are copied into an isolated ``inputs`` directory and made
    available to the script through ``IDLRAG_INPUT_DIR``.  Results are written
    to the run root through ``IDLRAG_OUTPUT_DIR`` so the existing protected
    research-output API and evidence package can serve them.
    """

    _SCRIPT_ROLE = "idl_script"

    def __init__(self) -> None:
        self.asset_storage = ResearchAssetStorage()

    def execute(
        self,
        *,
        db: Session,
        project_id: int,
        experiment: ResearchExperiment,
        formula_spec: FormulaSpec,
        snapshot: ResearchDataSnapshot,
        assets: list[ResearchDataAsset],
        output_dir: Path,
        run_token: str,
    ) -> IdlRunResult:
        settings = get_app_settings()
        script_asset = self._get_script_asset(db, project_id, experiment.parameters_json or {})
        script_path = self.asset_storage.resolve_asset_uri(script_asset.source_uri)
        self._verify_asset_hash(script_asset, script_path)
        source = script_path.read_text(encoding="utf-8", errors="strict")
        entrypoint = self._resolve_entrypoint(source, experiment.parameters_json or {})
        prediction_output_file = self._prediction_output_file(experiment.parameters_json or {})

        executable = str(settings.idl_executable or "").strip()
        if not executable:
            raise IdlRunnerUnavailableError(
                "未配置 IDLRAG_IDL_EXECUTABLE；IDLRunner 不可用，但不会影响 PythonRunner。"
            )
        unsupported_reason = validate_project_pro_executable(executable)
        if unsupported_reason:
            raise IdlRunnerUnavailableError(unsupported_reason)

        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=False)
        inputs_dir = output_dir / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=False)
        staged_inputs = self._stage_inputs(assets, inputs_dir, settings)

        source_copy = output_dir / "idl_source.pro"
        wrapper_path = output_dir / "__idl_runner.pro"
        source_copy.write_text(source, encoding="utf-8")
        wrapper_path.write_text(self._build_wrapper(entrypoint), encoding="utf-8")

        input_manifest = output_dir / "idl_inputs.json"
        input_manifest.write_text(
            json.dumps(
                {
                    "snapshot_id": snapshot.id,
                    "snapshot_hash": snapshot.snapshot_hash,
                    "formula_spec_id": formula_spec.id,
                    "assets": staged_inputs,
                    "notice": "Paths are relative to this protected run directory; raw pixels are not copied into evidence packages.",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        stdout_path = output_dir / "idl_stdout.log"
        stderr_path = output_dir / "idl_stderr.log"
        env = dict(os.environ)
        env.update(
            {
                "IDLRAG_INPUT_DIR": inputs_dir.as_posix(),
                "IDLRAG_OUTPUT_DIR": output_dir.as_posix(),
                "IDLRAG_RUN_TOKEN": run_token,
                "IDLRAG_INPUT_MANIFEST": input_manifest.as_posix(),
            }
        )

        started = time.perf_counter()
        try:
            with stdout_path.open("w", encoding="utf-8", errors="ignore") as stdout_file, stderr_path.open(
                "w", encoding="utf-8", errors="ignore"
            ) as stderr_file:
                completed = subprocess.run(
                    [executable, "-batch", str(wrapper_path)],
                    cwd=output_dir,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=min(settings.idl_run_timeout_seconds, max(1, settings.idl_run_timeout_seconds)),
                    shell=False,
                    env=env,
                )
        except FileNotFoundError as exc:
            raise IdlRunnerUnavailableError(
                f"IDL 可执行文件不可用：{executable}；请配置受许可的 IDL/ENVI 节点。"
                "这不会影响 PythonRunner。"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"IDL 执行超过 {settings.idl_run_timeout_seconds} 秒后已停止。") from exc

        duration_ms = int((time.perf_counter() - started) * 1000)
        stdout = self._read_limited(stdout_path, settings.idl_run_max_stdout_chars)
        stderr = self._read_limited(stderr_path, settings.idl_run_max_stderr_chars)
        if completed.returncode != 0:
            detail = stderr.strip() or stdout.strip() or f"exit_code={completed.returncode}"
            if self._looks_like_runtime_unavailable(detail):
                raise IdlRunnerUnavailableError(
                    "IDL 运行时未能初始化或取得许可；请配置可用的命令行 IDL/ENVI 节点。"
                    f"现场输出：{detail[:1200]}"
                )
            raise ValueError(f"IDL 脚本执行失败：{detail[:1800]}")

        outputs = self._collect_outputs(output_dir, run_token, settings)
        validation_plan = experiment.validation_plan_json or {}
        requires_prediction_output = "idl_prediction_output_file" in (experiment.parameters_json or {}) or any(
            validation_plan.get(key) is not None for key in ("reference_asset_id", "sample_validation")
        )
        if requires_prediction_output and not any(
            output.get("file_name") == prediction_output_file for output in outputs
        ):
            raise ValueError(
                f"IDL 脚本未在受限输出目录生成声明的预测 GeoTIFF：{prediction_output_file}。"
            )
        outputs.extend(
            [
                self._output_descriptor(stdout_path, "idl_log", run_token, {"stream": "stdout"}),
                self._output_descriptor(stderr_path, "idl_log", run_token, {"stream": "stderr"}),
                self._output_descriptor(input_manifest, "idl_input_manifest", run_token, {}),
            ]
        )
        manifest = {
            "schema_version": 1,
            "runner": "idl",
            "status": "completed",
            "run_token": run_token,
            "duration_ms": duration_ms,
            "exit_code": completed.returncode,
            "entrypoint": entrypoint,
            "script_asset_id": script_asset.id,
            "script_sha256": script_asset.sha256,
            "snapshot_id": snapshot.id,
            "snapshot_hash": snapshot.snapshot_hash,
            "formula_spec_id": formula_spec.id,
            "prediction_output_file": prediction_output_file,
            "inputs": staged_inputs,
            "stdout_truncated": len(stdout) >= settings.idl_run_max_stdout_chars,
            "stderr_truncated": len(stderr) >= settings.idl_run_max_stderr_chars,
            "raw_data_included": False,
        }
        manifest_path = output_dir / "run_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs.append(self._output_descriptor(manifest_path, "run_manifest", run_token, {"runner": "idl"}))
        manifest["outputs"] = outputs
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return IdlRunResult(manifest=manifest, outputs=outputs)

    def _get_script_asset(
        self, db: Session, project_id: int, parameters: dict[str, Any]
    ) -> ResearchDataAsset:
        raw_id = parameters.get("idl_script_asset_id")
        if raw_id is None:
            raise IdlRunnerUnavailableError(
                "未声明 parameters.idl_script_asset_id；请先上传项目 .pro 脚本。"
                "这不会影响 PythonRunner。"
            )
        if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id < 1:
            raise ValueError("IDL 实验必须在 parameters.idl_script_asset_id 中声明项目脚本资产。")
        asset = (
            db.query(ResearchDataAsset)
            .filter(ResearchDataAsset.id == raw_id, ResearchDataAsset.project_id == project_id)
            .first()
        )
        if asset is None:
            raise ValueError("IDL 脚本资产不存在或不属于当前项目。")
        if (asset.metadata_json or {}).get("asset_role") != self._SCRIPT_ROLE:
            raise ValueError("IDL 实验只能引用通过项目 IDL 脚本上传接口登记的资产。")
        if asset.source_type != "local" or not asset.source_uri.startswith("research://assets/"):
            raise ValueError("IDL 脚本必须是当前项目的私有本地资产。")
        return asset

    @staticmethod
    def _verify_asset_hash(asset: ResearchDataAsset, path: Path) -> None:
        if asset.sha256:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest.lower() != asset.sha256.lower():
                raise ValueError("IDL 脚本资产校验值与磁盘内容不一致。")
        if path.suffix.lower() != ".pro":
            raise ValueError("IDL 脚本资产必须是 .pro 文件。")

    @staticmethod
    def _resolve_entrypoint(source: str, parameters: dict[str, Any]) -> str:
        entrypoint = parameters.get("idl_entrypoint")
        if entrypoint is None:
            match = _PROCEDURE_RE.search(source)
            if not match:
                raise ValueError("IDL 脚本中未找到可运行的 procedure，请声明 idl_entrypoint。")
            return match.group(1)
        value = str(entrypoint).strip()
        if not _ENTRYPOINT_RE.fullmatch(value):
            raise ValueError("parameters.idl_entrypoint 不是合法的 IDL 过程名称。")
        return value

    @staticmethod
    def _prediction_output_file(parameters: dict[str, Any]) -> str:
        value = str(parameters.get("idl_prediction_output_file") or "water_mask.tif").strip()
        if Path(value).name != value or Path(value).suffix.lower() not in {".tif", ".tiff"}:
            raise ValueError("parameters.idl_prediction_output_file 必须是当前运行中的 GeoTIFF 文件名。")
        return value

    @staticmethod
    def _build_wrapper(entrypoint: str) -> str:
        return "\n".join(
            [
                "compile_opt idl2",
                "output_dir = getenv('IDLRAG_OUTPUT_DIR')",
                "input_dir = getenv('IDLRAG_INPUT_DIR')",
                "input_manifest = getenv('IDLRAG_INPUT_MANIFEST')",
                "if output_dir eq '' then message, 'IDLRAG_OUTPUT_DIR is missing'",
                "if input_dir eq '' then message, 'IDLRAG_INPUT_DIR is missing'",
                "cd, output_dir",
                ".compile 'idl_source.pro'",
                entrypoint,
                "exit",
                "",
            ]
        )

    def _stage_inputs(self, assets: list[ResearchDataAsset], inputs_dir: Path, settings) -> list[dict[str, Any]]:
        allowed_suffixes = {
            item.strip().lower() for item in settings.idl_run_allowed_input_suffixes.split(",") if item.strip()
        }
        max_size = settings.idl_run_max_input_file_mb * 1024 * 1024
        staged: list[dict[str, Any]] = []
        for asset in assets:
            path = self.asset_storage.resolve_asset_uri(asset.source_uri)
            if path.suffix.lower() not in allowed_suffixes:
                raise ValueError(f"IDL 快照输入格式不支持：{path.suffix}")
            if path.stat().st_size > max_size:
                raise ValueError("IDL 快照输入文件超过大小限制。")
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.name).strip("._")[:120] or "input.dat"
            target = inputs_dir / f"{asset.id}_{safe_name}"
            shutil.copy2(path, target)
            staged.append(
                {
                    "asset_id": asset.id,
                    "file_name": target.name,
                    "sha256": asset.sha256,
                    "asset_kind": asset.asset_kind,
                }
            )
        if len(staged) > settings.idl_run_max_input_files:
            raise ValueError("IDL 快照输入文件数量超过限制。")
        return staged

    def _collect_outputs(self, output_dir: Path, run_token: str, settings) -> list[dict[str, Any]]:
        allowed_suffixes = {
            item.strip().lower() for item in settings.idl_run_allowed_output_suffixes.split(",") if item.strip()
        }
        max_size = settings.idl_run_max_output_file_mb * 1024 * 1024
        ignored = {"idl_source.pro", "__idl_runner.pro", "idl_stdout.log", "idl_stderr.log", "idl_inputs.json"}
        outputs: list[dict[str, Any]] = []
        for path in sorted(output_dir.iterdir(), key=lambda item: item.name):
            if path.name in ignored or path.name.startswith(".") or path.suffix.lower() not in allowed_suffixes:
                continue
            resolved = path.resolve()
            if path.is_symlink() or not path.is_file() or not resolved.is_relative_to(output_dir):
                continue
            if path.stat().st_size > max_size:
                continue
            outputs.append(self._output_descriptor(path, "idl_output", run_token, {"previewable": self._is_image(path)}))
            if len(outputs) >= settings.idl_run_max_output_files:
                break
        return outputs

    @staticmethod
    def _read_limited(path: Path, max_chars: int) -> str:
        value = path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""
        return value if len(value) <= max_chars else value[:max_chars] + "\n...[truncated]"

    @staticmethod
    def _looks_like_runtime_unavailable(detail: str) -> bool:
        normalized = str(detail or "").lower()
        return any(
            marker in normalized
            for marker in (
                "failed to initialize idl instance",
                "unable to acquire an idl license",
                "license checkout",
                "license not available",
                "许可",
                "授权",
            )
        )

    @staticmethod
    def _is_image(path: Path) -> bool:
        return (mimetypes.guess_type(path.name)[0] or "").startswith("image/")

    @staticmethod
    def _output_descriptor(path: Path, kind: str, run_token: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": kind,
            "file_name": path.name,
            "uri": f"research://runs/{run_token}/{path.name}",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": path.stat().st_size,
            "metadata": metadata,
        }
