from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
import json
import math
import mimetypes
from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.orm import Session

from app.api.schemas import (
    ResearchParameterSweepCreate,
    ResearchRunComparisonResponse,
    ResearchRunReproducibilityResponse,
    ResearchRunResponse,
    ResearchRunVerificationResponse,
)
from app.core.config import get_app_settings
from app.db.models import (
    EvidenceCard,
    FormulaSpec,
    ResearchDataAsset,
    ResearchDataSnapshot,
    ResearchExperiment,
    ResearchProject,
    ResearchProjectMember,
    ResearchProtocolRevision,
    ResearchRun,
    ResearchValidationSample,
)
from app.services.research_evidence_package import ResearchEvidencePackageService
from app.services.research_reproducibility_service import ResearchReproducibilityService
from app.services.research_report_service import ResearchReportService
from app.services.research_run_comparison_service import ResearchRunComparisonService
from app.services.research_idl_runner import IdlRunnerUnavailableError, ResearchIdlRunner
from app.services.research_service import ResearchService
from app.services.python_runner import PythonRunResult, PythonRunner
from app.services.raster_comparison_service import RasterComparisonResult, RasterComparisonService


class ResearchRunService:
    """执行已创建的研究实验，且不提供任意代码执行入口。"""

    def __init__(self) -> None:
        self.python_runner = PythonRunner()
        self.raster_comparison_service = RasterComparisonService()
        self.evidence_package_service = ResearchEvidencePackageService()
        self.reproducibility_service = ResearchReproducibilityService()
        self.report_service = ResearchReportService()
        self.comparison_service = ResearchRunComparisonService()
        self.idl_runner = ResearchIdlRunner()

    def list_runs(
        self, db: Session, project_id: int, experiment_id: int, owner_user_id: int
    ) -> list[ResearchRunResponse]:
        self._get_owned_experiment(db, project_id, experiment_id, owner_user_id)
        runs = (
            db.query(ResearchRun)
            .filter(ResearchRun.project_id == project_id, ResearchRun.experiment_id == experiment_id)
            .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
            .all()
        )
        return [self._response(run) for run in runs]

    def verify_run_integrity(
        self,
        db: Session,
        project_id: int,
        experiment_id: int,
        run_id: int,
        owner_user_id: int,
    ) -> ResearchRunVerificationResponse:
        run = self._get_owned_run(db, project_id, experiment_id, run_id, owner_user_id)
        package_output = next(
            (output for output in run.outputs_json if output.get("kind") == "research_evidence_package"),
            None,
        )
        if not package_output:
            return ResearchRunVerificationResponse(
                run_id=run.id,
                run_token=run.run_token,
                status="not_available",
                verified=False,
                notice="本次运行没有 Research Evidence Package，无法进行证据包校验。",
            )
        output_dir = get_app_settings().research_runs_dir / f"project-{project_id}" / run.run_token
        package_name = str(package_output.get("file_name") or "")
        safe_name = Path(package_name).name
        if not safe_name or safe_name != package_name:
            raise LookupError("研究证据包路径不安全。")
        package_path = (output_dir / safe_name).resolve()
        if not package_path.is_relative_to(output_dir.resolve()):
            raise LookupError("研究证据包路径不安全。")
        result = self.evidence_package_service.verify(
            package_path=package_path,
            package_sha256=package_output.get("sha256"),
            output_descriptors=run.outputs_json,
        )
        return ResearchRunVerificationResponse(run_id=run.id, run_token=run.run_token, **result)

    def compare_reproducibility(
        self,
        db: Session,
        project_id: int,
        experiment_id: int,
        run_id: int,
        owner_user_id: int,
        *,
        reference_run_id: int,
        absolute_tolerance: float,
        relative_tolerance: float,
    ) -> ResearchRunReproducibilityResponse:
        if run_id == reference_run_id:
            raise ValueError("重跑一致性比较不能引用运行自身作为基准。")
        target = self._get_owned_run(db, project_id, experiment_id, run_id, owner_user_id)
        reference = self._get_owned_run(db, project_id, experiment_id, reference_run_id, owner_user_id)
        if target.status != "completed" or reference.status != "completed":
            raise ValueError("重跑一致性比较要求目标运行和基准运行都已 completed。")
        if not isinstance(absolute_tolerance, (int, float)) or isinstance(absolute_tolerance, bool):
            raise ValueError("absolute_tolerance 必须是有限数值。")
        if not isinstance(relative_tolerance, (int, float)) or isinstance(relative_tolerance, bool):
            raise ValueError("relative_tolerance 必须是有限数值。")
        if not math.isfinite(float(absolute_tolerance)) or not math.isfinite(float(relative_tolerance)):
            raise ValueError("重跑比较容差必须是有限数值。")
        target_dir = get_app_settings().research_runs_dir / f"project-{project_id}" / target.run_token
        reference_dir = get_app_settings().research_runs_dir / f"project-{project_id}" / reference.run_token
        result = self.reproducibility_service.compare(
            target_run_id=target.id,
            reference_run_id=reference.id,
            target_manifest=target.manifest_json or {},
            reference_manifest=reference.manifest_json or {},
            target_outputs=target.outputs_json,
            reference_outputs=reference.outputs_json,
            target_dir=target_dir,
            reference_dir=reference_dir,
            absolute_tolerance=float(absolute_tolerance),
            relative_tolerance=float(relative_tolerance),
        )
        return ResearchRunReproducibilityResponse(**result)

    def compare_formal_runs(
        self,
        db: Session,
        project_id: int,
        experiment_id: int,
        run_id: int,
        owner_user_id: int,
        *,
        reference_run_id: int,
    ) -> ResearchRunComparisonResponse:
        if run_id == reference_run_id:
            raise ValueError("基线运行不能是目标运行自身。")
        target = self._get_owned_run(db, project_id, experiment_id, run_id, owner_user_id)
        reference = self._get_owned_run_any_experiment(db, project_id, reference_run_id, owner_user_id)
        if target.status != "completed" or reference.status != "completed":
            raise ValueError("基线与候选比较要求两个运行都已 completed。")
        target_experiment = self._get_owned_experiment(db, project_id, target.experiment_id, owner_user_id)
        reference_experiment = self._get_owned_experiment(db, project_id, reference.experiment_id, owner_user_id)
        result = self.comparison_service.compare(
            run_id=target.id,
            reference_run_id=reference.id,
            run_manifest=target.manifest_json or {},
            reference_manifest=reference.manifest_json or {},
            run_validation_plan=target_experiment.validation_plan_json or {},
            reference_validation_plan=reference_experiment.validation_plan_json or {},
        )
        return ResearchRunComparisonResponse(**result)

    def start_run(
        self,
        db: Session,
        project_id: int,
        experiment_id: int,
        owner_user_id: int,
        *,
        retry_of_run_id: int | None = None,
    ) -> ResearchRunResponse:
        run, experiment, formula_spec, snapshot = self._create_queued_run(
            db,
            project_id,
            experiment_id,
            owner_user_id,
            execution_mode="sync",
            retry_of_run_id=retry_of_run_id,
        )
        return self._execute_claimed_run(
            db, run, experiment, formula_spec, snapshot, owner_user_id
        )

    def start_parameter_sweep(
        self,
        db: Session,
        project_id: int,
        experiment_id: int,
        owner_user_id: int,
        sweep: ResearchParameterSweepCreate,
        *,
        mode: str = "sync",
        _run: ResearchRun | None = None,
        retry_of_run_id: int | None = None,
    ) -> ResearchRunResponse:
        """Evaluate preview candidates without exposing independent-test labels.

        A sweep is persisted as one ordinary ResearchRun so every candidate's
        images and metrics remain downloadable and auditable. It deliberately
        does not create or freeze a FormulaSpec: ranking is an exploration aid,
        not an automatic scientific conclusion.
        """
        if mode not in {"sync", "queue", "execute"}:
            raise ValueError("参数候选实验模式必须是 sync、queue 或 execute。")
        experiment = self._get_owned_experiment(db, project_id, experiment_id, owner_user_id)
        if experiment.execution_mode != "preview":
            raise ValueError("参数候选实验只能运行在 preview 实验上；正式实验不得用扫参结果替代独立测试。")
        if experiment.runner_type != "python":
            raise ValueError("参数候选实验目前只支持 PythonRunner；IDL 需要单独的受许可节点。")
        self._validate_parameter_sweep_request(sweep)
        if "idl_comparison" in (experiment.parameters_json or {}):
            raise ValueError("参数候选实验暂不支持 IDL 对照；请先完成单次 Python/IDL 对照运行。")

        formula_spec = self._get_formula_spec(db, project_id, experiment.formula_spec_id)
        snapshot = self._get_snapshot(db, project_id, experiment.data_snapshot_id)
        spec_parameters = (formula_spec.spec_json or {}).get("parameters", {})
        if not isinstance(spec_parameters, dict):
            spec_parameters = {}
        allowed_parameter_keys = set(spec_parameters.keys())
        allowed_parameter_keys.update((experiment.parameters_json or {}).keys())
        unknown_keys = sorted(
            {
                key
                for candidate in sweep.candidates
                for key in candidate.parameters
                if key not in allowed_parameter_keys
            }
        )
        if unknown_keys:
            raise ValueError(f"参数候选包含公式未声明的参数：{', '.join(unknown_keys[:10])}。")
        self._validate_execution_preconditions(db, experiment, formula_spec, snapshot)
        validation_plan = copy.deepcopy(experiment.validation_plan_json or {})
        point_plan = validation_plan.get("sample_validation")
        if not isinstance(point_plan, dict):
            raise ValueError("参数候选实验必须声明 sample_validation，且只能在开发/模型选择样本上比较。")
        if validation_plan.get("reference_asset_id") is not None:
            raise ValueError("参数候选实验只允许使用点样本开发/模型选择划分，不会读取参考栅格或独立测试标签。")
        point_plan["split"] = sweep.evaluation_split
        validation_plan["sample_validation"] = point_plan

        assets = self._get_snapshot_assets(db, project_id, snapshot)
        validation_samples = self._get_snapshot_validation_samples(db, project_id, snapshot)
        if _run is None:
            run_token = uuid4().hex
            run_control = {
                "execution_mode": "queue" if mode == "queue" else "sync",
                "run_kind": "parameter_sweep",
                "evaluation_split": sweep.evaluation_split,
                "ranking_metric": sweep.ranking_metric,
                "sweep_request": sweep.model_dump(mode="json"),
            }
            if retry_of_run_id is not None:
                run_control["retry_of_run_id"] = retry_of_run_id
            run = ResearchRun(
                project_id=project_id,
                experiment_id=experiment_id,
                runner_type="python",
                run_token=run_token,
                status="queued" if mode == "queue" else "running",
                run_dir=f"research://runs/{run_token}",
                manifest_json=run_control,
                started_at=self._now() if mode != "queue" else None,
            )
            if mode != "queue":
                experiment.status = "running"
            db.add(run)
            db.commit()
            db.refresh(run)
            if mode == "queue":
                return self._response(run)
        else:
            run = _run
            run_token = run.run_token
            run_control = dict(run.manifest_json or {})
            if run.status != "running":
                raise ValueError("参数候选队列运行必须处于 running 状态才能执行。")
        output_dir = get_app_settings().research_runs_dir / f"project-{project_id}" / run_token
        output_dir.mkdir(parents=True, exist_ok=True)

        candidate_records: list[dict[str, object]] = []
        outputs: list[dict[str, object]] = []
        cancelled = False
        try:
            for index, candidate in enumerate(sweep.candidates, start=1):
                self._refresh_run_control(db, run)
                if self._cancel_requested(run):
                    cancelled = True
                    break
                candidate_token = f"{run_token}-c{index:02d}"
                candidate_dir = output_dir / f"candidate-{index:02d}"
                candidate_parameters = copy.deepcopy(experiment.parameters_json or {})
                candidate_parameters.update(copy.deepcopy(candidate.parameters))
                candidate_experiment = SimpleNamespace(
                    id=experiment.id,
                    name=f"{experiment.name} · {candidate.name}",
                    execution_mode="preview",
                    parameters_json=candidate_parameters,
                    validation_plan_json=copy.deepcopy(validation_plan),
                    runner_type="python",
                )
                try:
                    result = self.python_runner.execute(
                        experiment=candidate_experiment,
                        formula_spec=formula_spec,
                        snapshot=snapshot,
                        assets=assets,
                        output_dir=candidate_dir,
                        run_token=candidate_token,
                        validation_samples=validation_samples,
                    )
                    point_validation = ((result.manifest.get("validation") or {}).get("point_samples") or {})
                    if point_validation.get("status") != "completed":
                        raise ValueError("候选没有生成指定开发/模型选择样本的完整验证结果。")
                    metrics = ((point_validation.get("metrics") or {}).get("metrics") or {})
                    score = metrics.get(sweep.ranking_metric)
                    if not isinstance(score, (int, float)):
                        raise ValueError(f"候选缺少可排序的 {sweep.ranking_metric} 指标。")
                    candidate_outputs: list[dict[str, object]] = []
                    for descriptor in result.outputs:
                        if descriptor.get("kind") == "run_manifest":
                            continue
                        source = candidate_dir / str(descriptor["file_name"])
                        target = output_dir / f"candidate-{index:02d}-{source.name}"
                        if not source.is_file() or source.is_symlink():
                            raise ValueError(f"候选产物不存在或路径不安全：{source.name}")
                        shutil.copy2(source, target)
                        copied = dict(descriptor)
                        copied["file_name"] = target.name
                        copied["uri"] = f"research://runs/{run_token}/{target.name}"
                        copied_metadata = dict(copied.get("metadata") or {})
                        copied_metadata.update({"candidate_index": index, "candidate_name": candidate.name})
                        copied["metadata"] = copied_metadata
                        candidate_outputs.append(copied)
                        outputs.append(copied)
                    candidate_records.append(
                        {
                            "index": index,
                            "name": candidate.name,
                            "parameters": candidate_parameters,
                            "status": "completed",
                            "score": float(score),
                            "metrics": metrics,
                            "selection": point_validation.get("selection", {}),
                            "outputs": [item["file_name"] for item in candidate_outputs],
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - preserve per-candidate failure evidence
                    candidate_records.append(
                        {
                            "index": index,
                            "name": candidate.name,
                            "parameters": candidate_parameters,
                            "status": "failed",
                            "error": str(exc)[:1000],
                            "outputs": [],
                        }
                    )

            successful = [item for item in candidate_records if item["status"] == "completed"]
            successful.sort(key=lambda item: (-float(item["score"]), int(item["index"])))
            for rank, item in enumerate(successful, start=1):
                item["rank"] = rank
            failed_count = sum(1 for item in candidate_records if item["status"] == "failed")
            cancelled_candidate_count = len(sweep.candidates) - len(candidate_records)
            self._refresh_run_control(db, run)
            run_control = dict(run.manifest_json or {})
            sweep_manifest: dict[str, object] = {
                "schema_version": 1,
                "kind": "parameter_sweep",
                "evaluation_split": sweep.evaluation_split,
                "ranking_metric": sweep.ranking_metric,
                "candidate_count": len(sweep.candidates),
                "completed_candidate_count": len(successful),
                "failed_candidate_count": failed_count,
                "cancelled_candidate_count": cancelled_candidate_count,
                "candidates": candidate_records,
                "ranking": [
                    {
                        "rank": item["rank"],
                        "index": item["index"],
                        "name": item["name"],
                        "score": item["score"],
                    }
                    for item in successful
                ],
                "selection_notice": (
                    "排序只用于开发/模型选择阶段的候选比较；系统不会自动冻结公式、选择独立测试结果或宣称科学结论。"
                ),
            }
            sweep_path = output_dir / "sweep_manifest.json"
            sweep_path.write_text(json.dumps(sweep_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            outputs.append(self.python_runner._output_descriptor(sweep_path, "parameter_sweep_manifest", run_token, {}))
            manifest = {
                "schema_version": 1,
                "run_token": run_token,
                "run_kind": "parameter_sweep",
                "runner": self.python_runner._runner_manifest(),
                "experiment": {
                    "id": experiment.id,
                    "name": experiment.name,
                    "execution_mode": experiment.execution_mode,
                    "parameters": experiment.parameters_json,
                },
                "formula_spec": {
                    "id": formula_spec.id,
                    "name": formula_spec.name,
                    "version": formula_spec.version,
                    "status": formula_spec.status,
                },
                "data_snapshot": {
                    "id": snapshot.id,
                    "snapshot_hash": snapshot.snapshot_hash,
                    "asset_ids": snapshot.asset_ids_json,
                },
                "sweep": sweep_manifest,
                "outputs": outputs,
                "run_control": run_control,
            }
            manifest_path = output_dir / "run_manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            outputs.append(self.python_runner._output_descriptor(manifest_path, "run_manifest", run_token, {}))
            manifest["outputs"] = outputs
            run.manifest_json = manifest
            run.outputs_json = outputs
            run.status = "cancelled" if cancelled else ("completed" if failed_count == 0 else "failed")
            run.error_message = (
                "已请求取消；已保留取消前生成的候选产物，结果不得作为正式结论使用。"
                if cancelled
                else (None if failed_count == 0 else f"{failed_count} 个候选参数运行失败；请查看 sweep_manifest.json。")
            )
            run.finished_at = self._now()
            experiment.status = run.status
            db.commit()
            db.refresh(run)
            return self._response(run)
        except Exception as exc:
            run.status = "failed"
            run.error_message = str(exc)[:2000]
            run.finished_at = self._now()
            experiment.status = "failed"
            db.commit()
            db.refresh(run)
            return self._response(run)

    @staticmethod
    def _validate_parameter_sweep_request(sweep: ResearchParameterSweepCreate) -> None:
        names: set[str] = set()
        for candidate in sweep.candidates:
            if candidate.name in names:
                raise ValueError("参数候选名称必须唯一。")
            names.add(candidate.name)
            if "idl_comparison" in candidate.parameters:
                raise ValueError("参数候选不得覆盖 idl_comparison；IDL 对照必须单独声明并审计。")
            for key, value in candidate.parameters.items():
                if not isinstance(key, str) or not key.strip() or len(key) > 100 or key.startswith("__"):
                    raise ValueError("参数候选的参数名必须是 1 到 100 个字符，且不能使用保留前缀。")
                if isinstance(value, (dict, list, tuple)):
                    raise ValueError("参数候选目前只接受标量 JSON 值，不接受嵌套对象或数组。")
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError("参数候选不能包含 NaN 或无穷数值。")
        try:
            json.dumps([candidate.parameters for candidate in sweep.candidates], allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("参数候选必须是可复现的 JSON 值。") from exc

    def queue_run(
        self,
        db: Session,
        project_id: int,
        experiment_id: int,
        owner_user_id: int,
        *,
        retry_of_run_id: int | None = None,
    ) -> ResearchRunResponse:
        """Create a queued run for the embedded or standalone research worker."""
        run, _, _, _ = self._create_queued_run(
            db,
            project_id,
            experiment_id,
            owner_user_id,
            execution_mode="queue",
            retry_of_run_id=retry_of_run_id,
        )
        return self._response(run)

    def retry_run(
        self,
        db: Session,
        project_id: int,
        experiment_id: int,
        run_id: int,
        owner_user_id: int,
        *,
        mode: str = "queue",
    ) -> ResearchRunResponse:
        """Create an independent run from a terminal run without mutating it."""
        if mode not in {"sync", "queue"}:
            raise ValueError("重试模式必须是 sync 或 queue。")
        source = self._get_owned_run(db, project_id, experiment_id, run_id, owner_user_id)
        if source.status in {"queued", "running"}:
            raise ValueError("只有已完成、失败、不可用或已取消的运行可以重试。")
        if source.status not in {"completed", "failed", "unavailable", "cancelled"}:
            raise ValueError("当前运行状态不支持重试。")
        if (source.manifest_json or {}).get("run_kind") == "parameter_sweep":
            try:
                source_control = source.manifest_json or {}
                sweep_request = ResearchParameterSweepCreate.model_validate(
                    source_control.get("sweep_request")
                    or (source_control.get("run_control") or {}).get("sweep_request")
                    or {}
                )
            except Exception as exc:  # noqa: BLE001 - normalize persisted request corruption
                raise ValueError("原参数候选运行缺少可重试的扫参请求。") from exc
            return self.start_parameter_sweep(
                db,
                project_id,
                experiment_id,
                owner_user_id,
                sweep_request,
                mode=mode,
                retry_of_run_id=source.id,
            )
        if mode == "sync":
            return self.start_run(
                db,
                project_id,
                experiment_id,
                owner_user_id,
                retry_of_run_id=source.id,
            )
        return self.queue_run(
            db,
            project_id,
            experiment_id,
            owner_user_id,
            retry_of_run_id=source.id,
        )

    def cancel_run(
        self,
        db: Session,
        project_id: int,
        experiment_id: int,
        run_id: int,
        owner_user_id: int,
    ) -> ResearchRunResponse:
        """Cancel a queued run or request cooperative cancellation of a running one."""
        run = self._get_owned_run(db, project_id, experiment_id, run_id, owner_user_id)
        if run.status == "queued":
            run.status = "cancelled"
            run.error_message = "用户取消了尚未开始的研究运行。"
            run.finished_at = self._now()
        elif run.status == "running":
            control = dict(run.manifest_json or {})
            control["cancel_requested"] = True
            run.manifest_json = control
            run.error_message = "已请求取消；当前步骤结束后 worker 将安全停止并保留已有产物。"
        else:
            raise ValueError("只有 queued 或 running 状态的运行可以取消。")
        db.commit()
        db.refresh(run)
        return self._response(run)

    def process_next_queued_run(self, db: Session) -> bool:
        """Claim and execute one queued research run, if available."""
        self.recover_stale_runs(db)
        run = (
            db.query(ResearchRun)
            .filter(ResearchRun.status == "queued")
            .order_by(ResearchRun.created_at.asc(), ResearchRun.id.asc())
            .first()
        )
        if run is None:
            return False
        claimed = (
            db.query(ResearchRun)
            .filter(ResearchRun.id == run.id, ResearchRun.status == "queued")
            .update(
                {ResearchRun.status: "running", ResearchRun.started_at: self._now()},
                synchronize_session=False,
            )
        )
        if claimed != 1:
            db.rollback()
            return False
        db.commit()
        db.refresh(run)
        experiment = db.get(ResearchExperiment, run.experiment_id)
        project = db.get(ResearchProject, run.project_id)
        if experiment is None or project is None:
            self._fail_run(db, run, experiment, "排队运行引用的项目或实验不存在。")
            return True
        experiment.status = "running"
        try:
            formula_spec = self._get_formula_spec(db, run.project_id, experiment.formula_spec_id)
            snapshot = self._get_snapshot(db, run.project_id, experiment.data_snapshot_id)
            self._validate_execution_preconditions(db, experiment, formula_spec, snapshot)
        except Exception as exc:  # noqa: BLE001 - persist a user-visible failed run
            self._fail_run(db, run, experiment, str(exc))
            return True
        db.commit()
        if (run.manifest_json or {}).get("run_kind") == "parameter_sweep":
            try:
                sweep_request = ResearchParameterSweepCreate.model_validate(
                    (run.manifest_json or {}).get("sweep_request") or {}
                )
                self.start_parameter_sweep(
                    db,
                    run.project_id,
                    run.experiment_id,
                    project.owner_user_id,
                    sweep_request,
                    mode="execute",
                    _run=run,
                )
            except Exception as exc:  # noqa: BLE001 - persist a user-visible failed sweep
                self._fail_run(db, run, experiment, str(exc))
            return True
        return_value = self._execute_claimed_run(
            db, run, experiment, formula_spec, snapshot, project.owner_user_id
        )
        return return_value is not None

    def recover_stale_runs(self, db: Session) -> int:
        """Fail runs left in ``running`` after a worker/process interruption.

        A run has no external process supervisor in the local-first deployment.
        The worker therefore treats an old ``started_at`` as an operational
        timeout and persists a terminal failure instead of leaving the run
        permanently invisible in the queue. The timeout is deliberately long
        by default and configurable for deployments with a known runtime SLA.
        """
        cutoff = self._now() - timedelta(minutes=get_app_settings().research_run_timeout_minutes)
        running_runs = (
            db.query(ResearchRun)
            .filter(
                ResearchRun.status == "running",
                ResearchRun.started_at.is_not(None),
                ResearchRun.started_at < cutoff,
            )
            .all()
        )
        stale_runs = [
            run for run in running_runs if (run.manifest_json or {}).get("execution_mode") == "queue"
        ]
        if not stale_runs:
            return 0
        for run in stale_runs:
            cancel_requested = bool((run.manifest_json or {}).get("cancel_requested"))
            run.status = "cancelled" if cancel_requested else "failed"
            run.error_message = (
                "研究运行在取消请求后超过 worker 超时窗口，已安全标记为 cancelled。"
                if cancel_requested
                else "研究运行超过配置的 worker 超时窗口，已标记为 failed；请检查 worker 日志后重新加入队列。"
            )
            run.finished_at = self._now()
            experiment = db.get(ResearchExperiment, run.experiment_id)
            if experiment is not None and experiment.status == "running":
                experiment.status = "cancelled" if cancel_requested else "failed"
        db.commit()
        return len(stale_runs)

    def _create_queued_run(
        self,
        db: Session,
        project_id: int,
        experiment_id: int,
        owner_user_id: int,
        *,
        execution_mode: str,
        retry_of_run_id: int | None = None,
    ) -> tuple[ResearchRun, ResearchExperiment, FormulaSpec, ResearchDataSnapshot]:
        experiment = self._get_owned_experiment(db, project_id, experiment_id, owner_user_id)
        formula_spec = self._get_formula_spec(db, project_id, experiment.formula_spec_id)
        snapshot = self._get_snapshot(db, project_id, experiment.data_snapshot_id)
        self._validate_execution_preconditions(db, experiment, formula_spec, snapshot)

        run_token = uuid4().hex
        control = {"execution_mode": execution_mode}
        if retry_of_run_id is not None:
            control["retry_of_run_id"] = retry_of_run_id
        run = ResearchRun(
            project_id=project_id,
            experiment_id=experiment_id,
            runner_type=experiment.runner_type,
            run_token=run_token,
            status="running" if execution_mode == "sync" else "queued",
            run_dir=f"research://runs/{run_token}",
            manifest_json=control,
            started_at=self._now() if execution_mode == "sync" else None,
        )
        if execution_mode == "sync":
            experiment.status = "running"
        db.add(run)
        db.commit()
        db.refresh(run)
        return run, experiment, formula_spec, snapshot

    def _execute_claimed_run(
        self,
        db: Session,
        run: ResearchRun,
        experiment: ResearchExperiment,
        formula_spec: FormulaSpec,
        snapshot: ResearchDataSnapshot,
        owner_user_id: int,
    ) -> ResearchRunResponse:
        project_id = run.project_id
        run_token = run.run_token
        if self._cancel_requested(run):
            return self._mark_cancelled(db, run, experiment)
        try:
            assets = self._get_snapshot_assets(db, project_id, snapshot)
            validation_samples = self._get_snapshot_validation_samples(db, project_id, snapshot)
            output_dir = get_app_settings().research_runs_dir / f"project-{project_id}" / run_token
            if experiment.runner_type == "idl":
                result = self.idl_runner.execute(
                    db=db,
                    project_id=project_id,
                    experiment=experiment,
                    formula_spec=formula_spec,
                    snapshot=snapshot,
                    assets=assets,
                    output_dir=output_dir,
                    run_token=run_token,
                )
                # Reuse the same reference-raster and independent point-sample
                # validation contract as PythonRunner.  The IDL manifest may
                # name a different GeoTIFF, but it must still resolve through
                # PythonRunner's safe prediction-output boundary.
                if (experiment.validation_plan_json or {}).get("reference_asset_id") is not None or (
                    experiment.validation_plan_json or {}
                ).get("sample_validation") is not None:
                    result = self.python_runner._attach_reference_validation(
                        result, experiment, snapshot, assets, output_dir, run_token
                    )
                    result = self.python_runner._attach_point_sample_validation(
                        result, experiment, snapshot, output_dir, run_token, validation_samples
                    )
            else:
                result = self.python_runner.execute(
                    experiment=experiment,
                    formula_spec=formula_spec,
                    snapshot=snapshot,
                    assets=assets,
                    output_dir=output_dir,
                    run_token=run_token,
                    validation_samples=validation_samples,
                )
                comparison = self._attach_declared_idl_comparison(
                    db=db,
                    project_id=project_id,
                    experiment=experiment,
                    result=result,
                    output_dir=output_dir,
                    run_token=run_token,
                )
                if comparison is not None:
                    result.manifest["idl_comparison"] = comparison.summary
                    result.outputs.extend(comparison.outputs)
                    self.python_runner._refresh_manifest_output(result, output_dir, run_token)
            if experiment.execution_mode == "formal":
                result.manifest["project_protocol"] = {
                    "revision_id": experiment.project_protocol_revision_id,
                    "sha256": experiment.project_protocol_hash,
                    "research_question": (experiment.project_protocol_json or {}).get("research_question"),
                }
                self.python_runner._refresh_manifest_output(result, output_dir, run_token)
                report_output = self.report_service.build(
                    output_dir=output_dir,
                    run_token=run_token,
                    run_manifest=result.manifest,
                    outputs=result.outputs,
                )
                result.outputs.append(report_output)
                self.python_runner._refresh_manifest_output(result, output_dir, run_token)
                project = self._get_owned_project(db, project_id, owner_user_id)
                evidence_cards = self._get_formula_evidence_cards(db, project_id, formula_spec)
                package_output = self.evidence_package_service.build(
                    output_dir=output_dir,
                    run_token=run_token,
                    project=project,
                    snapshot=snapshot,
                    assets=assets,
                    formula_spec=formula_spec,
                    evidence_cards=evidence_cards,
                    experiment=experiment,
                    run_manifest=result.manifest,
                    outputs=result.outputs,
                )
                result.outputs.append(package_output)
                result.manifest["evidence_package"] = package_output
                result.manifest["outputs"] = result.outputs
            self._refresh_run_control(db, run)
            run_manifest = self._with_run_control(run, result.manifest)
        except IdlRunnerUnavailableError as exc:
            self._refresh_run_control(db, run)
            run.status = "unavailable"
            run.error_message = str(exc)[:2000]
            run.finished_at = self._now()
            experiment.status = "unavailable"
            db.commit()
            db.refresh(run)
            return self._response(run)
        except Exception as exc:  # noqa: BLE001 - convert controlled runner failures into persisted run evidence
            self._refresh_run_control(db, run)
            cancelled = self._cancel_requested(run)
            run.status = "cancelled" if cancelled else "failed"
            run.error_message = (
                "已请求取消；执行器在当前步骤报错后安全停止。"
                if cancelled
                else str(exc)[:2000]
            )
            run.finished_at = self._now()
            experiment.status = "cancelled" if cancelled else "failed"
            db.commit()
            db.refresh(run)
            return self._response(run)

        cancelled = self._cancel_requested(run)
        run.status = "cancelled" if cancelled else "completed"
        run.manifest_json = run_manifest
        run.outputs_json = result.outputs
        run.error_message = (
            "已请求取消；已保留取消前生成的产物，结果不得作为正式结论使用。"
            if cancelled
            else None
        )
        run.finished_at = self._now()
        experiment.status = "cancelled" if cancelled else "completed"
        db.commit()
        db.refresh(run)
        return self._response(run)

    def _fail_run(
        self,
        db: Session,
        run: ResearchRun,
        experiment: ResearchExperiment | None,
        error_message: str,
    ) -> None:
        run.status = "failed"
        run.error_message = error_message[:2000]
        run.finished_at = self._now()
        if experiment is not None:
            experiment.status = "failed"
        db.commit()

    @staticmethod
    def _cancel_requested(run: ResearchRun) -> bool:
        return bool((run.manifest_json or {}).get("cancel_requested"))

    @staticmethod
    def _with_run_control(run: ResearchRun, manifest: dict) -> dict:
        output = dict(manifest)
        control = {
            key: value
            for key, value in (run.manifest_json or {}).items()
            if key in {"execution_mode", "retry_of_run_id", "cancel_requested"}
        }
        if control:
            output["run_control"] = control
        return output

    @staticmethod
    def _refresh_run_control(db: Session, run: ResearchRun) -> None:
        db.expire(run, ["manifest_json", "status", "error_message"])
        db.refresh(run, attribute_names=["manifest_json", "status", "error_message"])

    def _mark_cancelled(
        self,
        db: Session,
        run: ResearchRun,
        experiment: ResearchExperiment,
        *,
        error_message: str = "用户取消了研究运行。",
    ) -> ResearchRunResponse:
        run.status = "cancelled"
        run.error_message = error_message
        run.finished_at = self._now()
        experiment.status = "cancelled"
        db.commit()
        db.refresh(run)
        return self._response(run)

    def get_output_file(
        self,
        db: Session,
        project_id: int,
        experiment_id: int,
        run_id: int,
        file_name: str,
        owner_user_id: int,
    ) -> tuple[Path, str]:
        self._get_owned_experiment(db, project_id, experiment_id, owner_user_id)
        run = (
            db.query(ResearchRun)
            .filter(
                ResearchRun.id == run_id,
                ResearchRun.project_id == project_id,
                ResearchRun.experiment_id == experiment_id,
            )
            .first()
        )
        if run is None:
            raise LookupError("研究运行记录不存在。")
        safe_name = Path(file_name).name
        if safe_name != file_name:
            raise LookupError("研究运行产物不存在。")
        allowed_names = {str(item.get("file_name")) for item in run.outputs_json if item.get("file_name")}
        if safe_name not in allowed_names:
            raise LookupError("研究运行产物不存在。")
        run_dir = get_app_settings().research_runs_dir / f"project-{project_id}" / run.run_token
        path = (run_dir / safe_name).resolve()
        if not path.is_relative_to(run_dir.resolve()) or path.is_symlink() or not path.is_file():
            raise LookupError("研究运行产物不存在。")
        return path, mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)

    @staticmethod
    def _get_owned_experiment(
        db: Session, project_id: int, experiment_id: int, owner_user_id: int
    ) -> ResearchExperiment:
        ResearchRunService._get_owned_project(db, project_id, owner_user_id)
        experiment = (
            db.query(ResearchExperiment)
            .filter(ResearchExperiment.id == experiment_id, ResearchExperiment.project_id == project_id)
            .first()
        )
        if experiment is None:
            raise LookupError("研究实验不存在。")
        return experiment

    @staticmethod
    def _get_owned_run(
        db: Session,
        project_id: int,
        experiment_id: int,
        run_id: int,
        owner_user_id: int,
    ) -> ResearchRun:
        ResearchRunService._get_owned_experiment(db, project_id, experiment_id, owner_user_id)
        run = (
            db.query(ResearchRun)
            .filter(
                ResearchRun.id == run_id,
                ResearchRun.project_id == project_id,
                ResearchRun.experiment_id == experiment_id,
            )
            .first()
        )
        if run is None:
            raise LookupError("研究运行记录不存在。")
        return run

    @staticmethod
    def _get_owned_run_any_experiment(
        db: Session,
        project_id: int,
        run_id: int,
        owner_user_id: int,
    ) -> ResearchRun:
        ResearchRunService._get_owned_project(db, project_id, owner_user_id)
        run = (
            db.query(ResearchRun)
            .filter(ResearchRun.id == run_id, ResearchRun.project_id == project_id)
            .first()
        )
        if run is None:
            raise LookupError("研究运行记录不存在。")
        return run

    @staticmethod
    def _get_owned_project(db: Session, project_id: int, owner_user_id: int) -> ResearchProject:
        project = db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
        if project is None:
            raise LookupError("研究项目不存在。")
        if project.owner_user_id == owner_user_id:
            return project
        member = (
            db.query(ResearchProjectMember)
            .filter(ResearchProjectMember.project_id == project_id, ResearchProjectMember.user_id == owner_user_id)
            .first()
        )
        if member is None:
            raise LookupError("研究项目不存在。")
        return project

    @staticmethod
    def _get_formula_spec(db: Session, project_id: int, formula_spec_id: int) -> FormulaSpec:
        spec = (
            db.query(FormulaSpec)
            .filter(FormulaSpec.id == formula_spec_id, FormulaSpec.project_id == project_id)
            .first()
        )
        if spec is None:
            raise ValueError("实验引用的公式规格不存在或不属于当前项目。")
        return spec

    @staticmethod
    def _get_snapshot(db: Session, project_id: int, snapshot_id: int) -> ResearchDataSnapshot:
        snapshot = (
            db.query(ResearchDataSnapshot)
            .filter(ResearchDataSnapshot.id == snapshot_id, ResearchDataSnapshot.project_id == project_id)
            .first()
        )
        if snapshot is None:
            raise ValueError("实验引用的数据快照不存在或不属于当前项目。")
        return snapshot

    @staticmethod
    def _get_snapshot_assets(
        db: Session, project_id: int, snapshot: ResearchDataSnapshot
    ) -> list[ResearchDataAsset]:
        asset_ids = snapshot.asset_ids_json
        assets = (
            db.query(ResearchDataAsset)
            .filter(ResearchDataAsset.project_id == project_id, ResearchDataAsset.id.in_(asset_ids))
            .all()
        )
        assets_by_id = {asset.id: asset for asset in assets}
        if len(assets_by_id) != len(asset_ids):
            raise ValueError("数据快照引用的资产已缺失或不属于当前项目。")
        return [assets_by_id[asset_id] for asset_id in asset_ids]

    @staticmethod
    def _get_snapshot_validation_samples(
        db: Session, project_id: int, snapshot: ResearchDataSnapshot
    ) -> list[ResearchValidationSample]:
        """Only samples explicitly bound to this frozen snapshot can score a run."""
        return (
            db.query(ResearchValidationSample)
            .filter(
                ResearchValidationSample.project_id == project_id,
                ResearchValidationSample.data_snapshot_id == snapshot.id,
            )
            .order_by(ResearchValidationSample.id.asc())
            .all()
        )

    @staticmethod
    def _get_formula_evidence_cards(
        db: Session, project_id: int, formula_spec: FormulaSpec
    ) -> list[EvidenceCard]:
        card_ids = formula_spec.evidence_card_ids_json
        if not card_ids:
            return []
        cards = (
            db.query(EvidenceCard)
            .filter(EvidenceCard.project_id == project_id, EvidenceCard.id.in_(card_ids))
            .all()
        )
        cards_by_id = {card.id: card for card in cards}
        if len(cards_by_id) != len(card_ids):
            raise ValueError("运行引用的公式证据卡已缺失或不属于当前项目。")
        return [cards_by_id[card_id] for card_id in card_ids]

    def _attach_declared_idl_comparison(
        self,
        *,
        db: Session,
        project_id: int,
        experiment: ResearchExperiment,
        result: PythonRunResult,
        output_dir: Path,
        run_token: str,
    ) -> RasterComparisonResult | None:
        """Run the optional local-IDL raster comparison declared before execution."""
        plan = (experiment.parameters_json or {}).get("idl_comparison")
        if plan is None:
            return None
        ResearchService._validate_idl_comparison_plan(db, project_id, experiment.parameters_json or {})
        asset_id = int(plan["idl_output_asset_id"])
        asset = (
            db.query(ResearchDataAsset)
            .filter(ResearchDataAsset.id == asset_id, ResearchDataAsset.project_id == project_id)
            .first()
        )
        if asset is None:  # Defensive; validation above makes this unreachable in normal execution.
            raise ValueError("IDL 对照输出资产不存在或不属于当前项目。")
        return self.raster_comparison_service.compare(
            output_dir=output_dir,
            run_token=run_token,
            python_output_file=str(plan.get("python_output_file", "water_mask.tif")),
            idl_output_asset=asset,
            comparison_mode=str(plan.get("comparison_mode", "classification")),
            absolute_tolerance=float(plan.get("absolute_tolerance", 0.0)),
        )

    @staticmethod
    def _validate_execution_preconditions(
        db: Session, experiment: ResearchExperiment, formula_spec: FormulaSpec, snapshot: ResearchDataSnapshot
    ) -> None:
        ResearchService._validate_idl_comparison_plan(db, experiment.project_id, experiment.parameters_json or {})
        if experiment.execution_mode != "formal":
            return
        frozen_protocol, protocol_hash = ResearchService._freeze_project_protocol(experiment.project_protocol_json)
        ResearchService._validate_formal_project_protocol(frozen_protocol)
        if experiment.project_protocol_hash != protocol_hash:
            raise ValueError("正式实验的冻结项目研究协议指纹不一致；请重新创建实验。")
        if experiment.project_protocol_revision_id is None:
            raise ValueError("正式实验缺少已保存的项目研究协议版本；请重新创建实验。")
        protocol_revision = (
            db.query(ResearchProtocolRevision)
            .filter(
                ResearchProtocolRevision.id == experiment.project_protocol_revision_id,
                ResearchProtocolRevision.project_id == experiment.project_id,
                ResearchProtocolRevision.protocol_hash == protocol_hash,
            )
            .first()
        )
        if protocol_revision is None:
            raise ValueError("正式实验引用的项目研究协议版本不存在或不匹配；请重新创建实验。")
        if formula_spec.status != "frozen" or not snapshot.is_frozen:
            raise ValueError("正式实验的公式规格与数据快照必须保持冻结状态。")
        if not experiment.validation_plan_json:
            raise ValueError("正式实验缺少验证方案。")
        ResearchService._validate_formal_validation_plan(db, snapshot, experiment.validation_plan_json)
        if not experiment.visualization_contract_json:
            raise ValueError("正式实验缺少可视化证据契约。")

    @staticmethod
    def _response(run: ResearchRun) -> ResearchRunResponse:
        return ResearchRunResponse(
            id=run.id,
            project_id=run.project_id,
            experiment_id=run.experiment_id,
            runner_type=run.runner_type,
            status=run.status,
            run_token=run.run_token,
            manifest=run.manifest_json,
            outputs=run.outputs_json,
            error_message=run.error_message,
            started_at=run.started_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
        )
