from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.schemas import (
    EvidenceCardCreate,
    EvidenceCardResponse,
    FormulaSpecCreate,
    FormulaSpecResponse,
    ResearchDataAssetCreate,
    ResearchDataAssetResponse,
    ResearchDataSnapshotCreate,
    ResearchDataSnapshotResponse,
    ResearchExperimentCreate,
    ResearchExperimentResponse,
    ResearchProjectCreate,
    ResearchProjectMemberCreate,
    ResearchProjectMemberResponse,
    ResearchProtocolDraftRequest,
    ResearchProtocolDraftResponse,
    ResearchProtocolRevisionResponse,
    ResearchProjectResponse,
    ResearchProjectUpdate,
    ResearchValidationSampleCreate,
    ResearchValidationSampleImportResponse,
    ResearchValidationSampleResponse,
)
from app.db.models import (
    EvidenceCard,
    FormulaSpec,
    ResearchDataAsset,
    ResearchDataSnapshot,
    ResearchExperiment,
    ResearchProject,
    ResearchProjectMember,
    ResearchProtocolRevision,
    ResearchValidationSample,
    User,
)


class ResearchService:
    """项目级研究协议、数据目录和低摩擦协作服务。"""

    _TRUSTED_EVIDENCE_STATUSES = {"verified", "imported", "experiment_pinned"}
    _IDL_ENTRYPOINT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def list_projects(self, db: Session, owner_user_id: int) -> list[ResearchProjectResponse]:
        projects = (
            db.query(ResearchProject)
            .outerjoin(ResearchProjectMember, ResearchProjectMember.project_id == ResearchProject.id)
            .filter(or_(ResearchProject.owner_user_id == owner_user_id, ResearchProjectMember.user_id == owner_user_id))
            .distinct()
            .order_by(ResearchProject.created_at.desc(), ResearchProject.id.desc())
            .all()
        )
        return [self._project_response(project) for project in projects]

    def create_project(
        self, db: Session, payload: ResearchProjectCreate, owner_user_id: int
    ) -> ResearchProjectResponse:
        project = ResearchProject(
            owner_user_id=owner_user_id,
            name=payload.name.strip(),
            description=self._optional_text(payload.description),
            entry_mode=payload.entry_mode,
            protocol_json=payload.protocol,
        )
        db.add(project)
        db.flush()
        if payload.protocol:
            self._record_protocol_revision(db, project, payload.protocol, owner_user_id)
        db.commit()
        db.refresh(project)
        return self._project_response(project)

    def list_project_members(
        self, db: Session, project_id: int, owner_user_id: int
    ) -> list[ResearchProjectMemberResponse]:
        self._get_owned_project(db, project_id, owner_user_id)
        members = (
            db.query(ResearchProjectMember)
            .filter(ResearchProjectMember.project_id == project_id)
            .order_by(ResearchProjectMember.created_at.asc(), ResearchProjectMember.id.asc())
            .all()
        )
        return [self._project_member_response(member) for member in members]

    def add_project_member(
        self,
        db: Session,
        project_id: int,
        payload: ResearchProjectMemberCreate,
        owner_user_id: int,
    ) -> ResearchProjectMemberResponse:
        project = self._get_project_owner(db, project_id, owner_user_id)
        username = payload.username.strip()
        user = db.query(User).filter(User.username == username).first()
        if user is None or not user.is_active:
            raise ValueError("要邀请的用户不存在或未启用。")
        if user.id == project.owner_user_id:
            raise ValueError("项目所有者已拥有访问权限，无需重复邀请。")
        existing = (
            db.query(ResearchProjectMember)
            .filter(ResearchProjectMember.project_id == project_id, ResearchProjectMember.user_id == user.id)
            .first()
        )
        if existing is not None:
            raise ValueError("该用户已是项目成员。")
        member = ResearchProjectMember(
            project_id=project_id,
            user_id=user.id,
            added_by_user_id=owner_user_id,
        )
        db.add(member)
        db.commit()
        db.refresh(member)
        return self._project_member_response(member)

    def remove_project_member(
        self, db: Session, project_id: int, member_id: int, owner_user_id: int
    ) -> None:
        self._get_project_owner(db, project_id, owner_user_id)
        member = (
            db.query(ResearchProjectMember)
            .filter(ResearchProjectMember.id == member_id, ResearchProjectMember.project_id == project_id)
            .first()
        )
        if member is None:
            raise LookupError("项目成员不存在。")
        db.delete(member)
        db.commit()

    def get_project(self, db: Session, project_id: int, owner_user_id: int) -> ResearchProjectResponse:
        return self._project_response(self._get_owned_project(db, project_id, owner_user_id))

    def update_project(
        self,
        db: Session,
        project_id: int,
        payload: ResearchProjectUpdate,
        owner_user_id: int,
    ) -> ResearchProjectResponse:
        project = self._get_owned_project(db, project_id, owner_user_id)
        updates = payload.model_dump(exclude_unset=True)
        if "name" in updates:
            project.name = updates["name"].strip()
        if "description" in updates:
            project.description = self._optional_text(updates["description"])
        if "protocol" in updates:
            normalized_protocol, _ = self._freeze_project_protocol(updates["protocol"])
            project.protocol_json = normalized_protocol
            self._record_protocol_revision(db, project, normalized_protocol, owner_user_id)
        db.commit()
        db.refresh(project)
        return self._project_response(project)

    def draft_protocol(
        self,
        db: Session,
        project_id: int,
        payload: ResearchProtocolDraftRequest,
        owner_user_id: int,
    ) -> ResearchProtocolDraftResponse:
        """Create an editable local protocol starter without sending project data outward."""
        self._get_owned_project(db, project_id, owner_user_id)
        question = payload.research_question.strip()
        protocol = {
            "schema_version": 1,
            "status": "draft",
            "research_question": question,
            "hypothesis": "待研究者基于可核验证据填写；系统不会预设候选公式必然优于基线。",
            "study_area": {
                "description": "待确认研究区与缓冲范围。",
                "roi_asset_id": None,
            },
            "temporal_scope": {
                "start": None,
                "end": None,
                "seasonal_strata": [],
            },
            "data_plan": {
                "snapshot_id": None,
                "source_assets": [],
                "preprocessing_rules": [],
                "private_data_egress": "private-local",
            },
            "method_plan": {
                "evidence_card_ids": [],
                "baseline_formula_spec_ids": [],
                "candidate_formula_spec_ids": [],
                "parameters_to_freeze": [],
            },
            "validation_plan": {
                "split": "spatiotemporal-holdout",
                "development_period": None,
                "model_selection_period": None,
                "independent_test_period": None,
                "spatial_blocks": [],
                "reference_source": "待登记共网格参考栅格或独立点样本。",
            },
            "visualization_contract": [
                "input",
                "preprocessing",
                "feature",
                "classification",
                "uncertainty",
                "validation_error",
            ],
            "conclusion_boundary": "仅冻结数据、公式、参数和独立验证完成后，才可形成正式结论。",
        }
        return ResearchProtocolDraftResponse(
            protocol=protocol,
            notice="已生成本地结构化协议草案；未查询外部服务、RAG 或模型。请补充并保存后再创建正式实验。",
        )

    def list_protocol_revisions(
        self, db: Session, project_id: int, owner_user_id: int
    ) -> list[ResearchProtocolRevisionResponse]:
        self._get_owned_project(db, project_id, owner_user_id)
        revisions = (
            db.query(ResearchProtocolRevision)
            .filter(ResearchProtocolRevision.project_id == project_id)
            .order_by(ResearchProtocolRevision.version.desc(), ResearchProtocolRevision.id.desc())
            .all()
        )
        return [self._protocol_revision_response(revision) for revision in revisions]

    def list_data_assets(
        self, db: Session, project_id: int, owner_user_id: int
    ) -> list[ResearchDataAssetResponse]:
        self._get_owned_project(db, project_id, owner_user_id)
        assets = (
            db.query(ResearchDataAsset)
            .filter(ResearchDataAsset.project_id == project_id)
            .order_by(ResearchDataAsset.created_at.asc(), ResearchDataAsset.id.asc())
            .all()
        )
        return [self._data_asset_response(asset) for asset in assets]

    def create_data_asset(
        self,
        db: Session,
        project_id: int,
        payload: ResearchDataAssetCreate,
        owner_user_id: int,
    ) -> ResearchDataAssetResponse:
        self._get_owned_project(db, project_id, owner_user_id)
        asset = ResearchDataAsset(
            project_id=project_id,
            name=payload.name.strip(),
            asset_kind=payload.asset_kind,
            source_type=payload.source_type,
            source_uri=payload.source_uri.strip(),
            sha256=payload.sha256.lower() if payload.sha256 else None,
            metadata_json=payload.metadata,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        return self._data_asset_response(asset)

    def list_data_snapshots(
        self, db: Session, project_id: int, owner_user_id: int
    ) -> list[ResearchDataSnapshotResponse]:
        self._get_owned_project(db, project_id, owner_user_id)
        snapshots = (
            db.query(ResearchDataSnapshot)
            .filter(ResearchDataSnapshot.project_id == project_id)
            .order_by(ResearchDataSnapshot.created_at.desc(), ResearchDataSnapshot.id.desc())
            .all()
        )
        return [self._data_snapshot_response(snapshot) for snapshot in snapshots]

    def create_data_snapshot(
        self,
        db: Session,
        project_id: int,
        payload: ResearchDataSnapshotCreate,
        owner_user_id: int,
    ) -> ResearchDataSnapshotResponse:
        self._get_owned_project(db, project_id, owner_user_id)
        asset_ids = payload.asset_ids
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError("数据快照不能重复引用同一数据资产。")

        assets = (
            db.query(ResearchDataAsset)
            .filter(ResearchDataAsset.project_id == project_id, ResearchDataAsset.id.in_(asset_ids))
            .all()
        )
        assets_by_id = {asset.id: asset for asset in assets}
        if len(assets_by_id) != len(asset_ids):
            raise ValueError("数据快照只能引用当前研究项目中的数据资产。")
        if any((asset.metadata_json or {}).get("asset_role") == "idl_script" for asset in assets):
            raise ValueError("IDL 脚本资产不能进入数据快照；请在 IDL 实验参数中显式引用脚本。")

        snapshot_payload = {
            "asset_ids": asset_ids,
            "assets": [self._asset_fingerprint_payload(assets_by_id[asset_id]) for asset_id in asset_ids],
        }
        snapshot_hash = hashlib.sha256(
            json.dumps(snapshot_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        snapshot = ResearchDataSnapshot(
            project_id=project_id,
            name=payload.name.strip(),
            description=self._optional_text(payload.description),
            asset_ids_json=asset_ids,
            snapshot_hash=snapshot_hash,
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
        return self._data_snapshot_response(snapshot)

    def list_evidence_cards(
        self, db: Session, project_id: int, owner_user_id: int
    ) -> list[EvidenceCardResponse]:
        self._get_owned_project(db, project_id, owner_user_id)
        cards = (
            db.query(EvidenceCard)
            .filter(EvidenceCard.project_id == project_id)
            .order_by(EvidenceCard.created_at.desc(), EvidenceCard.id.desc())
            .all()
        )
        return [self._evidence_card_response(card) for card in cards]

    def create_evidence_card(
        self,
        db: Session,
        project_id: int,
        payload: EvidenceCardCreate,
        owner_user_id: int,
    ) -> EvidenceCardResponse:
        self._get_owned_project(db, project_id, owner_user_id)
        if payload.status in self._TRUSTED_EVIDENCE_STATUSES and not (payload.source_url or payload.doi):
            raise ValueError("已核验或已固定的证据卡必须提供 DOI 或来源链接。")
        card = EvidenceCard(
            project_id=project_id,
            title=payload.title.strip(),
            status=payload.status,
            source_type=payload.source_type,
            source_url=self._optional_text(payload.source_url),
            doi=self._optional_text(payload.doi),
            license_note=self._optional_text(payload.license_note),
            applicability=self._optional_text(payload.applicability),
            limitations=self._optional_text(payload.limitations),
            metadata_json=payload.metadata,
        )
        db.add(card)
        db.commit()
        db.refresh(card)
        return self._evidence_card_response(card)

    def list_formula_specs(
        self, db: Session, project_id: int, owner_user_id: int
    ) -> list[FormulaSpecResponse]:
        self._get_owned_project(db, project_id, owner_user_id)
        specs = (
            db.query(FormulaSpec)
            .filter(FormulaSpec.project_id == project_id)
            .order_by(FormulaSpec.name.asc(), FormulaSpec.version.desc(), FormulaSpec.id.desc())
            .all()
        )
        return [self._formula_spec_response(spec) for spec in specs]

    def create_formula_spec(
        self,
        db: Session,
        project_id: int,
        payload: FormulaSpecCreate,
        owner_user_id: int,
    ) -> FormulaSpecResponse:
        self._get_owned_project(db, project_id, owner_user_id)
        cards = self._resolve_evidence_cards(db, project_id, payload.evidence_card_ids)
        if payload.status in {"candidate", "frozen"} and not cards:
            raise ValueError("候选或冻结的公式规格必须至少关联一张证据卡。")
        if payload.status == "frozen" and not any(
            card.status in self._TRUSTED_EVIDENCE_STATUSES for card in cards
        ):
            raise ValueError("冻结的公式规格必须关联至少一张已核验证据卡。")

        spec = FormulaSpec(
            project_id=project_id,
            name=payload.name.strip(),
            version=payload.version,
            status=payload.status,
            spec_json=payload.spec,
            evidence_card_ids_json=payload.evidence_card_ids,
        )
        db.add(spec)
        db.commit()
        db.refresh(spec)
        return self._formula_spec_response(spec)

    def list_experiments(
        self, db: Session, project_id: int, owner_user_id: int
    ) -> list[ResearchExperimentResponse]:
        self._get_owned_project(db, project_id, owner_user_id)
        experiments = (
            db.query(ResearchExperiment)
            .filter(ResearchExperiment.project_id == project_id)
            .order_by(ResearchExperiment.created_at.desc(), ResearchExperiment.id.desc())
            .all()
        )
        return [self._experiment_response(experiment) for experiment in experiments]

    def create_experiment(
        self,
        db: Session,
        project_id: int,
        payload: ResearchExperimentCreate,
        owner_user_id: int,
    ) -> ResearchExperimentResponse:
        project = self._get_owned_project(db, project_id, owner_user_id)
        frozen_project_protocol, project_protocol_hash = self._freeze_project_protocol(project.protocol_json)
        protocol_revision = self._current_protocol_revision(db, project_id, project_protocol_hash)

        if payload.execution_mode == "formal":
            self._validate_formal_project_protocol(frozen_project_protocol)
            if protocol_revision is None:
                raise ValueError("正式实验必须使用已保存且可追溯的项目研究协议版本。")
        formula_spec = self._get_project_formula_spec(db, project_id, payload.formula_spec_id)
        snapshot = self._get_project_snapshot(db, project_id, payload.data_snapshot_id)
        self._validate_idl_comparison_plan(db, project_id, payload.parameters)
        self._validate_idl_runner_plan(db, project_id, payload.runner_type, payload.parameters)

        if payload.execution_mode == "formal":
            if formula_spec.status != "frozen":
                raise ValueError("正式实验只能使用已冻结的公式规格。")
            if not snapshot.is_frozen:
                raise ValueError("正式实验只能使用已冻结的数据快照。")
            if not payload.validation_plan:
                raise ValueError("正式实验必须记录验证方案。")
            self._validate_formal_validation_plan(db, snapshot, payload.validation_plan)
            if not payload.visualization_contract:
                raise ValueError("正式实验必须声明可视化证据契约。")

        experiment = ResearchExperiment(
            project_id=project_id,
            formula_spec_id=formula_spec.id,
            data_snapshot_id=snapshot.id,
            name=payload.name.strip(),
            runner_type=payload.runner_type,
            execution_mode=payload.execution_mode,
            parameters_json=payload.parameters,
            validation_plan_json=payload.validation_plan,
            visualization_contract_json=payload.visualization_contract,
            project_protocol_revision_id=protocol_revision.id if protocol_revision is not None else None,
            project_protocol_json=frozen_project_protocol,
            project_protocol_hash=project_protocol_hash,
        )
        db.add(experiment)
        db.commit()
        db.refresh(experiment)
        return self._experiment_response(experiment)

    def list_validation_samples(
        self, db: Session, project_id: int, owner_user_id: int
    ) -> list[ResearchValidationSampleResponse]:
        self._get_owned_project(db, project_id, owner_user_id)
        samples = (
            db.query(ResearchValidationSample)
            .filter(ResearchValidationSample.project_id == project_id)
            .order_by(ResearchValidationSample.observed_at.asc(), ResearchValidationSample.id.asc())
            .all()
        )
        return [self._validation_sample_response(sample) for sample in samples]

    def create_validation_sample(
        self,
        db: Session,
        project_id: int,
        payload: ResearchValidationSampleCreate,
        owner_user_id: int,
    ) -> ResearchValidationSampleResponse:
        self._get_owned_project(db, project_id, owner_user_id)
        if payload.data_snapshot_id is not None:
            self._get_project_snapshot(db, project_id, payload.data_snapshot_id)
        if payload.source_asset_id is not None:
            asset = (
                db.query(ResearchDataAsset)
                .filter(ResearchDataAsset.id == payload.source_asset_id, ResearchDataAsset.project_id == project_id)
                .first()
            )
            if asset is None:
                raise ValueError("验证样本的 source_asset_id 不属于当前项目。")
        sample = ResearchValidationSample(
            project_id=project_id,
            data_snapshot_id=payload.data_snapshot_id,
            source_asset_id=payload.source_asset_id,
            longitude=payload.longitude,
            latitude=payload.latitude,
            label=payload.label,
            observed_at=payload.observed_at.replace(tzinfo=None),
            annotator=payload.annotator.strip(),
            confidence=payload.confidence,
            split=payload.split,
            spatial_block=payload.spatial_block.strip(),
            temporal_stratum=payload.temporal_stratum.strip(),
            conflict_status=payload.conflict_status,
            source_note=payload.source_note.strip(),
            metadata_json=payload.metadata,
        )
        db.add(sample)
        db.commit()
        db.refresh(sample)
        return self._validation_sample_response(sample)

    def import_validation_samples(
        self,
        db: Session,
        project_id: int,
        data_snapshot_id: int,
        payloads: list[ResearchValidationSampleCreate],
        owner_user_id: int,
    ) -> ResearchValidationSampleImportResponse:
        """Atomically add validated CSV rows that all belong to one frozen snapshot."""
        if not payloads:
            raise ValueError("验证样本 CSV 不包含可导入的记录。")
        self._get_owned_project(db, project_id, owner_user_id)
        self._get_project_snapshot(db, project_id, data_snapshot_id)
        if any(payload.data_snapshot_id != data_snapshot_id for payload in payloads):
            raise ValueError("批量导入的每条验证样本必须绑定同一个指定的数据快照。")

        source_asset_ids = {payload.source_asset_id for payload in payloads if payload.source_asset_id is not None}
        if source_asset_ids:
            matching_asset_ids = {
                asset_id
                for (asset_id,) in (
                    db.query(ResearchDataAsset.id)
                    .filter(
                        ResearchDataAsset.project_id == project_id,
                        ResearchDataAsset.id.in_(source_asset_ids),
                    )
                    .all()
                )
            }
            if matching_asset_ids != source_asset_ids:
                raise ValueError("验证样本 CSV 包含不属于当前项目的 source_asset_id。")

        samples = [
            ResearchValidationSample(
                project_id=project_id,
                data_snapshot_id=payload.data_snapshot_id,
                source_asset_id=payload.source_asset_id,
                longitude=payload.longitude,
                latitude=payload.latitude,
                label=payload.label,
                observed_at=payload.observed_at.replace(tzinfo=None),
                annotator=payload.annotator.strip(),
                confidence=payload.confidence,
                split=payload.split,
                spatial_block=payload.spatial_block.strip(),
                temporal_stratum=payload.temporal_stratum.strip(),
                conflict_status=payload.conflict_status,
                source_note=payload.source_note.strip(),
                metadata_json=payload.metadata,
            )
            for payload in payloads
        ]
        db.add_all(samples)
        db.commit()
        for sample in samples:
            db.refresh(sample)
        return ResearchValidationSampleImportResponse(
            imported_count=len(samples),
            samples=[self._validation_sample_response(sample) for sample in samples],
        )

    @staticmethod
    def _optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _asset_fingerprint_payload(asset: ResearchDataAsset) -> dict:
        return {
            "id": asset.id,
            "name": asset.name,
            "asset_kind": asset.asset_kind,
            "source_type": asset.source_type,
            "source_uri": asset.source_uri,
            "sha256": asset.sha256,
            "metadata": asset.metadata_json,
            "access_policy": asset.access_policy,
        }

    @staticmethod
    def _project_response(project: ResearchProject) -> ResearchProjectResponse:
        return ResearchProjectResponse(
            id=project.id,
            owner_user_id=project.owner_user_id,
            name=project.name,
            description=project.description,
            entry_mode=project.entry_mode,
            visibility=project.visibility,
            egress_policy=project.egress_policy,
            status=project.status,
            protocol=project.protocol_json,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )

    @staticmethod
    def _data_asset_response(asset: ResearchDataAsset) -> ResearchDataAssetResponse:
        return ResearchDataAssetResponse(
            id=asset.id,
            project_id=asset.project_id,
            name=asset.name,
            asset_kind=asset.asset_kind,
            source_type=asset.source_type,
            source_uri=asset.source_uri,
            sha256=asset.sha256,
            metadata=asset.metadata_json,
            access_policy=asset.access_policy,
            created_at=asset.created_at,
        )

    @staticmethod
    def _data_snapshot_response(snapshot: ResearchDataSnapshot) -> ResearchDataSnapshotResponse:
        return ResearchDataSnapshotResponse(
            id=snapshot.id,
            project_id=snapshot.project_id,
            name=snapshot.name,
            description=snapshot.description,
            asset_ids=snapshot.asset_ids_json,
            snapshot_hash=snapshot.snapshot_hash,
            is_frozen=True,
            frozen_at=snapshot.frozen_at,
            created_at=snapshot.created_at,
        )

    @staticmethod
    def _evidence_card_response(card: EvidenceCard) -> EvidenceCardResponse:
        return EvidenceCardResponse(
            id=card.id,
            project_id=card.project_id,
            title=card.title,
            status=card.status,
            source_type=card.source_type,
            source_url=card.source_url,
            doi=card.doi,
            license_note=card.license_note,
            applicability=card.applicability,
            limitations=card.limitations,
            metadata=card.metadata_json,
            retrieved_at=card.retrieved_at,
            created_at=card.created_at,
        )

    @staticmethod
    def _formula_spec_response(spec: FormulaSpec) -> FormulaSpecResponse:
        return FormulaSpecResponse(
            id=spec.id,
            project_id=spec.project_id,
            name=spec.name,
            version=spec.version,
            status=spec.status,
            spec=spec.spec_json,
            evidence_card_ids=spec.evidence_card_ids_json,
            created_at=spec.created_at,
            updated_at=spec.updated_at,
        )

    @staticmethod
    def _experiment_response(experiment: ResearchExperiment) -> ResearchExperimentResponse:
        return ResearchExperimentResponse(
            id=experiment.id,
            project_id=experiment.project_id,
            formula_spec_id=experiment.formula_spec_id,
            data_snapshot_id=experiment.data_snapshot_id,
            name=experiment.name,
            runner_type=experiment.runner_type,
            execution_mode=experiment.execution_mode,
            status=experiment.status,
            parameters=experiment.parameters_json,
            validation_plan=experiment.validation_plan_json,
            visualization_contract=experiment.visualization_contract_json,
            project_protocol_revision_id=experiment.project_protocol_revision_id,
            project_protocol_hash=experiment.project_protocol_hash,
            created_at=experiment.created_at,
        )

    @staticmethod
    def _freeze_project_protocol(protocol: object) -> tuple[dict, str]:
        """Canonicalize a per-experiment copy so later project edits cannot rewrite history."""
        if not isinstance(protocol, dict):
            raise ValueError("项目研究协议必须是 JSON 对象。")
        try:
            canonical_payload = json.dumps(
                protocol, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("项目研究协议必须是可序列化且不含 NaN 的 JSON 对象。") from exc
        return json.loads(canonical_payload), hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_formal_project_protocol(protocol: dict) -> None:
        question = protocol.get("research_question", protocol.get("question"))
        if not isinstance(question, str) or len(question.strip()) < 8:
            raise ValueError("正式实验必须先保存包含至少 8 个字符 research_question 或 question 的项目研究协议。")

    def _record_protocol_revision(
        self,
        db: Session,
        project: ResearchProject,
        protocol: object,
        saved_by_user_id: int,
    ) -> ResearchProtocolRevision:
        normalized_protocol, protocol_hash = self._freeze_project_protocol(protocol)
        latest = (
            db.query(ResearchProtocolRevision)
            .filter(ResearchProtocolRevision.project_id == project.id)
            .order_by(ResearchProtocolRevision.version.desc(), ResearchProtocolRevision.id.desc())
            .first()
        )
        if latest is not None and latest.protocol_hash == protocol_hash:
            return latest
        revision = ResearchProtocolRevision(
            project_id=project.id,
            version=1 if latest is None else latest.version + 1,
            protocol_json=normalized_protocol,
            protocol_hash=protocol_hash,
            saved_by_user_id=saved_by_user_id,
        )
        db.add(revision)
        db.flush()
        return revision

    @staticmethod
    def _current_protocol_revision(
        db: Session, project_id: int, protocol_hash: str
    ) -> ResearchProtocolRevision | None:
        return (
            db.query(ResearchProtocolRevision)
            .filter(
                ResearchProtocolRevision.project_id == project_id,
                ResearchProtocolRevision.protocol_hash == protocol_hash,
            )
            .order_by(ResearchProtocolRevision.version.desc(), ResearchProtocolRevision.id.desc())
            .first()
        )

    @staticmethod
    def _protocol_revision_response(revision: ResearchProtocolRevision) -> ResearchProtocolRevisionResponse:
        return ResearchProtocolRevisionResponse(
            id=revision.id,
            project_id=revision.project_id,
            version=revision.version,
            protocol=revision.protocol_json,
            protocol_hash=revision.protocol_hash,
            saved_by_user_id=revision.saved_by_user_id,
            created_at=revision.created_at,
        )

    @staticmethod
    def _validation_sample_response(sample: ResearchValidationSample) -> ResearchValidationSampleResponse:
        return ResearchValidationSampleResponse(
            id=sample.id,
            project_id=sample.project_id,
            data_snapshot_id=sample.data_snapshot_id,
            source_asset_id=sample.source_asset_id,
            longitude=sample.longitude,
            latitude=sample.latitude,
            label=sample.label,
            observed_at=sample.observed_at,
            annotator=sample.annotator,
            confidence=sample.confidence,
            split=sample.split,
            spatial_block=sample.spatial_block,
            temporal_stratum=sample.temporal_stratum,
            conflict_status=sample.conflict_status,
            source_note=sample.source_note,
            metadata=sample.metadata_json,
            created_at=sample.created_at,
        )

    @staticmethod
    def _project_member_response(member: ResearchProjectMember) -> ResearchProjectMemberResponse:
        return ResearchProjectMemberResponse(
            id=member.id,
            project_id=member.project_id,
            user_id=member.user_id,
            username=member.user.username,
            added_by_user_id=member.added_by_user_id,
            created_at=member.created_at,
        )

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
    def _get_project_owner(db: Session, project_id: int, owner_user_id: int) -> ResearchProject:
        project = (
            db.query(ResearchProject)
            .filter(ResearchProject.id == project_id, ResearchProject.owner_user_id == owner_user_id)
            .first()
        )
        if project is None:
            raise LookupError("研究项目不存在。")
        return project

    def _resolve_evidence_cards(
        self, db: Session, project_id: int, evidence_card_ids: list[int]
    ) -> list[EvidenceCard]:
        if len(set(evidence_card_ids)) != len(evidence_card_ids):
            raise ValueError("公式规格不能重复引用同一证据卡。")
        if not evidence_card_ids:
            return []
        cards = (
            db.query(EvidenceCard)
            .filter(EvidenceCard.project_id == project_id, EvidenceCard.id.in_(evidence_card_ids))
            .all()
        )
        if len(cards) != len(evidence_card_ids):
            raise ValueError("公式规格只能引用当前研究项目中的证据卡。")
        return cards

    @staticmethod
    def _get_project_formula_spec(db: Session, project_id: int, formula_spec_id: int) -> FormulaSpec:
        formula_spec = (
            db.query(FormulaSpec)
            .filter(FormulaSpec.id == formula_spec_id, FormulaSpec.project_id == project_id)
            .first()
        )
        if formula_spec is None:
            raise ValueError("公式规格不存在或不属于当前研究项目。")
        return formula_spec

    @staticmethod
    def _get_project_snapshot(db: Session, project_id: int, snapshot_id: int) -> ResearchDataSnapshot:
        snapshot = (
            db.query(ResearchDataSnapshot)
            .filter(ResearchDataSnapshot.id == snapshot_id, ResearchDataSnapshot.project_id == project_id)
            .first()
        )
        if snapshot is None:
            raise ValueError("数据快照不存在或不属于当前研究项目。")
        return snapshot

    @staticmethod
    def _validate_idl_comparison_plan(db: Session, project_id: int, parameters: dict) -> None:
        """Validate the optional, declared Python-versus-local-IDL raster comparison."""
        plan = parameters.get("idl_comparison")
        if plan is None:
            return
        if not isinstance(plan, dict):
            raise ValueError("parameters.idl_comparison 必须是对象。")
        asset_id = plan.get("idl_output_asset_id")
        if not isinstance(asset_id, int):
            raise ValueError("IDL 对照必须声明当前项目的 idl_output_asset_id。")
        asset = (
            db.query(ResearchDataAsset)
            .filter(ResearchDataAsset.id == asset_id, ResearchDataAsset.project_id == project_id)
            .first()
        )
        if asset is None:
            raise ValueError("IDL 对照输出资产不存在或不属于当前项目。")
        if asset.asset_kind != "derived":
            raise ValueError("IDL 对照输出必须登记为 asset_kind=derived 的私有项目资产。")
        mode = plan.get("comparison_mode", "classification")
        if mode not in {"classification", "continuous"}:
            raise ValueError("IDL 对照 comparison_mode 必须为 classification 或 continuous。")
        output_file = plan.get("python_output_file", "water_mask.tif")
        if not isinstance(output_file, str) or Path(output_file).name != output_file or Path(output_file).suffix.lower() not in {".tif", ".tiff"}:
            raise ValueError("IDL 对照 python_output_file 必须是本次运行中的 GeoTIFF 文件名。")
        tolerance = plan.get("absolute_tolerance", 0.0)
        if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or tolerance < 0:
            raise ValueError("IDL 对照 absolute_tolerance 必须是大于等于 0 的数值。")
        if mode == "classification" and tolerance != 0:
            raise ValueError("分类栅格 IDL 对照必须使用 absolute_tolerance=0。")

    @classmethod
    def _validate_idl_runner_plan(
        cls, db: Session, project_id: int, runner_type: str, parameters: dict
    ) -> None:
        if runner_type != "idl":
            return
        script_asset_id = parameters.get("idl_script_asset_id")
        # Preserve the legacy, explicit "IDL node not configured" probe.  It
        # can be created for compatibility but the runner will persist an
        # unavailable state instead of executing anything.
        if script_asset_id is None:
            return
        if isinstance(script_asset_id, bool) or not isinstance(script_asset_id, int) or script_asset_id < 1:
            raise ValueError("IDL 实验必须声明正整数 parameters.idl_script_asset_id。")
        script_asset = (
            db.query(ResearchDataAsset)
            .filter(ResearchDataAsset.id == script_asset_id, ResearchDataAsset.project_id == project_id)
            .first()
        )
        if script_asset is None:
            raise ValueError("IDL 脚本资产不存在或不属于当前项目。")
        if (script_asset.metadata_json or {}).get("asset_role") != "idl_script":
            raise ValueError("IDL 实验只能引用项目 IDL 脚本上传接口登记的资产。")
        if script_asset.source_type != "local" or not script_asset.source_uri.startswith("research://assets/"):
            raise ValueError("IDL 脚本必须是当前项目的私有本地资产。")
        entrypoint = parameters.get("idl_entrypoint")
        if entrypoint is not None and not cls._IDL_ENTRYPOINT_RE.fullmatch(str(entrypoint).strip()):
            raise ValueError("parameters.idl_entrypoint 不是合法的 IDL 过程名称。")
        prediction_output_file = parameters.get("idl_prediction_output_file")
        if prediction_output_file is not None:
            value = str(prediction_output_file).strip()
            if Path(value).name != value or Path(value).suffix.lower() not in {".tif", ".tiff"}:
                raise ValueError("parameters.idl_prediction_output_file 必须是当前运行中的 GeoTIFF 文件名。")

    @staticmethod
    def _validate_formal_validation_plan(
        db: Session, snapshot: ResearchDataSnapshot, validation_plan: dict
    ) -> None:
        """Reject a formal conclusion that has no declared independent evidence path."""
        if validation_plan.get("split") != "spatiotemporal-holdout":
            raise ValueError("正式实验的 validation_plan.split 必须为 spatiotemporal-holdout。")
        reference_asset_id = validation_plan.get("reference_asset_id")
        point_plan = validation_plan.get("sample_validation")
        if reference_asset_id is None and point_plan is None:
            raise ValueError("正式实验必须登记共网格参考栅格或独立点样本验证方案。")
        if reference_asset_id is not None:
            if not isinstance(reference_asset_id, int) or reference_asset_id not in snapshot.asset_ids_json:
                raise ValueError("正式实验的 reference_asset_id 必须属于当前冻结数据快照。")
            reference_asset = db.get(ResearchDataAsset, reference_asset_id)
            if reference_asset is None or reference_asset.project_id != snapshot.project_id:
                raise ValueError("正式实验的验证参考资产不存在或不属于当前项目。")
            if reference_asset.asset_kind != "reference":
                raise ValueError("正式实验的 reference_asset_id 必须指向 asset_kind=reference 的资产。")
        if point_plan is not None:
            if not isinstance(point_plan, dict):
                raise ValueError("正式实验的 sample_validation 必须是对象。")
            if point_plan.get("split") != "independent_test":
                raise ValueError("正式实验的点样本验证必须使用 independent_test 划分。")
            min_confidence = point_plan.get("min_confidence")
            if isinstance(min_confidence, bool) or not isinstance(min_confidence, (int, float)) or not 0 <= min_confidence <= 1:
                raise ValueError("正式实验的点样本验证必须声明 0 到 1 之间的 min_confidence。")
            if point_plan.get("require_unconflicted") is not True:
                raise ValueError("正式实验的点样本验证必须设置 require_unconflicted=true。")
            for field_name, label in (
                ("min_sample_count", "min_sample_count"),
                ("min_spatial_blocks", "min_spatial_blocks"),
                ("min_temporal_strata", "min_temporal_strata"),
                ("min_samples_per_spatial_block", "min_samples_per_spatial_block"),
                ("min_samples_per_temporal_stratum", "min_samples_per_temporal_stratum"),
            ):
                value = point_plan.get(field_name)
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError(f"正式实验的点样本验证必须声明至少为 1 的 {label}。")
