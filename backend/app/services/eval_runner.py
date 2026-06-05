from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.api.schemas import Citation
from app.services.eval_metrics import EVALUATOR_NAME, EvalReport, evaluate_answer, evaluate_retrieval
from app.services.llm_service import LlmService
from app.services.retrieve_service import RetrievalService

AnswerProvider = Callable[[str, list[Citation], dict[str, Any]], str]


class EvalRunner:
    def __init__(
        self,
        db: Session,
        knowledge_base_id: int,
        *,
        dataset_path: Path | None = None,
        answer_provider: AnswerProvider | None = None,
    ) -> None:
        self.db = db
        self.knowledge_base_id = knowledge_base_id
        self.retrieval_service = RetrievalService()
        self.llm_service = LlmService()
        self.dataset_path = dataset_path or Path(__file__).resolve().parents[2] / "tests" / "eval" / "golden_qa.json"
        self.answer_provider = answer_provider

    def load_cases(self, categories: list[str] | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        payload = json.loads(self.dataset_path.read_text(encoding="utf-8"))
        cases = payload.get("test_cases", [])
        if categories is not None:
            selected = set(categories)
            cases = [case for case in cases if case.get("category") in selected]
        return cases[:limit] if limit is not None else cases

    def run_single(self, test_case: dict[str, Any], *, top_k: int = 6, strategy: str = "hybrid_rrf_no_rerank") -> EvalReport:
        started_at = time.perf_counter()
        citations = self._retrieve_citations(test_case, top_k=top_k, strategy=strategy)
        answer = self._generate_answer(test_case, citations)
        latency_ms = (time.perf_counter() - started_at) * 1000
        retrieval = evaluate_retrieval(citations, test_case, top_k=top_k)
        answer_metrics = evaluate_answer(answer, citations, test_case)
        passed = self._passed(test_case, retrieval.hit_rate, answer_metrics.answer_relevance, answer_metrics.faithfulness)
        return EvalReport(
            test_case_id=test_case["id"],
            category=test_case["category"],
            retrieval=retrieval,
            answer=answer_metrics,
            latency_ms=latency_ms,
            passed=passed,
            strategy=strategy,
        )

    def run_all(
        self,
        categories: list[str] | None = None,
        *,
        top_k: int = 6,
        limit: int | None = None,
        strategy: str = "hybrid_rrf_no_rerank",
    ) -> list[EvalReport]:
        return [self.run_single(test_case, top_k=top_k, strategy=strategy) for test_case in self.load_cases(categories, limit)]

    def summary(self, reports: list[EvalReport]) -> dict[str, Any]:
        by_category: dict[str, list[EvalReport]] = defaultdict(list)
        by_strategy: dict[str, list[EvalReport]] = defaultdict(list)
        for report in reports:
            by_category[report.category].append(report)
            by_strategy[report.strategy].append(report)
        return {
            "evaluator": EVALUATOR_NAME,
            "total": len(reports),
            "passed": sum(1 for report in reports if report.passed),
            "pass_rate": self._avg(1.0 if report.passed else 0.0 for report in reports),
            "precision_at_k": self._avg(report.retrieval.precision_at_k for report in reports),
            "recall_at_k": self._avg(report.retrieval.recall_at_k for report in reports),
            "mrr": self._avg(report.retrieval.mrr for report in reports),
            "hit_rate": self._avg(report.retrieval.hit_rate for report in reports),
            "answer_relevance": self._avg(report.answer.answer_relevance for report in reports),
            "faithfulness": self._avg(report.answer.faithfulness for report in reports),
            "latency_ms": self._avg(report.latency_ms for report in reports),
            "by_category": {
                category: self._summary_block(items)
                for category, items in sorted(by_category.items())
            },
            "by_strategy": {
                strategy: self._summary_block(items)
                for strategy, items in sorted(by_strategy.items())
            },
        }

    def _retrieve_citations(self, test_case: dict[str, Any], *, top_k: int, strategy: str) -> list[Citation]:
        query = test_case["question"]
        if test_case.get("mode") != "code_tool":
            return self.retrieval_service.search_with_strategy(
                self.db,
                knowledge_base_id=self.knowledge_base_id,
                query=query,
                strategy=strategy,
                top_k=top_k,
            )

        if strategy in {"symbol_search", "symbol"}:
            return self.retrieval_service.search_symbol(
                self.db,
                self.knowledge_base_id,
                test_case.get("expected_symbol") or query,
                top_k=top_k,
                exact=False,
                include_dependencies=True,
            )
        if strategy in {"read_context", "symbol_context"}:
            return self.retrieval_service.read_context_by_symbol(
                self.db,
                self.knowledge_base_id,
                test_case.get("expected_symbol") or query,
                max_chunks=top_k,
                include_callees=True,
            )
        if strategy == "find_callers":
            return self.retrieval_service.find_callers(
                self.db,
                self.knowledge_base_id,
                test_case.get("expected_symbol") or query,
                top_k=top_k,
            )
        if strategy == "find_callees":
            return self.retrieval_service.find_callees(
                self.db,
                self.knowledge_base_id,
                test_case.get("expected_symbol") or query,
                top_k=top_k,
            )
        return self.retrieval_service.search_with_strategy(
            self.db,
            knowledge_base_id=self.knowledge_base_id,
            query=query,
            strategy=strategy,
            top_k=top_k,
        )

    def _generate_answer(self, test_case: dict[str, Any], citations: list[Citation]) -> str:
        if self.answer_provider is not None:
            return self.answer_provider(test_case["question"], citations, test_case)
        if test_case.get("mode") == "code_tool":
            return "\n".join(citation.excerpt for citation in citations)
        if test_case.get("category") in {"code_generation", "code_fix"}:
            return self.llm_service.generate_pro_file(self.db, test_case["question"], citations, [])
        return self.llm_service.generate_answer(self.db, test_case["question"], citations, [])

    def _summary_block(self, reports: list[EvalReport]) -> dict[str, Any]:
        return {
            "total": len(reports),
            "passed": sum(1 for report in reports if report.passed),
            "pass_rate": self._avg(1.0 if report.passed else 0.0 for report in reports),
            "precision_at_k": self._avg(report.retrieval.precision_at_k for report in reports),
            "recall_at_k": self._avg(report.retrieval.recall_at_k for report in reports),
            "hit_rate": self._avg(report.retrieval.hit_rate for report in reports),
            "mrr": self._avg(report.retrieval.mrr for report in reports),
            "answer_relevance": self._avg(report.answer.answer_relevance for report in reports),
            "faithfulness": self._avg(report.answer.faithfulness for report in reports),
            "latency_ms": self._avg(report.latency_ms for report in reports),
        }

    @staticmethod
    def _passed(test_case: dict[str, Any], hit_rate: float, answer_relevance: float, faithfulness: float) -> bool:
        category = test_case.get("category")
        if category in {"code_generation", "code_fix"}:
            return answer_relevance >= 0.3
        return hit_rate >= 1.0 and answer_relevance >= 0.4 and faithfulness >= 0.3

    @staticmethod
    def _avg(values) -> float:
        items = list(values)
        if not items:
            return 0.0
        return sum(items) / len(items)
