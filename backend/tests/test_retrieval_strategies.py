from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.api.schemas import KnowledgeBaseCreate, RegisterRequest
from app.core.config import get_app_settings
from app.db.database import get_engine, get_index_engine, get_index_session_factory, get_session_factory, init_database
from app.db.models import IndexJob
from app.services.auth_service import AuthService
from app.services.ingest_service import IngestService
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.retrieve_service import RetrievalService


STRATEGY_MARKDOWN = """# ENVI_OPEN_FILE

ENVI_OPEN_FILE 用于打开 ENVI 格式的栅格 raster 文件。失败时 r_fid 通常返回 -1。

# Headless Batch

服务器批处理 batch 环境应使用 e = envi(/headless)，避免打开图形界面。

# Coordinate

坐标系 spatial reference 和 map projection 信息用于地理配准与重采样 resampling。
"""


STRATEGY_PRO = """function compute_ndvi, nir, red
  compile_opt idl2
  denominator = nir + red
  ndvi = fltarr(size(red, /dimensions)) + !values.f_nan
  valid = where(denominator ne 0 and finite(denominator), count)
  if count gt 0 then ndvi[valid] = (nir[valid] - red[valid]) / denominator[valid]
  return, ndvi
end

function read_header, input_file
  compile_opt idl2
  header = {SCENE_HEADER, file: input_file, sensor: '', bands: 0, valid: 1}
  return, header
end

function apply_calibration, data, scale_factor, offset
  compile_opt idl2
  return, data * scale_factor + offset
end

pro process_scene, input_file, output_file
  compile_opt idl2
  header = read_header(input_file)
  calibrated = apply_calibration(findgen(10, 10), 0.0001, 0.0)
  print, output_file, header.valid, mean(calibrated)
end
"""


@pytest.fixture
def retrieval_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Session, int]:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("IDLRAG_IMPORT_ROOTS", str(tmp_path))
    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_index_engine.cache_clear()
    get_session_factory.cache_clear()
    get_index_session_factory.cache_clear()
    init_database()

    db = get_session_factory()()
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "strategy_docs.md").write_text(STRATEGY_MARKDOWN, encoding="utf-8")
    (source_dir / "strategy_samples.pro").write_text(STRATEGY_PRO, encoding="utf-8")

    login = AuthService().register(db, RegisterRequest(username="admin", password="secret123"))
    kb = KnowledgeBaseService().create_knowledge_base(
        db,
        KnowledgeBaseCreate(name="Strategy KB", description="Strategy test corpus"),
        owner_user_id=login.user.id,
    )

    ingest = IngestService()
    ingest.import_path(db, kb.id, source_dir.as_posix(), recursive=True, owner_user_id=login.user.id)
    while db.query(IndexJob).filter(IndexJob.status == "queued").count() > 0:
        ingest.process_next_job(db)

    try:
        yield db, kb.id
    finally:
        db.close()
        get_app_settings.cache_clear()
        get_engine.cache_clear()
        get_index_engine.cache_clear()
        get_session_factory.cache_clear()
        get_index_session_factory.cache_clear()


@pytest.mark.parametrize(
    "strategy,query",
    [
        ("fts_only", "ENVI_OPEN_FILE 栅格"),
        ("vector_only", "如何在服务器批处理环境启动 ENVI"),
        ("hybrid_rrf", "compute_ndvi 如何计算 NDVI"),
        ("hybrid_rrf_no_rerank", "process_scene 调用了哪些函数"),
        ("multi_query", "近红外和红光如何计算植被指数"),
        ("hyde", "IDL 中 NDVI 函数应该包含哪些步骤"),
        ("rag_fusion", "坐标系和重采样有什么关系"),
        ("parent_child", "compute_ndvi 的代码和说明"),
        ("dependency_graphrag", "process_scene 调用 read_header 和 apply_calibration 吗"),
        ("agentic_react", "ENVI_OPEN_FILE 的作用"),
    ],
)
def test_retrieval_strategies_return_citations(retrieval_env: tuple[Session, int], strategy: str, query: str) -> None:
    db, kb_id = retrieval_env
    citations = RetrievalService().search_with_strategy(db, kb_id, query, strategy, top_k=6)

    assert citations
    assert all(citation.chunk_id for citation in citations)
    assert all(citation.file_name for citation in citations)


def test_retrieval_citations_include_source_metadata(retrieval_env: tuple[Session, int]) -> None:
    db, kb_id = retrieval_env
    citations = RetrievalService().search_with_strategy(db, kb_id, "compute_ndvi 如何计算 NDVI", "hybrid_rrf", top_k=3)

    assert citations
    assert citations[0].knowledge_base_id == kb_id
    assert citations[0].knowledge_base_name == "Strategy KB"
    assert citations[0].chunk_kind
    assert citations[0].score is not None
    assert citations[0].source_strategy


def test_retrieval_debug_search_returns_ranked_candidates(retrieval_env: tuple[Session, int]) -> None:
    db, kb_id = retrieval_env
    response = RetrievalService().debug_search(db, [kb_id], "compute_ndvi 如何计算 NDVI", "hybrid_rrf", top_k=3)

    assert response.query == "compute_ndvi 如何计算 NDVI"
    assert response.strategy == "hybrid_rrf"
    assert response.candidate_count == len(response.candidates)
    assert response.candidates
    assert response.candidates[0].rank == 1
    assert response.candidates[0].knowledge_base_id == kb_id
    assert response.candidates[0].debug


def test_dependency_graphrag_includes_called_symbols(retrieval_env: tuple[Session, int]) -> None:
    db, kb_id = retrieval_env
    citations = RetrievalService().search_with_strategy(
        db,
        kb_id,
        "process_scene 调用了哪些子函数",
        "dependency_graphrag",
        top_k=6,
    )
    symbols = {citation.symbol_name for citation in citations}

    assert "read_header" in symbols
    assert "apply_calibration" in symbols


def test_search_multiple_uses_global_ranking(monkeypatch: pytest.MonkeyPatch, retrieval_env: tuple[Session, int]) -> None:
    db, kb_id = retrieval_env
    service = RetrievalService()

    def fake_hybrid_rows(_db, knowledge_base_id, _query, _candidate_limit):
        if knowledge_base_id == kb_id:
            return [
                {
                    "chunk_id": 1,
                    "document_id": 1,
                    "file_name": "first.md",
                    "file_path": "first.md",
                    "title": "first",
                    "section": None,
                    "symbol_name": None,
                    "chunk_kind": "paragraph",
                    "excerpt": "low relevance",
                }
            ]
        return [
            {
                "chunk_id": 2,
                "document_id": 2,
                "file_name": "second.md",
                "file_path": "second.md",
                "title": "second",
                "section": None,
                "symbol_name": None,
                "chunk_kind": "paragraph",
                "excerpt": "high relevance",
            }
        ]

    def fake_fuse(ranked_lists):
        return [ranked_lists[1][0], ranked_lists[0][0]]

    monkeypatch.setattr(service, "_hybrid_ranked_rows", fake_hybrid_rows)
    monkeypatch.setattr(service, "_fuse_ranked_lists", fake_fuse)
    monkeypatch.setattr(service.rerank_service, "rerank", lambda _db, _query, candidates, top_k: candidates[:top_k])

    citations = service.search_multiple(db, [kb_id, kb_id + 1], "query", top_k=1)

    assert citations[0].file_name == "second.md"


def test_hybrid_skips_hash_fallback_vectors(monkeypatch: pytest.MonkeyPatch, retrieval_env: tuple[Session, int]) -> None:
    db, kb_id = retrieval_env
    service = RetrievalService()
    fts_rows = [{
        "chunk_id": 1,
        "document_id": 1,
        "file_name": "fts.md",
        "file_path": "fts.md",
        "title": "FTS result",
        "section": None,
        "symbol_name": None,
        "chunk_kind": "paragraph",
        "excerpt": "deterministic text match",
    }]
    vector_rows = [{
        "chunk_id": 2,
        "document_id": 2,
        "file_name": "fallback.md",
        "file_path": "fallback.md",
        "title": "Hash fallback result",
        "section": None,
        "symbol_name": None,
        "chunk_kind": "paragraph",
        "excerpt": "not a semantic match",
    }]
    monkeypatch.setattr(service, "_search_fts", lambda *_args: fts_rows)
    monkeypatch.setattr(service, "_search_vectors", lambda *_args: vector_rows)
    monkeypatch.setattr(service.embedding_service, "last_query_fallback", True)

    rows = service._hybrid_ranked_rows(db, kb_id, "query", candidate_limit=10)

    assert [row["chunk_id"] for row in rows] == [1]


def test_source_tier_nudge_prefers_authoritative_material_when_evidence_is_close(
    retrieval_env: tuple[Session, int],
) -> None:
    db, kb_id = retrieval_env
    service = RetrievalService()
    rows = [
        {
            "chunk_id": 1,
            "document_id": 1,
            "file_path": "E:/workspace/data/sources/collected/openalex_remote_sensing_articles/paper.md",
            "file_name": "paper.md",
            "title": "candidate metadata",
            "section": None,
            "symbol_name": None,
            "chunk_kind": "paragraph",
        },
        {
            "chunk_id": 2,
            "document_id": 2,
            "file_path": "E:/workspace/data/sources/remote_sensing_official/product.md",
            "file_name": "product.md",
            "title": "official product guide",
            "section": None,
            "symbol_name": None,
            "chunk_kind": "paragraph",
        },
    ]

    ranked = service._rank_rows("product scale factor", [rows, list(reversed(rows))])

    assert ranked[0]["chunk_id"] == 2
    assert ranked[0]["source_tier_nudge"] > ranked[1]["source_tier_nudge"]
    db.close()


def test_filename_and_title_tokens_disambiguate_product_notes(
    retrieval_env: tuple[Session, int],
) -> None:
    db, _kb_id = retrieval_env
    service = RetrievalService()
    target = {
        "chunk_id": 1,
        "document_id": 1,
        "file_path": "E:/workspace/data/sources/remote_sensing_official/gee_sentinel2_harmonized.md",
        "file_name": "gee_sentinel2_harmonized.md",
        "title": "Sentinel-2 Harmonized products in Google Earth Engine",
        "section": "Product facts",
        "symbol_name": None,
        "chunk_kind": "paragraph",
    }
    distractor = {
        "chunk_id": 2,
        "document_id": 2,
        "file_path": "E:/workspace/data/sources/remote_sensing_official/Sentinel-1_Product_Definition.pdf",
        "file_name": "Sentinel-1_Product_Definition.pdf",
        "title": "Sentinel-1 product definition",
        "section": "Product facts",
        "symbol_name": None,
        "chunk_kind": "paragraph",
    }

    ranked = service._rank_rows(
        "GEE Sentinel-2 SR Harmonized 缩放因子",
        [[target, distractor], [distractor, target]],
    )

    assert ranked[0]["chunk_id"] == 1
    db.close()
