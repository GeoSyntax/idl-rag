from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.api.schemas import ResearchProtocolReadinessItem, ResearchProtocolReadinessResponse
from app.db.models import EvidenceCard, ResearchDataAsset, ResearchDataSnapshot
from app.services.research_service import ResearchService


class ResearchProtocolReadinessService:
    """Explain whether the saved project protocol is ready for formal review.

    This is a read-only diagnostic. Preview experiments remain intentionally
    permissive, while formal experiment creation and execution keep their own
    input-specific validation rules.
    """

    _TRUSTED_EVIDENCE_STATUSES = {"verified", "imported", "experiment_pinned"}
    _REQUIRED_VISUALIZATIONS = {"input", "preprocessing", "feature", "classification", "validation_error"}

    def __init__(self) -> None:
        self.project_service = ResearchService()

    def check(
        self, db: Session, project_id: int, owner_user_id: int
    ) -> ResearchProtocolReadinessResponse:
        project = self.project_service._get_owned_project(db, project_id, owner_user_id)
        protocol, protocol_hash = self.project_service._freeze_project_protocol(project.protocol_json)
        revision = self.project_service._current_protocol_revision(db, project_id, protocol_hash)
        missing: list[ResearchProtocolReadinessItem] = []

        def require(code: str, path: str, message: str) -> None:
            missing.append(ResearchProtocolReadinessItem(code=code, path=path, message=message))

        if revision is None:
            require("protocol_revision_missing", "protocol", "请保存一次当前协议，使其产生可追溯的版本。")

        question = protocol.get("research_question", protocol.get("question"))
        if not isinstance(question, str) or len(question.strip()) < 8:
            require("research_question_missing", "research_question", "研究问题至少需要 8 个字符。")

        hypothesis = protocol.get("hypothesis")
        if not isinstance(hypothesis, str) or len(hypothesis.strip()) < 8 or hypothesis.strip().startswith("待研究者"):
            require("hypothesis_missing", "hypothesis", "请写出可被数据检验的假设，并标明它仍是待验证命题。")

        study_area = protocol.get("study_area")
        if not isinstance(study_area, dict):
            require("roi_missing", "study_area.roi_asset_id", "请登记研究区 ROI 资产。")
        else:
            roi_asset_id = study_area.get("roi_asset_id")
            roi_asset = (
                db.query(ResearchDataAsset)
                .filter(
                    ResearchDataAsset.id == roi_asset_id,
                    ResearchDataAsset.project_id == project_id,
                    ResearchDataAsset.asset_kind.in_(["roi", "vector"]),
                )
                .first()
                if isinstance(roi_asset_id, int) and not isinstance(roi_asset_id, bool)
                else None
            )
            if roi_asset is None:
                require("roi_missing", "study_area.roi_asset_id", "请引用当前项目中 asset_kind=roi 或 vector 的研究区资产。")

        temporal_scope = protocol.get("temporal_scope")
        if not isinstance(temporal_scope, dict):
            require("temporal_scope_missing", "temporal_scope", "请填写研究起止日期。")
        else:
            start = temporal_scope.get("start")
            end = temporal_scope.get("end")
            try:
                start_date = date.fromisoformat(start) if isinstance(start, str) else None
                end_date = date.fromisoformat(end) if isinstance(end, str) else None
            except ValueError:
                start_date = end_date = None
            if start_date is None or end_date is None or start_date >= end_date:
                require("temporal_scope_missing", "temporal_scope.start/end", "请填写有效且先后顺序正确的 ISO 日期范围。")

        data_plan = protocol.get("data_plan")
        snapshot = None
        if isinstance(data_plan, dict):
            snapshot_id = data_plan.get("snapshot_id")
            if isinstance(snapshot_id, int) and not isinstance(snapshot_id, bool):
                snapshot = (
                    db.query(ResearchDataSnapshot)
                    .filter(ResearchDataSnapshot.id == snapshot_id, ResearchDataSnapshot.project_id == project_id)
                    .first()
                )
        if snapshot is None or not snapshot.is_frozen:
            require("snapshot_missing", "data_plan.snapshot_id", "请引用当前项目已冻结的数据快照。")

        method_plan = protocol.get("method_plan")
        evidence_ids = method_plan.get("evidence_card_ids") if isinstance(method_plan, dict) else None
        trusted_cards = (
            db.query(EvidenceCard)
            .filter(
                EvidenceCard.project_id == project_id,
                EvidenceCard.id.in_(evidence_ids),
                EvidenceCard.status.in_(self._TRUSTED_EVIDENCE_STATUSES),
            )
            .count()
            if isinstance(evidence_ids, list) and evidence_ids
            else 0
        )
        if trusted_cards == 0:
            require("evidence_missing", "method_plan.evidence_card_ids", "至少关联一张当前项目内已核验或已导入的 EvidenceCard。")

        validation_plan = protocol.get("validation_plan")
        if not isinstance(validation_plan, dict) or validation_plan.get("split") != "spatiotemporal-holdout":
            require("validation_split_missing", "validation_plan.split", "验证方案必须声明 spatiotemporal-holdout。")
        else:
            test_period = validation_plan.get("independent_test_period")
            if not isinstance(test_period, str) or not test_period.strip():
                require("independent_test_period_missing", "validation_plan.independent_test_period", "请声明独立测试时段。")
            reference_source = validation_plan.get("reference_source")
            if not isinstance(reference_source, str) or not reference_source.strip() or reference_source.strip().startswith("待登记"):
                require("validation_reference_missing", "validation_plan.reference_source", "请声明独立参考栅格、点样本或权威资料来源。")
            spatial_blocks = validation_plan.get("spatial_blocks")
            if not isinstance(spatial_blocks, list) or not spatial_blocks:
                require("spatial_blocks_missing", "validation_plan.spatial_blocks", "请声明开发/测试使用的空间分块方案。")

        visualizations = protocol.get("visualization_contract")
        if not isinstance(visualizations, list) or not self._REQUIRED_VISUALIZATIONS.issubset(set(visualizations)):
            require(
                "visualization_contract_missing",
                "visualization_contract",
                "至少声明 input、preprocessing、feature、classification 和 validation_error 图件。",
            )

        conclusion_boundary = protocol.get("conclusion_boundary")
        if not isinstance(conclusion_boundary, str) or len(conclusion_boundary.strip()) < 12:
            require("conclusion_boundary_missing", "conclusion_boundary", "请写明正式结论的适用范围和限制。")

        if missing:
            notice = f"当前协议还有 {len(missing)} 项需要补齐；该检查只读，不会阻断预览实验。"
        else:
            notice = "当前保存协议已通过研究设计就绪检查；创建正式实验时仍需声明具体的独立验证样本/参考资产和指标。"
        return ResearchProtocolReadinessResponse(
            project_id=project_id,
            ready=not missing,
            protocol_hash=protocol_hash,
            protocol_revision_id=revision.id if revision is not None else None,
            missing=missing,
            notice=notice,
        )
