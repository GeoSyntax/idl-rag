import csv
import io
import json
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.schemas import (
    EvidenceCardCreate,
    EvidenceCardResponse,
    FormulaSpecCreate,
    FormulaSpecResponse,
    ResearchLiteratureCandidateImport,
    ResearchLiteratureRagImport,
    ResearchLiteratureRagImportResponse,
    ResearchLiteratureNetworkRequest,
    ResearchLiteratureNetworkResponse,
    ResearchLiteratureSearchResponse,
    ResearchStacCandidateImport,
    ResearchStacDownloadRequest,
    ResearchStacDownloadResponse,
    ResearchStacSearchRequest,
    ResearchStacSearchResponse,
    ResearchKnowledgeSourceCreate,
    ResearchKnowledgeSourceResponse,
    ResearchRagSearchResponse,
    ResearchDataAssetCreate,
    ResearchDataAssetResponse,
    ResearchRasterStackCreate,
    ResearchDataSnapshotCreate,
    ResearchDataSnapshotResponse,
    ResearchExperimentCreate,
    ResearchExperimentResponse,
    ResearchParameterSweepCreate,
    ResearchGeeFetchRequest,
    ResearchGeeFetchResponse,
    ResearchProjectCreate,
    ResearchProjectMemberCreate,
    ResearchProjectMemberResponse,
    ResearchProtocolDraftRequest,
    ResearchProtocolDraftResponse,
    ResearchProtocolEvidenceMapDraftRequest,
    ResearchProtocolEvidenceMapDraftResponse,
    ResearchProtocolReadinessResponse,
    ResearchProtocolRevisionResponse,
    ResearchProjectResponse,
    ResearchProjectUpdate,
    ResearchRunResponse,
    ResearchRunComparisonRequest,
    ResearchRunComparisonResponse,
    ResearchRunReproducibilityRequest,
    ResearchRunReproducibilityResponse,
    ResearchRunVerificationResponse,
    ResearchValidationSampleCreate,
    ResearchValidationSampleImportResponse,
    ResearchValidationSampleResponse,
)
from app.db.database import get_db
from app.db.models import User
from app.services.research_service import ResearchService
from app.services.research_asset_storage import ResearchAssetStorage
from app.services.research_literature_search import LiteratureProviderUnavailableError, ResearchLiteratureSearchService
from app.services.research_stac_service import ResearchStacService
from app.services.research_raster_stack_service import ResearchRasterStackService
from app.services.research_run_service import ResearchRunService
from app.services.research_rag_service import ResearchRagService
from app.services.research_protocol_evidence_service import ResearchProtocolEvidenceMapService
from app.services.research_protocol_readiness_service import ResearchProtocolReadinessService
from app.services.gee_service import GeeService

router = APIRouter(prefix="/research/projects", tags=["research-projects"])
service = ResearchService()
asset_storage = ResearchAssetStorage()
run_service = ResearchRunService()
literature_search_service = ResearchLiteratureSearchService()
stac_service = ResearchStacService()
raster_stack_service = ResearchRasterStackService()
gee_service = GeeService()
research_rag_service = ResearchRagService()
protocol_evidence_map_service = ResearchProtocolEvidenceMapService(research_rag_service)
protocol_readiness_service = ResearchProtocolReadinessService()

_VALIDATION_SAMPLE_CSV_REQUIRED_COLUMNS = {
    "longitude",
    "latitude",
    "label",
    "observed_at",
    "annotator",
    "confidence",
    "split",
    "spatial_block",
    "temporal_stratum",
    "source_note",
}
_VALIDATION_SAMPLE_CSV_MAX_BYTES = 10 * 1024 * 1024
_VALIDATION_SAMPLE_CSV_MAX_ROWS = 10_000


def _not_found(exc: LookupError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _invalid_request(exc: ValueError | IntegrityError, db: Session) -> HTTPException:
    db.rollback()
    if isinstance(exc, IntegrityError):
        detail = "名称或版本已存在，请使用新的项目、快照或公式版本。"
    else:
        detail = str(exc)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


async def _read_validation_sample_csv(
    file: UploadFile,
    data_snapshot_id: int,
    default_source_asset_id: int | None,
) -> list[ResearchValidationSampleCreate]:
    """Parse a bounded UTF-8 CSV without persisting it as a second private copy."""
    filename = file.filename or ""
    if not filename.lower().endswith(".csv"):
        raise ValueError("验证样本批量导入只接受 .csv 文件。")
    content = await file.read()
    if not content:
        raise ValueError("验证样本 CSV 为空。")
    if len(content) > _VALIDATION_SAMPLE_CSV_MAX_BYTES:
        raise ValueError("验证样本 CSV 超过 10 MiB 限制。")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("验证样本 CSV 必须使用 UTF-8 编码。") from exc

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("验证样本 CSV 缺少表头。")
    fieldnames = {field.strip() for field in reader.fieldnames if field is not None}
    missing_columns = sorted(_VALIDATION_SAMPLE_CSV_REQUIRED_COLUMNS - fieldnames)
    if missing_columns:
        raise ValueError(f"验证样本 CSV 缺少必需列：{', '.join(missing_columns)}。")

    payloads: list[ResearchValidationSampleCreate] = []
    for line_number, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(f"验证样本 CSV 第 {line_number} 行的列数与表头不一致。")
        normalized = {
            key.strip(): value.strip()
            for key, value in row.items()
            if key is not None and value is not None and value.strip()
        }
        if not normalized:
            continue
        metadata_json = normalized.pop("metadata_json", None)
        if metadata_json is not None:
            try:
                normalized["metadata"] = json.loads(metadata_json)
            except json.JSONDecodeError as exc:
                raise ValueError(f"验证样本 CSV 第 {line_number} 行的 metadata_json 不是合法 JSON。") from exc
        if "label" in normalized:
            try:
                normalized["label"] = int(normalized["label"])
            except ValueError as exc:
                raise ValueError(f"验证样本 CSV 第 {line_number} 行字段 label 必须为 0 或 1。") from exc
        normalized["data_snapshot_id"] = data_snapshot_id
        if "source_asset_id" not in normalized and default_source_asset_id is not None:
            normalized["source_asset_id"] = default_source_asset_id
        try:
            payloads.append(ResearchValidationSampleCreate.model_validate(normalized))
        except ValidationError as exc:
            first_error = exc.errors()[0]
            location = ".".join(str(part) for part in first_error["loc"])
            raise ValueError(f"验证样本 CSV 第 {line_number} 行字段 {location} 无效：{first_error['msg']}。") from exc
        if len(payloads) > _VALIDATION_SAMPLE_CSV_MAX_ROWS:
            raise ValueError("验证样本 CSV 超过 10000 行限制。")
    if not payloads:
        raise ValueError("验证样本 CSV 不包含可导入的记录。")
    return payloads


@router.get("", response_model=list[ResearchProjectResponse])
def list_projects(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ResearchProjectResponse]:
    return service.list_projects(db, current_user.id)


@router.post("", response_model=ResearchProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ResearchProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchProjectResponse:
    try:
        return service.create_project(db, payload, current_user.id)
    except IntegrityError as exc:
        raise _invalid_request(exc, db) from exc


@router.get("/{project_id}", response_model=ResearchProjectResponse)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchProjectResponse:
    try:
        return service.get_project(db, project_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.patch("/{project_id}", response_model=ResearchProjectResponse)
def update_project(
    project_id: int,
    payload: ResearchProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchProjectResponse:
    try:
        return service.update_project(db, project_id, payload, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except IntegrityError as exc:
        raise _invalid_request(exc, db) from exc


@router.get("/{project_id}/members", response_model=list[ResearchProjectMemberResponse])
def list_project_members(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ResearchProjectMemberResponse]:
    try:
        return service.list_project_members(db, project_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/{project_id}/members",
    response_model=ResearchProjectMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_project_member(
    project_id: int,
    payload: ResearchProjectMemberCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchProjectMemberResponse:
    try:
        return service.add_project_member(db, project_id, payload, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except (ValueError, IntegrityError) as exc:
        raise _invalid_request(exc, db) from exc


@router.delete("/{project_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_project_member(
    project_id: int,
    member_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    try:
        service.remove_project_member(db, project_id, member_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.get("/{project_id}/rag-sources", response_model=list[ResearchKnowledgeSourceResponse])
def list_research_rag_sources(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ResearchKnowledgeSourceResponse]:
    try:
        return research_rag_service.list_sources(db, project_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/{project_id}/rag-sources",
    response_model=ResearchKnowledgeSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_research_rag_source(
    project_id: int,
    payload: ResearchKnowledgeSourceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchKnowledgeSourceResponse:
    try:
        return research_rag_service.add_source(db, project_id, payload, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except (ValueError, IntegrityError) as exc:
        raise _invalid_request(exc, db) from exc


@router.get("/{project_id}/rag-search", response_model=ResearchRagSearchResponse)
def search_research_rag(
    project_id: int,
    query: str = Query(min_length=2, max_length=1000),
    category: str = Query(default="all"),
    top_k: int = Query(default=6, ge=1, le=20),
    strategy: str = Query(default="hybrid_rrf_no_rerank", min_length=1, max_length=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchRagSearchResponse:
    try:
        return research_rag_service.search(
            db, project_id, current_user.id, query, category, top_k, strategy
        )
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.post("/{project_id}/protocol-draft", response_model=ResearchProtocolDraftResponse)
def draft_project_protocol(
    project_id: int,
    payload: ResearchProtocolDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchProtocolDraftResponse:
    try:
        return service.draft_protocol(db, project_id, payload, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post("/{project_id}/protocol-evidence-map-draft", response_model=ResearchProtocolEvidenceMapDraftResponse)
def draft_project_protocol_with_evidence_map(
    project_id: int,
    payload: ResearchProtocolEvidenceMapDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchProtocolEvidenceMapDraftResponse:
    try:
        return protocol_evidence_map_service.draft(db, project_id, payload, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.get("/{project_id}/protocol-readiness", response_model=ResearchProtocolReadinessResponse)
def check_project_protocol_readiness(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchProtocolReadinessResponse:
    try:
        return protocol_readiness_service.check(db, project_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.get("/{project_id}/protocol-revisions", response_model=list[ResearchProtocolRevisionResponse])
def list_project_protocol_revisions(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ResearchProtocolRevisionResponse]:
    try:
        return service.list_protocol_revisions(db, project_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.get("/{project_id}/literature-search", response_model=ResearchLiteratureSearchResponse)
def search_literature(
    project_id: int,
    query: str = Query(min_length=3, max_length=500),
    rows: int = Query(default=8, ge=1, le=20),
    provider: str = Query(default="crossref", min_length=1, max_length=30),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchLiteratureSearchResponse:
    try:
        return literature_search_service.search(db, project_id, current_user.id, query, rows, provider)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except LiteratureProviderUnavailableError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.post(
    "/{project_id}/literature-search/evidence-cards",
    response_model=EvidenceCardResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_literature_candidate(
    project_id: int,
    payload: ResearchLiteratureCandidateImport,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvidenceCardResponse:
    try:
        return literature_search_service.import_candidate_as_evidence_card(
            db, project_id, current_user.id, payload
        )
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.post(
    "/{project_id}/literature-search/rag-import",
    response_model=ResearchLiteratureRagImportResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_literature_candidate_to_rag(
    project_id: int,
    payload: ResearchLiteratureRagImport,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchLiteratureRagImportResponse:
    try:
        return literature_search_service.import_candidate_to_rag(
            db, project_id, current_user.id, payload
        )
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.post(
    "/{project_id}/literature-search/semantic-scholar-network",
    response_model=ResearchLiteratureNetworkResponse,
)
def expand_semantic_scholar_network(
    project_id: int,
    payload: ResearchLiteratureNetworkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchLiteratureNetworkResponse:
    try:
        return literature_search_service.expand_semantic_scholar_network(
            db, project_id, current_user.id, payload
        )
    except LookupError as exc:
        raise _not_found(exc) from exc
    except LiteratureProviderUnavailableError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.post(
    "/{project_id}/stac-search",
    response_model=ResearchStacSearchResponse,
)
def search_public_stac(
    project_id: int,
    payload: ResearchStacSearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchStacSearchResponse:
    try:
        return stac_service.search(db, project_id, current_user.id, payload)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.post(
    "/{project_id}/stac-search/import",
    response_model=ResearchDataAssetResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_public_stac_reference(
    project_id: int,
    payload: ResearchStacCandidateImport,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchDataAssetResponse:
    try:
        return stac_service.import_reference(db, project_id, current_user.id, payload)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.post(
    "/{project_id}/stac-search/download",
    response_model=ResearchStacDownloadResponse,
    status_code=status.HTTP_201_CREATED,
)
def download_public_stac_asset(
    project_id: int,
    payload: ResearchStacDownloadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchStacDownloadResponse:
    try:
        return stac_service.download_asset(db, project_id, current_user.id, payload)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.post("/{project_id}/gee-fetch", response_model=ResearchGeeFetchResponse, status_code=status.HTTP_201_CREATED)
def fetch_gee_research_asset(
    project_id: int,
    payload: ResearchGeeFetchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchGeeFetchResponse:
    try:
        return gee_service.fetch_research_asset(db, project_id, payload, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.get("/{project_id}/data-assets", response_model=list[ResearchDataAssetResponse])
def list_data_assets(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ResearchDataAssetResponse]:
    try:
        return service.list_data_assets(db, project_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/{project_id}/data-assets",
    response_model=ResearchDataAssetResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_data_asset(
    project_id: int,
    payload: ResearchDataAssetCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchDataAssetResponse:
    try:
        return service.create_data_asset(db, project_id, payload, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/{project_id}/data-assets/upload",
    response_model=ResearchDataAssetResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_data_asset(
    project_id: int,
    file: UploadFile = File(...),
    asset_kind: str = Form("raster"),
    name: str | None = Form(default=None),
    metadata_json: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchDataAssetResponse:
    try:
        service.get_project(db, project_id, current_user.id)
        supplied_metadata: dict[str, object] = {}
        if metadata_json:
            if len(metadata_json) > 16_000:
                raise ValueError("上传资产 provenance metadata 不能超过 16,000 个字符。")
            try:
                parsed_metadata = json.loads(metadata_json)
            except json.JSONDecodeError as exc:
                raise ValueError("上传资产 metadata_json 必须是合法 JSON 对象。") from exc
            if not isinstance(parsed_metadata, dict):
                raise ValueError("上传资产 metadata_json 必须是 JSON 对象。")
            supplied_metadata = parsed_metadata
        source_uri, sha256, size = asset_storage.store_upload(project_id, file)
        metadata: dict[str, object] = {
            "uploaded_size": size,
            "original_file_name": file.filename or "",
            **supplied_metadata,
        }
        payload = ResearchDataAssetCreate(
            name=name or file.filename or "uploaded_asset",
            asset_kind=asset_kind,
            source_type="local",
            source_uri=source_uri,
            sha256=sha256,
            metadata=metadata,
        )
        return service.create_data_asset(db, project_id, payload, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.post(
    "/{project_id}/idl-scripts/upload",
    response_model=ResearchDataAssetResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_idl_script_asset(
    project_id: int,
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchDataAssetResponse:
    """Register a private project-owned ``.pro`` source for explicit IDL runs."""
    try:
        service.get_project(db, project_id, current_user.id)
        source_uri, sha256, size = asset_storage.store_idl_script_upload(project_id, file)
        payload = ResearchDataAssetCreate(
            name=name or file.filename or "research_script.pro",
            asset_kind="derived",
            source_type="local",
            source_uri=source_uri,
            sha256=sha256,
            metadata={
                "asset_role": "idl_script",
                "uploaded_size": size,
                "original_file_name": file.filename or "",
            },
        )
        return service.create_data_asset(db, project_id, payload, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.post(
    "/{project_id}/data-assets/stack",
    response_model=ResearchDataAssetResponse,
    status_code=status.HTTP_201_CREATED,
)
def stack_research_raster_assets(
    project_id: int,
    payload: ResearchRasterStackCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchDataAssetResponse:
    try:
        return raster_stack_service.create_stack(db, project_id, current_user.id, payload)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.get("/{project_id}/data-snapshots", response_model=list[ResearchDataSnapshotResponse])
def list_data_snapshots(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ResearchDataSnapshotResponse]:
    try:
        return service.list_data_snapshots(db, project_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/{project_id}/data-snapshots",
    response_model=ResearchDataSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_data_snapshot(
    project_id: int,
    payload: ResearchDataSnapshotCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchDataSnapshotResponse:
    try:
        return service.create_data_snapshot(db, project_id, payload, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except (ValueError, IntegrityError) as exc:
        raise _invalid_request(exc, db) from exc


@router.get("/{project_id}/validation-samples", response_model=list[ResearchValidationSampleResponse])
def list_validation_samples(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ResearchValidationSampleResponse]:
    try:
        return service.list_validation_samples(db, project_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/{project_id}/validation-samples",
    response_model=ResearchValidationSampleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_validation_sample(
    project_id: int,
    payload: ResearchValidationSampleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchValidationSampleResponse:
    try:
        return service.create_validation_sample(db, project_id, payload, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.post(
    "/{project_id}/validation-samples/import",
    response_model=ResearchValidationSampleImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_validation_samples(
    project_id: int,
    file: UploadFile = File(...),
    data_snapshot_id: int = Form(...),
    source_asset_id: int | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchValidationSampleImportResponse:
    try:
        payloads = await _read_validation_sample_csv(file, data_snapshot_id, source_asset_id)
        return service.import_validation_samples(db, project_id, data_snapshot_id, payloads, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.get("/{project_id}/evidence-cards", response_model=list[EvidenceCardResponse])
def list_evidence_cards(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[EvidenceCardResponse]:
    try:
        return service.list_evidence_cards(db, project_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/{project_id}/evidence-cards",
    response_model=EvidenceCardResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_evidence_card(
    project_id: int,
    payload: EvidenceCardCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EvidenceCardResponse:
    try:
        return service.create_evidence_card(db, project_id, payload, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.get("/{project_id}/formula-specs", response_model=list[FormulaSpecResponse])
def list_formula_specs(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[FormulaSpecResponse]:
    try:
        return service.list_formula_specs(db, project_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/{project_id}/formula-specs",
    response_model=FormulaSpecResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_formula_spec(
    project_id: int,
    payload: FormulaSpecCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FormulaSpecResponse:
    try:
        return service.create_formula_spec(db, project_id, payload, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except (ValueError, IntegrityError) as exc:
        raise _invalid_request(exc, db) from exc


@router.get("/{project_id}/experiments", response_model=list[ResearchExperimentResponse])
def list_experiments(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ResearchExperimentResponse]:
    try:
        return service.list_experiments(db, project_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/{project_id}/experiments",
    response_model=ResearchExperimentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_experiment(
    project_id: int,
    payload: ResearchExperimentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchExperimentResponse:
    try:
        return service.create_experiment(db, project_id, payload, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.get(
    "/{project_id}/experiments/{experiment_id}/runs",
    response_model=list[ResearchRunResponse],
)
def list_experiment_runs(
    project_id: int,
    experiment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ResearchRunResponse]:
    try:
        return run_service.list_runs(db, project_id, experiment_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/{project_id}/experiments/{experiment_id}/runs",
    response_model=ResearchRunResponse,
    status_code=status.HTTP_201_CREATED,
)
def start_experiment_run(
    project_id: int,
    experiment_id: int,
    mode: Literal["sync", "queue"] = Query(default="sync"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchRunResponse:
    try:
        if mode == "queue":
            return run_service.queue_run(db, project_id, experiment_id, current_user.id)
        return run_service.start_run(db, project_id, experiment_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.post(
    "/{project_id}/experiments/{experiment_id}/sweeps",
    response_model=ResearchRunResponse,
    status_code=status.HTTP_201_CREATED,
)
def start_parameter_sweep(
    project_id: int,
    experiment_id: int,
    payload: ResearchParameterSweepCreate,
    mode: Literal["sync", "queue"] = Query(default="sync"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchRunResponse:
    try:
        return run_service.start_parameter_sweep(
            db,
            project_id,
            experiment_id,
            current_user.id,
            payload,
            mode=mode,
        )
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.post(
    "/{project_id}/experiments/{experiment_id}/runs/{run_id}/cancel",
    response_model=ResearchRunResponse,
)
def cancel_experiment_run(
    project_id: int,
    experiment_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchRunResponse:
    try:
        return run_service.cancel_run(db, project_id, experiment_id, run_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.post(
    "/{project_id}/experiments/{experiment_id}/runs/{run_id}/retry",
    response_model=ResearchRunResponse,
    status_code=status.HTTP_201_CREATED,
)
def retry_experiment_run(
    project_id: int,
    experiment_id: int,
    run_id: int,
    mode: Literal["sync", "queue"] = Query(default="queue"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchRunResponse:
    try:
        return run_service.retry_run(
            db,
            project_id,
            experiment_id,
            run_id,
            current_user.id,
            mode=mode,
        )
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.get(
    "/{project_id}/experiments/{experiment_id}/runs/{run_id}/verification",
    response_model=ResearchRunVerificationResponse,
)
def verify_research_run(
    project_id: int,
    experiment_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchRunVerificationResponse:
    try:
        return run_service.verify_run_integrity(db, project_id, experiment_id, run_id, current_user.id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/{project_id}/experiments/{experiment_id}/runs/{run_id}/reproducibility",
    response_model=ResearchRunReproducibilityResponse,
)
def compare_research_run_reproducibility(
    project_id: int,
    experiment_id: int,
    run_id: int,
    payload: ResearchRunReproducibilityRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchRunReproducibilityResponse:
    try:
        return run_service.compare_reproducibility(
            db,
            project_id,
            experiment_id,
            run_id,
            current_user.id,
            reference_run_id=payload.reference_run_id,
            absolute_tolerance=payload.absolute_tolerance,
            relative_tolerance=payload.relative_tolerance,
        )
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.post(
    "/{project_id}/experiments/{experiment_id}/runs/{run_id}/comparison",
    response_model=ResearchRunComparisonResponse,
)
def compare_formal_research_runs(
    project_id: int,
    experiment_id: int,
    run_id: int,
    payload: ResearchRunComparisonRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchRunComparisonResponse:
    try:
        return run_service.compare_formal_runs(
            db,
            project_id,
            experiment_id,
            run_id,
            current_user.id,
            reference_run_id=payload.reference_run_id,
        )
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid_request(exc, db) from exc


@router.get("/{project_id}/experiments/{experiment_id}/runs/{run_id}/outputs/{file_name}")
def download_run_output(
    project_id: int,
    experiment_id: int,
    run_id: int,
    file_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    try:
        path, media_type = run_service.get_output_file(
            db, project_id, experiment_id, run_id, file_name, current_user.id
        )
    except LookupError as exc:
        raise _not_found(exc) from exc
    return FileResponse(path, media_type=media_type, filename=file_name)
