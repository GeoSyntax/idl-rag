from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_app_settings
from app.db.models import EvaluationReport, KnowledgeBase
from app.services.eval_runner import EvalRunner
from app.services.langsmith_eval_service import DEFAULT_STRATEGIES, LangSmithEvalService


class EvaluationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_app_settings()

    def run_local_evaluation(
        self,
        *,
        knowledge_base_id: int,
        owner_user_id: int,
        categories: list[str] | None = None,
        strategies: list[str] | None = None,
        top_k: int = 6,
        limit: int | None = None,
    ) -> EvaluationReport:
        self._get_owned_knowledge_base(knowledge_base_id, owner_user_id)
        runner = EvalRunner(self.db, knowledge_base_id)
        target_strategies = self._normalize_strategies(strategies)
        reports = [
            report
            for strategy in target_strategies
            for report in runner.run_all(categories, top_k=top_k, limit=limit, strategy=strategy)
        ]
        summary = runner.summary(reports)
        summary["top_k"] = top_k
        summary["strategy_count"] = len(target_strategies)
        comparison = self._rerank_comparison(summary.get("by_strategy", {}))
        if comparison:
            summary["rerank_comparison"] = comparison
        payload = {
            "knowledge_base_id": knowledge_base_id,
            "categories": categories,
            "strategies": target_strategies,
            "top_k": top_k,
            "limit": limit,
            "summary": summary,
            "reports": [asdict(report) for report in reports],
        }
        report_path = self._write_report("rag_eval", payload)
        payload["report_path"] = report_path.as_posix()
        return self._save_report(
            knowledge_base_id=knowledge_base_id,
            created_by_user_id=owner_user_id,
            report_type="local",
            strategy=",".join(target_strategies),
            dataset="golden_qa",
            status="completed",
            summary_json=summary,
            report_json=payload,
            report_path=report_path.as_posix(),
        )

    def sync_langsmith_dataset(
        self,
        *,
        created_by_user_id: int,
        dataset_name: str | None = None,
        dry_run: bool = True,
    ) -> EvaluationReport:
        service = LangSmithEvalService(self.db)
        result = service.sync_dataset(dataset_name=dataset_name, dry_run=dry_run)
        return self._save_report(
            knowledge_base_id=None,
            created_by_user_id=created_by_user_id,
            report_type="langsmith_sync",
            strategy=None,
            dataset=result.get("dataset"),
            status="completed",
            summary_json={
                "total_cases": result.get("total_cases", 0),
                "created": result.get("created", 0),
                "dry_run": result.get("dry_run", False),
            },
            report_json=result,
            report_path=None,
        )

    def run_langsmith_evaluation(
        self,
        *,
        knowledge_base_id: int,
        owner_user_id: int,
        dataset_name: str | None = None,
        strategies: list[str] | None = None,
        top_k: int = 6,
    ) -> EvaluationReport:
        self._get_owned_knowledge_base(knowledge_base_id, owner_user_id)
        target_strategies = strategies or DEFAULT_STRATEGIES
        service = LangSmithEvalService(self.db)
        result = service.evaluate_strategies(
            knowledge_base_id=knowledge_base_id,
            strategies=target_strategies,
            dataset_name=dataset_name,
            top_k=top_k,
        )
        summary = {
            "strategy_count": len(target_strategies),
            "report_count": len(result.get("reports", [])),
            "experiments": result.get("experiments", {}),
        }
        return self._save_report(
            knowledge_base_id=knowledge_base_id,
            created_by_user_id=owner_user_id,
            report_type="langsmith_eval",
            strategy=",".join(target_strategies),
            dataset=result.get("dataset"),
            status="completed",
            summary_json=summary,
            report_json=result,
            report_path=result.get("report_path"),
        )

    def list_reports(self, owner_user_id: int) -> list[EvaluationReport]:
        return (
            self.db.query(EvaluationReport)
            .outerjoin(KnowledgeBase, KnowledgeBase.id == EvaluationReport.knowledge_base_id)
            .filter(
                (EvaluationReport.knowledge_base_id.is_(None))
                | (KnowledgeBase.owner_user_id == owner_user_id)
            )
            .order_by(EvaluationReport.created_at.desc(), EvaluationReport.id.desc())
            .limit(100)
            .all()
        )

    def get_report(self, report_id: int, owner_user_id: int) -> EvaluationReport:
        report = self.db.get(EvaluationReport, report_id)
        if report is None:
            raise ValueError("评测报告不存在。")
        if report.knowledge_base_id is not None:
            self._get_owned_knowledge_base(report.knowledge_base_id, owner_user_id)
        return report

    def compare_reports(self, left_id: int, right_id: int, owner_user_id: int) -> dict:
        left = self.get_report(left_id, owner_user_id)
        right = self.get_report(right_id, owner_user_id)
        left_summary = left.summary_json or {}
        right_summary = right.summary_json or {}
        metric_keys = [
            "total", "passed", "pass_rate", "hit_rate",
            "precision_at_k", "recall_at_k", "mrr",
            "answer_relevance", "faithfulness", "latency_ms",
        ]
        deltas: dict[str, dict[str, float]] = {}
        for key in metric_keys:
            lv = left_summary.get(key)
            rv = right_summary.get(key)
            if isinstance(lv, int | float) and isinstance(rv, int | float):
                deltas[key] = {"left": float(lv), "right": float(rv), "delta": float(rv - lv)}
        return {
            "left": {
                "id": left.id,
                "report_type": left.report_type,
                "strategy": left.strategy,
                "created_at": str(left.created_at),
            },
            "right": {
                "id": right.id,
                "report_type": right.report_type,
                "strategy": right.strategy,
                "created_at": str(right.created_at),
            },
            "deltas": deltas,
        }

    def _get_owned_knowledge_base(self, knowledge_base_id: int, owner_user_id: int) -> KnowledgeBase:
        knowledge_base = self.db.get(KnowledgeBase, knowledge_base_id)
        if knowledge_base is None or knowledge_base.owner_user_id != owner_user_id:
            raise ValueError("知识库不存在。")
        return knowledge_base

    @staticmethod
    def _normalize_strategies(strategies: list[str] | None) -> list[str]:
        normalized = list(dict.fromkeys((strategy or "").strip().lower() for strategy in strategies or [] if strategy and strategy.strip()))
        return normalized or ["hybrid_rrf_no_rerank", "hybrid_rrf"]

    @staticmethod
    def _rerank_comparison(by_strategy: dict[str, Any]) -> dict[str, float] | None:
        baseline = by_strategy.get("hybrid_rrf_no_rerank")
        rerank = by_strategy.get("hybrid_rrf")
        if not isinstance(baseline, dict) or not isinstance(rerank, dict):
            return None
        result: dict[str, float] = {}
        for key in ("hit_rate", "precision_at_k", "recall_at_k", "mrr", "latency_ms"):
            baseline_value = baseline.get(key)
            rerank_value = rerank.get(key)
            if isinstance(baseline_value, int | float) and isinstance(rerank_value, int | float):
                result[f"{key}_delta"] = float(rerank_value) - float(baseline_value)
        return result or None

    def _save_report(
        self,
        *,
        knowledge_base_id: int | None,
        created_by_user_id: int,
        report_type: str,
        strategy: str | None,
        dataset: str | None,
        status: str,
        summary_json: dict,
        report_json: dict,
        report_path: str | None,
        error_message: str | None = None,
    ) -> EvaluationReport:
        report = EvaluationReport(
            knowledge_base_id=knowledge_base_id,
            created_by_user_id=created_by_user_id,
            report_type=report_type,
            strategy=strategy,
            dataset=dataset,
            status=status,
            summary_json=summary_json,
            report_json=report_json,
            report_path=report_path,
            error_message=error_message,
        )
        self.db.add(report)
        self.db.commit()
        self.db.refresh(report)
        return report

    def _write_report(self, prefix: str, payload: dict[str, Any]) -> Path:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        report_path = self.settings.logs_dir / f"{prefix}_{timestamp}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return report_path
