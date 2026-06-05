from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin
from app.api.schemas import (
    EvaluationReportResponse,
    EvaluationRunRequest,
    LangSmithEvaluateRequest,
    LangSmithSyncRequest,
)
from app.db.database import get_db
from app.db.models import User
from app.services.evaluation_service import EvaluationService
from app.services.langsmith_eval_service import LangSmithConfigError

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


@router.post("/local", response_model=EvaluationReportResponse)
def run_local_evaluation(
    payload: EvaluationRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> EvaluationReportResponse:
    try:
        report = EvaluationService(db).run_local_evaluation(
            knowledge_base_id=payload.knowledge_base_id,
            owner_user_id=current_user.id,
            categories=payload.categories,
            strategies=payload.strategies,
            top_k=payload.top_k,
            limit=payload.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return EvaluationReportResponse.model_validate(report)


@router.post("/langsmith/sync", response_model=EvaluationReportResponse)
def sync_langsmith_dataset(
    payload: LangSmithSyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> EvaluationReportResponse:
    try:
        report = EvaluationService(db).sync_langsmith_dataset(
            created_by_user_id=current_user.id,
            dataset_name=payload.dataset,
            dry_run=payload.dry_run,
        )
    except LangSmithConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EvaluationReportResponse.model_validate(report)


@router.post("/langsmith/evaluate", response_model=EvaluationReportResponse)
def run_langsmith_evaluation(
    payload: LangSmithEvaluateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> EvaluationReportResponse:
    try:
        report = EvaluationService(db).run_langsmith_evaluation(
            knowledge_base_id=payload.knowledge_base_id,
            owner_user_id=current_user.id,
            dataset_name=payload.dataset,
            strategies=payload.strategies,
            top_k=payload.top_k,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LangSmithConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EvaluationReportResponse.model_validate(report)


@router.get("/reports", response_model=list[EvaluationReportResponse])
def list_evaluation_reports(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> list[EvaluationReportResponse]:
    reports = EvaluationService(db).list_reports(current_user.id)
    return [EvaluationReportResponse.model_validate(report) for report in reports]


@router.get("/reports/{report_id}", response_model=EvaluationReportResponse)
def get_evaluation_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> EvaluationReportResponse:
    try:
        report = EvaluationService(db).get_report(report_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return EvaluationReportResponse.model_validate(report)
