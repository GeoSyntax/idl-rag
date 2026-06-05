from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.api.schemas import Citation


EVALUATOR_NAME = "IDL-RAGAS-lite"
EVALUATOR_VERSION = "1.0"


@dataclass(frozen=True)
class RetrievalMetrics:
    precision_at_k: float
    recall_at_k: float
    mrr: float
    hit_rate: float


@dataclass(frozen=True)
class AnswerMetrics:
    keyword_recall: float
    faithfulness: float
    answer_relevance: float
    code_correctness: float


@dataclass(frozen=True)
class EvalReport:
    test_case_id: str
    category: str
    retrieval: RetrievalMetrics
    answer: AnswerMetrics
    latency_ms: float
    passed: bool
    strategy: str = "hybrid_rrf_no_rerank"
    evaluator: str = EVALUATOR_NAME


def normalize_text(value: str | None) -> str:
    return (value or "").lower()


def citation_text(citation: Citation) -> str:
    return "\n".join(
        str(part or "")
        for part in [
            citation.file_name,
            citation.file_path,
            citation.title,
            citation.section,
            citation.symbol_name,
            citation.excerpt,
        ]
    )


def keyword_hits(text: str, keywords: list[str]) -> int:
    lowered = normalize_text(text)
    return sum(1 for keyword in keywords if normalize_text(keyword) in lowered)


def keyword_recall(text: str, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    return keyword_hits(text, keywords) / len(keywords)


def is_relevant_citation(citation: Citation, test_case: dict[str, Any]) -> bool:
    expected_files = [normalize_text(item) for item in test_case.get("expected_files", [])]
    expected_symbol = normalize_text(test_case.get("expected_symbol"))
    expected_callees = [normalize_text(item) for item in test_case.get("expected_callee_symbols", [])]
    expected_callers = [normalize_text(item) for item in test_case.get("expected_caller_symbols", [])]
    expected_keywords = test_case.get("expected_keywords", [])

    file_name = normalize_text(citation.file_name)
    symbol_name = normalize_text(citation.symbol_name)
    text = normalize_text(citation_text(citation))

    if expected_symbol and expected_symbol == symbol_name:
        return True
    if expected_files and any(file_name == expected_file for expected_file in expected_files):
        return True
    if expected_callees and any(callee == symbol_name for callee in expected_callees):
        return True
    if expected_callers and any(caller == symbol_name for caller in expected_callers):
        return True
    return keyword_recall(text, expected_keywords) >= 0.5


def evaluate_retrieval(citations: list[Citation], test_case: dict[str, Any], top_k: int | None = None) -> RetrievalMetrics:
    if not citations:
        return RetrievalMetrics(precision_at_k=0.0, recall_at_k=0.0, mrr=0.0, hit_rate=0.0)

    k = top_k or len(citations)
    considered = citations[:k]
    relevant_flags = [is_relevant_citation(citation, test_case) for citation in considered]
    relevant_count = sum(1 for flag in relevant_flags if flag)

    target_count = len(test_case.get("expected_files", []))
    if test_case.get("expected_symbol"):
        target_count += 1
    target_count += len(test_case.get("expected_callee_symbols", []))
    target_count += len(test_case.get("expected_caller_symbols", []))
    if target_count == 0:
        target_count = 1

    first_relevant_rank = next((idx + 1 for idx, flag in enumerate(relevant_flags) if flag), None)
    return RetrievalMetrics(
        precision_at_k=relevant_count / max(len(considered), 1),
        recall_at_k=min(relevant_count / target_count, 1.0),
        mrr=1.0 / first_relevant_rank if first_relevant_rank else 0.0,
        hit_rate=1.0 if relevant_count > 0 else 0.0,
    )


def evaluate_answer(answer: str, citations: list[Citation], test_case: dict[str, Any]) -> AnswerMetrics:
    expected_keywords = test_case.get("expected_keywords", [])
    expected_answer_contains = test_case.get("expected_answer_contains")
    citation_context = "\n".join(citation.excerpt for citation in citations)
    answer_keyword_recall = keyword_recall(answer, expected_keywords)

    if expected_answer_contains:
        answer_relevance = 1.0 if normalize_text(expected_answer_contains) in normalize_text(answer) else answer_keyword_recall
    else:
        answer_relevance = answer_keyword_recall

    support_keywords = [keyword for keyword in expected_keywords if normalize_text(keyword) in normalize_text(answer)]
    faithfulness = keyword_recall(citation_context, support_keywords) if support_keywords else 0.0
    if citations and faithfulness == 0.0:
        faithfulness = min(keyword_recall(citation_context, expected_keywords), 1.0)

    return AnswerMetrics(
        keyword_recall=answer_keyword_recall,
        faithfulness=faithfulness,
        answer_relevance=answer_relevance,
        code_correctness=evaluate_code_correctness(answer, test_case),
    )


def evaluate_code_correctness(answer: str, test_case: dict[str, Any]) -> float:
    category = test_case.get("category", "")
    if category not in {"code_generation", "code_fix"}:
        return 1.0

    checks = [
        bool(re.search(r"\b(pro|function)\s+[A-Za-z_][A-Za-z0-9_]*", answer, re.IGNORECASE)),
        "compile_opt" in normalize_text(answer),
        bool(re.search(r"^\s*end\b", answer, re.IGNORECASE | re.MULTILINE)),
    ]
    keywords = test_case.get("expected_keywords", [])
    if keywords:
        checks.append(keyword_recall(answer, keywords) >= 0.4)
    return sum(1 for check in checks if check) / len(checks)


def report_passed(report: EvalReport, *, min_hit_rate: float = 1.0, min_answer_relevance: float = 0.4) -> bool:
    return (
        report.retrieval.hit_rate >= min_hit_rate
        and report.answer.answer_relevance >= min_answer_relevance
        and report.answer.faithfulness >= 0.3
    )
