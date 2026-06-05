from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.api.schemas import KnowledgeBaseCreate, RegisterRequest
from app.core.config import get_app_settings
from app.db.database import get_engine, get_session_factory, init_database
from app.db.models import IndexJob
from app.services.auth_service import AuthService
from app.services.ingest_service import IngestService
from app.services.knowledge_base_service import KnowledgeBaseService
from tests.eval.runner import EvalRunner


EVAL_MARKDOWN = """# ENVI_OPEN_FILE

ENVI_OPEN_FILE 用于打开 ENVI 格式的栅格文件。它通过 r_fid 返回文件 ID，失败时 fid 通常为 -1。

# ENVI_GET_DATA

ENVI_GET_DATA 使用 fid、dims、pos 和 data 参数读取指定波段。pos 是从 0 开始的波段索引。

# NetCDF

IDL 读取 NetCDF 通常使用 NCDF_OPEN 打开文件，NCDF_VARID 获取变量 ID，NCDF_VARGET 读取变量数据，最后 NCDF_CLOSE 关闭文件。

# Headless

服务器批处理环境应使用 e = envi(/headless)，避免打开 ENVI 图形界面。

# 大影像分块

大影像处理应按 block 或 tile 分块读取，避免一次性读入内存。
"""


EVAL_PRO = """; Evaluation symbols

function compute_ndvi, nir, red
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
def eval_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Session, int]:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("IDLRAG_IMPORT_ROOTS", str(tmp_path))
    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    init_database()

    session_factory = get_session_factory()
    db = session_factory()

    source_dir = tmp_path / "eval_sources"
    source_dir.mkdir()
    (source_dir / "envi_api.md").write_text(EVAL_MARKDOWN, encoding="utf-8")
    (source_dir / "idl_eval_samples.pro").write_text(EVAL_PRO, encoding="utf-8")

    login = AuthService().register(db, RegisterRequest(username="admin", password="secret123"))
    kb = KnowledgeBaseService().create_knowledge_base(
        db,
        KnowledgeBaseCreate(name="Eval KB", description="Evaluation corpus"),
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
        get_session_factory.cache_clear()


def _local_answer_provider(question, citations, test_case):
    if test_case["category"] == "code_generation":
        return """function compute_ndvi, nir, red
  compile_opt idl2
  return, (nir - red) / (nir + red)
end
"""
    return "\n".join(citation.excerpt for citation in citations)


def test_eval_runner_loads_golden_dataset(eval_env: tuple[Session, int]) -> None:
    db, kb_id = eval_env
    runner = EvalRunner(db, kb_id, answer_provider=_local_answer_provider)
    cases = runner.load_cases(["fts_keyword"])

    assert len(cases) == 6
    assert cases[0]["id"] == "fts_keyword_01"


def test_eval_runner_scores_keyword_retrieval(eval_env: tuple[Session, int]) -> None:
    db, kb_id = eval_env
    runner = EvalRunner(db, kb_id, answer_provider=_local_answer_provider)
    case = runner.load_cases(["fts_keyword"])[0]

    report = runner.run_single(case, top_k=6)

    assert report.passed
    assert report.retrieval.hit_rate == 1.0
    assert report.answer.answer_relevance >= 0.4
    assert report.answer.faithfulness >= 0.3


def test_eval_runner_scores_symbol_dependency_case(eval_env: tuple[Session, int]) -> None:
    db, kb_id = eval_env
    runner = EvalRunner(db, kb_id, answer_provider=_local_answer_provider)
    case = next(item for item in runner.load_cases(["dependency_graph"]) if item["id"] == "dependency_graph_01")

    report = runner.run_single(case, top_k=6)

    assert report.retrieval.hit_rate == 1.0
    assert report.answer.keyword_recall >= 0.5


def test_eval_summary_groups_by_category(eval_env: tuple[Session, int]) -> None:
    db, kb_id = eval_env
    runner = EvalRunner(db, kb_id, answer_provider=_local_answer_provider)
    reports = runner.run_all(["fts_keyword"], top_k=6)
    summary = runner.summary(reports)

    assert summary["evaluator"] == "IDL-RAGAS-lite"
    assert summary["total"] == 6
    assert "fts_keyword" in summary["by_category"]
