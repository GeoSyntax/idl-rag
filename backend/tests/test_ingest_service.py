from pathlib import Path

from app.services.ingest_service import IngestService, _sanitize_unicode_text


class _FakePdfPage:
    def extract_text(self) -> str:
        return ""


class _FakePdfReader:
    def __init__(self, _file_path: Path) -> None:
        self.pages = [_FakePdfPage()]


def test_extract_text_uses_ocr_fallback_for_scanned_pdf(monkeypatch, tmp_path: Path) -> None:
    pdf_path = tmp_path / "scan.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr("app.services.ingest_service.PdfReader", _FakePdfReader)
    monkeypatch.setattr(
        IngestService,
        "_extract_pdf_text_with_ocr",
        lambda self, file_path, file_hash=None: "实验一 IDL 基本运算\n1. 使用 idl_validname\n2. 表达式求值",
    )

    service = IngestService()

    assert service._extract_text(pdf_path).startswith("实验一 IDL 基本运算")


def test_sanitize_unicode_text_replaces_lone_surrogates() -> None:
    value = "reflectance " + chr(0xD835) + chr(0xDC9C) + " and valid 中文"

    sanitized = _sanitize_unicode_text(value)

    assert chr(0xD835) not in sanitized
    assert chr(0xDC9C) not in sanitized
    assert "reflectance" in sanitized
    assert "中文" in sanitized
