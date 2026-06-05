from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.api.schemas import Citation
from app.core.config import get_app_settings
from app.db.models import KnowledgeBase
from app.services.llm_service import LlmService
from app.services.retrieve_service import RetrievalService
from app.services.eval_metrics import evaluate_answer, evaluate_retrieval
from app.services.settings_service import get_runtime_settings


DEFAULT_STRATEGIES = [
    "hybrid_rrf_no_rerank",
    "hybrid_rrf",
    "fts_only",
    "vector_only",
    "multi_query",
    "hyde",
    "rag_fusion",
    "parent_child",
    "dependency_graphrag",
]


class LangSmithConfigError(RuntimeError):
    pass


class LangSmithEvalService:
    def __init__(self, db: Session, *, dataset_path: Path | None = None) -> None:
        self.db = db
        self.settings = get_app_settings()
        self.dataset_path = dataset_path or Path(__file__).resolve().parents[2] / "tests" / "eval" / "golden_qa.json"
        self.retrieval_service = RetrievalService()
        self.llm_service = LlmService()

    def sync_dataset(self, *, dataset_name: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        runtime_settings = get_runtime_settings(self.db)
        target_dataset_name = dataset_name or runtime_settings.langsmith_dataset
        cases = self.load_cases()
        if dry_run:
            return {"dataset": target_dataset_name, "total_cases": len(cases), "created": 0, "dry_run": True}

        client = self._client()
        dataset = self._get_or_create_dataset(client, target_dataset_name)
        existing_ids = self._existing_example_ids(client, dataset.id)
        missing_cases = [case for case in cases if case["id"] not in existing_ids]
        if missing_cases:
            client.create_examples(
                inputs=[self._example_inputs(case) for case in missing_cases],
                outputs=[self._example_outputs(case) for case in missing_cases],
                metadata=[self._example_metadata(case) for case in missing_cases],
                dataset_id=dataset.id,
            )
        return {
            "dataset": target_dataset_name,
            "dataset_id": str(dataset.id),
            "total_cases": len(cases),
            "created": len(missing_cases),
            "existing": len(existing_ids),
            "dry_run": False,
        }

    def evaluate_strategies(
        self,
        *,
        knowledge_base_id: int,
        strategies: list[str] | None = None,
        dataset_name: str | None = None,
        top_k: int = 6,
    ) -> dict[str, Any]:
        runtime_settings = get_runtime_settings(self.db)
        if not runtime_settings.langsmith_api_key:
            raise LangSmithConfigError("请先在 Settings 中配置 LangSmith API Key。")
        target_dataset_name = dataset_name or runtime_settings.langsmith_dataset
        target_strategies = strategies or DEFAULT_STRATEGIES
        self._configure_langsmith_env(runtime_settings)

        from langsmith import evaluate, traceable

        local_reports: list[dict[str, Any]] = []
        experiment_names: dict[str, str] = {}
        for strategy in target_strategies:
            @traceable(name=f"idl_rag_{strategy}", run_type="chain", project_name=runtime_settings.langsmith_project)
            def target(inputs: dict[str, Any]) -> dict[str, Any]:
                question = inputs["question"]
                citations = self.retrieval_service.search_with_strategy(
                    self.db,
                    knowledge_base_id,
                    question,
                    strategy,
                    top_k=inputs.get("top_k") or top_k,
                )
                if inputs.get("category") in {"code_generation", "code_fix"}:
                    answer = self.llm_service.generate_pro_file(self.db, question, citations, [])
                else:
                    answer = self.llm_service.generate_answer(self.db, question, citations, [])
                return {
                    "answer": answer,
                    "citations": [citation.model_dump() for citation in citations],
                    "strategy": strategy,
                }

            results = evaluate(
                target,
                data=target_dataset_name,
                evaluators=self._evaluators(),
                experiment_prefix=f"idl-rag-{strategy}",
                metadata={"strategy": strategy, "top_k": top_k, "version": "retrieval-v2"},
                max_concurrency=1,
            )
            experiment_names[strategy] = getattr(results, "experiment_name", "")
            for row in results:
                run = row.get("run")
                example = row.get("example")
                if run is not None and example is not None:
                    local_reports.append(self._local_report_from_run(strategy, run, example))

        payload = {
            "dataset": target_dataset_name,
            "knowledge_base_id": knowledge_base_id,
            "strategies": target_strategies,
            "experiments": experiment_names,
            "reports": local_reports,
        }
        report_path = self._write_local_report(payload)
        payload["report_path"] = report_path.as_posix()
        return payload

    def load_cases(self) -> list[dict[str, Any]]:
        payload = json.loads(self.dataset_path.read_text(encoding="utf-8"))
        return payload.get("test_cases", [])

    def _client(self):
        runtime_settings = get_runtime_settings(self.db)
        if not runtime_settings.langsmith_api_key:
            raise LangSmithConfigError("请先在 Settings 中配置 LangSmith API Key。")
        from langsmith import Client

        return Client(api_key=runtime_settings.langsmith_api_key, api_url=runtime_settings.langsmith_endpoint)

    def _get_or_create_dataset(self, client, dataset_name: str):
        try:
            return client.read_dataset(dataset_name=dataset_name)
        except Exception:  # noqa: BLE001
            return client.create_dataset(
                dataset_name,
                description="IDL-RAG Golden QA dataset for retrieval and answer evaluation.",
                data_type="kv",
            )

    @staticmethod
    def _existing_example_ids(client, dataset_id) -> set[str]:
        try:
            examples = client.list_examples(dataset_id=dataset_id)
        except Exception:  # noqa: BLE001
            return set()
        ids: set[str] = set()
        for example in examples:
            metadata = getattr(example, "metadata", None) or {}
            case_id = metadata.get("id")
            if case_id:
                ids.add(case_id)
        return ids

    @staticmethod
    def _example_inputs(case: dict[str, Any]) -> dict[str, Any]:
        return {"question": case["question"], "category": case["category"], "top_k": 6}

    @staticmethod
    def _example_outputs(case: dict[str, Any]) -> dict[str, Any]:
        return {
            "expected_keywords": case.get("expected_keywords", []),
            "expected_answer_contains": case.get("expected_answer_contains"),
            "expected_files": case.get("expected_files", []),
            "expected_symbol": case.get("expected_symbol"),
            "expected_callee_symbols": case.get("expected_callee_symbols", []),
        }

    @staticmethod
    def _example_metadata(case: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": case["id"],
            "category": case["category"],
            "difficulty": case.get("difficulty"),
            "tags": case.get("tags", []),
        }

    @staticmethod
    def _configure_langsmith_env(runtime_settings) -> None:
        os.environ["LANGSMITH_API_KEY"] = runtime_settings.langsmith_api_key
        os.environ["LANGSMITH_ENDPOINT"] = runtime_settings.langsmith_endpoint
        os.environ["LANGSMITH_PROJECT"] = runtime_settings.langsmith_project
        os.environ["LANGCHAIN_TRACING_V2"] = "true"

    def _evaluators(self):
        def retrieval_hit_rate(run, example) -> dict[str, Any]:
            metrics = self._retrieval_metrics(run, example)
            return {"key": "retrieval_hit_rate", "score": metrics.hit_rate}

        def retrieval_mrr(run, example) -> dict[str, Any]:
            metrics = self._retrieval_metrics(run, example)
            return {"key": "retrieval_mrr", "score": metrics.mrr}

        def retrieval_recall_at_k(run, example) -> dict[str, Any]:
            metrics = self._retrieval_metrics(run, example)
            return {"key": "retrieval_recall_at_k", "score": metrics.recall_at_k}

        def answer_relevance(run, example) -> dict[str, Any]:
            metrics = self._answer_metrics(run, example)
            return {"key": "answer_relevance", "score": metrics.answer_relevance}

        def faithfulness(run, example) -> dict[str, Any]:
            metrics = self._answer_metrics(run, example)
            return {"key": "faithfulness", "score": metrics.faithfulness}

        def code_correctness(run, example) -> dict[str, Any]:
            metrics = self._answer_metrics(run, example)
            return {"key": "code_correctness", "score": metrics.code_correctness}

        return [retrieval_hit_rate, retrieval_mrr, retrieval_recall_at_k, answer_relevance, faithfulness, code_correctness]

    def _retrieval_metrics(self, run, example):
        return evaluate_retrieval(self._citations_from_run(run), self._test_case_from_example(example))

    def _answer_metrics(self, run, example):
        outputs = run.outputs or {}
        return evaluate_answer(
            outputs.get("answer", ""),
            self._citations_from_run(run),
            self._test_case_from_example(example),
        )

    @staticmethod
    def _citations_from_run(run) -> list[Citation]:
        outputs = run.outputs or {}
        return [Citation(**citation) for citation in outputs.get("citations", [])]

    @staticmethod
    def _test_case_from_example(example) -> dict[str, Any]:
        inputs = example.inputs or {}
        outputs = example.outputs or {}
        metadata = getattr(example, "metadata", None) or {}
        return {
            "id": metadata.get("id", ""),
            "category": inputs.get("category") or metadata.get("category", ""),
            "question": inputs.get("question", ""),
            "expected_keywords": outputs.get("expected_keywords", []),
            "expected_answer_contains": outputs.get("expected_answer_contains"),
            "expected_files": outputs.get("expected_files", []),
            "expected_symbol": outputs.get("expected_symbol"),
            "expected_callee_symbols": outputs.get("expected_callee_symbols", []),
        }

    def _local_report_from_run(self, strategy: str, run, example) -> dict[str, Any]:
        test_case = self._test_case_from_example(example)
        retrieval = self._retrieval_metrics(run, example)
        answer = self._answer_metrics(run, example)
        return {
            "strategy": strategy,
            "test_case_id": test_case["id"],
            "category": test_case["category"],
            "retrieval": asdict(retrieval),
            "answer": asdict(answer),
        }

    def _write_local_report(self, payload: dict[str, Any]) -> Path:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        report_path = self.settings.logs_dir / f"langsmith_eval_{timestamp}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return report_path


def get_knowledge_base_id(db: Session, kb_name: str) -> int:
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.name == kb_name).order_by(KnowledgeBase.id.desc()).first()
    if kb is None:
        raise ValueError(f"knowledge base not found: {kb_name}")
    return kb.id
