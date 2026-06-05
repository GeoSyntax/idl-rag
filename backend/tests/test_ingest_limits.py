from pathlib import Path
from types import SimpleNamespace

import pytest


def _prepare_settings(monkeypatch, tmp_path: Path, **env: str) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    from app.core.config import get_app_settings

    get_app_settings.cache_clear()


def test_import_path_requires_configured_root(monkeypatch, tmp_path: Path) -> None:
    _prepare_settings(monkeypatch, tmp_path)

    from app.services.ingest_service import IngestService

    service = IngestService()
    with pytest.raises(ValueError, match="服务器路径导入未启用"):
        service._resolve_import_path(str(tmp_path / "source.md"))


def test_import_path_allows_only_configured_root(monkeypatch, tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    blocked_root = tmp_path / "blocked"
    allowed_root.mkdir()
    blocked_root.mkdir()
    allowed_file = allowed_root / "source.md"
    blocked_file = blocked_root / "source.md"
    allowed_file.write_text("ok", encoding="utf-8")
    blocked_file.write_text("no", encoding="utf-8")
    _prepare_settings(monkeypatch, tmp_path, IDLRAG_IMPORT_ROOTS=str(allowed_root))

    from app.services.ingest_service import IngestService

    service = IngestService()
    assert service._resolve_import_path(str(allowed_file)) == allowed_file.resolve()
    with pytest.raises(ValueError, match="不允许导入该路径"):
        service._resolve_import_path(str(blocked_file))


def test_upload_batch_file_count_limit(monkeypatch, tmp_path: Path) -> None:
    _prepare_settings(monkeypatch, tmp_path, IDLRAG_MAX_UPLOAD_FILES="2")

    from app.services.ingest_service import IngestService

    service = IngestService()
    files = [SimpleNamespace(filename=f"file-{index}.md") for index in range(3)]
    with pytest.raises(ValueError, match="单次最多上传 2 个文件"):
        service._validate_upload_batch(files)


def test_file_size_limit(monkeypatch, tmp_path: Path) -> None:
    _prepare_settings(monkeypatch, tmp_path, IDLRAG_MAX_UPLOAD_FILE_MB="1")
    source = tmp_path / "large.md"
    source.write_bytes(b"x" * (1024 * 1024 + 1))

    from app.services.ingest_service import IngestService

    service = IngestService()
    with pytest.raises(ValueError, match="单个文件不能超过 1 MB"):
        service._validate_file_limits(source)


def test_pdf_page_limit(monkeypatch, tmp_path: Path) -> None:
    _prepare_settings(monkeypatch, tmp_path, IDLRAG_MAX_PDF_PAGES="1")
    source = tmp_path / "large.pdf"
    source.write_bytes(b"%PDF-1.4")

    class FakePdfReader:
        def __init__(self, _path: Path) -> None:
            self.pages = [object(), object()]

    monkeypatch.setattr("app.services.ingest_service.PdfReader", FakePdfReader)

    from app.services.ingest_service import IngestService

    service = IngestService()
    with pytest.raises(ValueError, match="PDF 页数不能超过 1 页"):
        service._validate_file_limits(source)
