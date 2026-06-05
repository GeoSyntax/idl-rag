from app.api.schemas import Citation
from tests.eval.metrics import evaluate_answer, evaluate_retrieval, keyword_recall


def test_keyword_recall_counts_expected_terms() -> None:
    score = keyword_recall("ENVI_OPEN_FILE can open an ENVI raster", ["ENVI_OPEN_FILE", "raster", "missing"])
    assert score == 2 / 3


def test_evaluate_retrieval_scores_hit_precision_recall_and_mrr() -> None:
    citations = [
        Citation(
            chunk_id=1,
            document_id=1,
            file_name="unrelated.md",
            file_path="E:/samples/unrelated.md",
            title="Other",
            section="Other",
            symbol_name=None,
            excerpt="not relevant",
        ),
        Citation(
            chunk_id=2,
            document_id=2,
            file_name="envi_api.md",
            file_path="E:/samples/envi_api.md",
            title="ENVI_OPEN_FILE",
            section="ENVI_OPEN_FILE",
            symbol_name=None,
            excerpt="ENVI_OPEN_FILE opens ENVI raster files.",
        ),
    ]
    metrics = evaluate_retrieval(
        citations,
        {
            "expected_files": ["envi_api.md"],
            "expected_keywords": ["ENVI_OPEN_FILE", "raster"],
        },
    )

    assert metrics.hit_rate == 1.0
    assert metrics.precision_at_k == 0.5
    assert metrics.recall_at_k == 1.0
    assert metrics.mrr == 0.5


def test_evaluate_answer_scores_faithfulness_and_code_correctness() -> None:
    citation = Citation(
        chunk_id=1,
        document_id=1,
        file_name="envi_raster_ops.md",
        file_path="E:/samples/envi_raster_ops.md",
        title="compute_ndvi",
        section="compute_ndvi",
        symbol_name="compute_ndvi",
        excerpt="compute_ndvi uses nir and red. Use compile_opt idl2 and return NDVI.",
    )
    answer = """function compute_ndvi, nir, red
  compile_opt idl2
  return, (nir - red) / (nir + red)
end
"""
    metrics = evaluate_answer(
        answer,
        [citation],
        {
            "category": "code_generation",
            "expected_keywords": ["function", "compute_ndvi", "nir", "red", "return"],
            "expected_answer_contains": "compute_ndvi",
        },
    )

    assert metrics.keyword_recall >= 0.8
    assert metrics.faithfulness > 0
    assert metrics.answer_relevance == 1.0
    assert metrics.code_correctness == 1.0
