from __future__ import annotations

import hashlib
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from fastapi import UploadFile
from pypdf import PdfReader
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.schemas import DocumentChunkResponse, DocumentResponse, ImportResult
from app.core.config import get_app_settings
from app.db.database import sqlite_retry
from app.db.models import Chunk, Document, IndexJob, KnowledgeBase, SymbolDependency
from app.services.chunk_idl_service import CHUNKER_VERSION as IDL_CHUNKER_VERSION, PARSER_VERSION as IDL_PARSER_VERSION, ChunkResult, IdlChunker
from app.services.retrieve_service import RetrievalService
from app.services.settings_service import get_runtime_settings
from app.services.storage_stores import SQLiteFullTextStore

SUPPORTED_SUFFIXES = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".pro": "text/x-idl",
    ".idl": "text/x-idl",
}

PARSER_VERSION_TEXT = "text-reader-v1"
PARSER_VERSION_PDF = "pdf-ocr-v2"
CHUNKER_VERSION_TEXT = "plain-text-v1"
CHUNKER_VERSION_IDL = IDL_CHUNKER_VERSION

# 加载 ENVI/IDL 自定义分词词典（全局生效，同时影响入库和查询分词）
_IDL_DICT_PATH = Path(__file__).resolve().parents[2] / "data" / "dict" / "idl_terms.txt"
if _IDL_DICT_PATH.exists():
    try:
        import jieba
        jieba.load_userdict(_IDL_DICT_PATH.as_posix())
    except ImportError:
        pass


class DuplicateDocumentError(ValueError):
    pass


def _get_rapid_ocr_engine():
    """获取 RapidOCR 引擎单例，避免每次 OCR 都重新加载 ONNX 模型。"""
    if not hasattr(_get_rapid_ocr_engine, "_instance"):
        from rapidocr_onnxruntime import RapidOCR
        _get_rapid_ocr_engine._instance = RapidOCR()
    return _get_rapid_ocr_engine._instance


# 模块级标记：OCR 引擎不可用时跳过后续尝试
_OCR_AVAILABLE: bool | None = None


def _sanitize_unicode_text(value: str) -> str:
    """Remove lone UTF-16 surrogate code points emitted by malformed PDFs.

    Some scientific PDFs contain mathematical glyph mappings that pypdf exposes
    as lone surrogates. SQLite, JSON and embedding clients require valid UTF-8;
    replacing only those invalid code points keeps the rest of the extracted
    formula/text available instead of failing the entire indexing job.
    """

    return "".join("\uFFFD" if 0xD800 <= ord(char) <= 0xDFFF else char for char in value)


class IngestService:
    def __init__(self) -> None:
        self.chunker = IdlChunker()
        self.retrieval_service = RetrievalService()
        self.full_text_store = SQLiteFullTextStore()

    @property
    def settings(self):
        """Read current settings so long-lived worker/service instances do not retain stale test or process config."""
        return get_app_settings()

    def import_path(
        self,
        db: Session,
        knowledge_base_id: int,
        path: str,
        recursive: bool,
        owner_user_id: int,
    ) -> ImportResult:
        self._get_owned_knowledge_base_or_raise(db, knowledge_base_id, owner_user_id)
        source_path = self._resolve_import_path(path)
        if not source_path.exists():
            raise ValueError("导入路径不存在。")

        files = self._collect_files(source_path, recursive)
        imported: list[DocumentResponse] = []
        skipped: list[str] = []
        for file_path in files:
            try:
                self._validate_file_limits(file_path)
                document = self._queue_document(db, knowledge_base_id, file_path)
                imported.append(self._to_document_response(db, document))
            except DuplicateDocumentError:
                skipped.append(file_path.as_posix())
            except ValueError as exc:
                skipped.append(f"{file_path.as_posix()}: {exc}")
        return ImportResult(imported=imported, skipped=skipped)

    def upload_files(
        self,
        db: Session,
        knowledge_base_id: int,
        files: list[UploadFile],
        owner_user_id: int,
    ) -> ImportResult:
        self._get_owned_knowledge_base_or_raise(db, knowledge_base_id, owner_user_id)
        self._validate_upload_batch(files)
        target_dir = self.settings.source_dir / f"kb-{knowledge_base_id}"
        target_dir.mkdir(parents=True, exist_ok=True)

        imported: list[DocumentResponse] = []
        skipped: list[str] = []
        total_bytes = 0
        max_total_bytes = self.settings.max_upload_total_mb * 1024 * 1024
        for upload in files:
            if not upload.filename:
                continue
            file_name = Path(upload.filename).name
            suffix = Path(file_name).suffix.lower()
            if suffix not in SUPPORTED_SUFFIXES:
                skipped.append(file_name)
                continue
            destination = self._make_unique_path(target_dir / file_name)
            with destination.open("wb") as output:
                shutil.copyfileobj(upload.file, output)
            try:
                file_size = destination.stat().st_size
                if total_bytes + file_size > max_total_bytes:
                    destination.unlink(missing_ok=True)
                    raise ValueError(f"上传总大小不能超过 {self.settings.max_upload_total_mb} MB。")
                self._validate_file_limits(destination)
                total_bytes += file_size
                document = self._queue_document(db, knowledge_base_id, destination)
                imported.append(self._to_document_response(db, document))
            except DuplicateDocumentError:
                destination.unlink(missing_ok=True)
                skipped.append(file_name)
            except ValueError as exc:
                destination.unlink(missing_ok=True)
                skipped.append(f"{file_name}: {exc}")
        return ImportResult(imported=imported, skipped=skipped)

    def queue_text_document(
        self,
        db: Session,
        knowledge_base_id: int,
        file_name: str,
        content: str,
        owner_user_id: int,
    ) -> DocumentResponse:
        """Queue a server-generated, provenance-bearing Markdown document.

        This is intentionally narrower than ``upload_files``: callers must
        already have a local, trusted text payload (for example public paper
        metadata returned by an audited provider).  The content is written to
        the same private source store and follows the normal indexing worker
        path, so partial documents are removed when validation or queuing
        fails.
        """
        self._get_owned_knowledge_base_or_raise(db, knowledge_base_id, owner_user_id)
        safe_name = Path(file_name).name
        if Path(safe_name).suffix.lower() not in {".md", ".markdown", ".txt"}:
            raise ValueError("服务器生成的文献记录必须是 Markdown 或纯文本。")
        if not content.strip():
            raise ValueError("不能为知识库写入空的文献记录。")
        target_dir = self.settings.source_dir / f"kb-{knowledge_base_id}"
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = self._make_unique_path(target_dir / safe_name)
        destination.write_text(content, encoding="utf-8")
        try:
            self._validate_file_limits(destination)
            document = self._queue_document(db, knowledge_base_id, destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return self._to_document_response(db, document)

    def list_documents(self, db: Session, knowledge_base_id: int, owner_user_id: int) -> list[DocumentResponse]:
        self._get_owned_knowledge_base_or_raise(db, knowledge_base_id, owner_user_id)
        documents = (
            db.query(Document)
            .filter(Document.knowledge_base_id == knowledge_base_id)
            .order_by(Document.created_at.desc(), Document.id.desc())
            .all()
        )
        return [self._to_document_response(db, document) for document in documents]

    def list_document_chunks(
        self,
        db: Session,
        knowledge_base_id: int,
        document_id: int,
        owner_user_id: int,
    ) -> list[DocumentChunkResponse]:
        self._get_owned_knowledge_base_or_raise(db, knowledge_base_id, owner_user_id)
        self._get_document_or_raise(db, knowledge_base_id, document_id)
        chunks = (
            db.query(Chunk)
            .filter(Chunk.knowledge_base_id == knowledge_base_id, Chunk.document_id == document_id)
            .order_by(Chunk.chunk_index.asc(), Chunk.id.asc())
            .all()
        )
        return [
            DocumentChunkResponse(
                id=chunk.id,
                knowledge_base_id=chunk.knowledge_base_id,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                title=chunk.title,
                section=chunk.section,
                symbol_name=chunk.symbol_name,
                content=chunk.content,
                metadata=chunk.meta_json or {},
                created_at=chunk.created_at,
            )
            for chunk in chunks
        ]

    def delete_document(self, db: Session, knowledge_base_id: int, document_id: int, owner_user_id: int) -> None:
        self._get_owned_knowledge_base_or_raise(db, knowledge_base_id, owner_user_id)
        document = self._get_document_or_raise(db, knowledge_base_id, document_id)
        self.retrieval_service.remove_document(document_id)
        db.execute(delete(IndexJob).where(IndexJob.document_id == document_id))
        self.full_text_store.remove_document(db, document_id)
        db.execute(delete(SymbolDependency).where(SymbolDependency.document_id == document_id))
        db.delete(document)
        db.commit()

    def retry_document(self, db: Session, knowledge_base_id: int, document_id: int, owner_user_id: int) -> DocumentResponse:
        self._get_owned_knowledge_base_or_raise(db, knowledge_base_id, owner_user_id)
        document = self._get_document_or_raise(db, knowledge_base_id, document_id)
        if document.status != "failed":
            raise ValueError("只有失败文档可以重试。")
        return self._requeue_document(db, document, "ingest")

    def reindex_document(self, db: Session, knowledge_base_id: int, document_id: int, owner_user_id: int) -> DocumentResponse:
        self._get_owned_knowledge_base_or_raise(db, knowledge_base_id, owner_user_id)
        document = self._get_document_or_raise(db, knowledge_base_id, document_id)
        if document.status not in {"ready", "stale"}:
            raise ValueError("只有 ready 或 stale 文档可以重建索引。")
        return self._requeue_document(db, document, "reindex")

    def _get_document_or_raise(self, db: Session, knowledge_base_id: int, document_id: int) -> Document:
        document = db.get(Document, document_id)
        if document is None or document.knowledge_base_id != knowledge_base_id:
            raise ValueError("文档不存在。")
        return document

    def _get_owned_knowledge_base_or_raise(self, db: Session, knowledge_base_id: int, owner_user_id: int) -> KnowledgeBase:
        knowledge_base = db.get(KnowledgeBase, knowledge_base_id)
        if knowledge_base is None or knowledge_base.owner_user_id != owner_user_id:
            raise ValueError("知识库不存在。")
        return knowledge_base

    def _requeue_document(self, db: Session, document: Document, job_type: str) -> DocumentResponse:
        db.add(
            IndexJob(
                knowledge_base_id=document.knowledge_base_id,
                document_id=document.id,
                job_type=job_type,
                status="queued",
                payload_json={"file_path": document.file_path, "previous_status": document.status},
            )
        )
        document.status = "queued"
        document.error_message = None
        db.commit()
        db.refresh(document)
        return self._to_document_response(db, document)

    def process_next_job(self, db: Session) -> bool:
        self.recover_stale_processing_jobs(db)
        job = (
            db.query(IndexJob)
            .filter(IndexJob.status == "queued")
            .order_by(IndexJob.created_at.asc(), IndexJob.id.asc())
            .first()
        )
        if job is None:
            return False

        document = db.get(Document, job.document_id)
        if document is None:
            job.status = "failed"
            job.error_message = "文档不存在。"
            job.finished_at = datetime.utcnow()
            db.commit()
            return True

        job.status = "processing"
        job.attempt_count += 1
        job.started_at = datetime.utcnow()
        job.finished_at = None
        job.error_message = None
        document.status = "processing"
        document.error_message = None
        db.commit()

        try:
            self._process_document(db, document, job.job_type)
        except Exception as exc:  # noqa: BLE001
            self._handle_job_failure(db, job.id, document.id, str(exc))
            return True

        completed_job = db.get(IndexJob, job.id)
        if completed_job is not None:
            completed_job.status = "completed"
            completed_job.error_message = None
            completed_job.finished_at = datetime.utcnow()
            db.commit()
        return True

    def recover_stale_processing_jobs(self, db: Session) -> int:
        cutoff = datetime.utcnow() - timedelta(minutes=self.settings.index_job_timeout_minutes)
        stale_jobs = (
            db.query(IndexJob)
            .filter(IndexJob.status == "processing", IndexJob.started_at.is_not(None), IndexJob.started_at < cutoff)
            .all()
        )
        recovered = 0
        for job in stale_jobs:
            document = db.get(Document, job.document_id)
            if job.attempt_count >= self.settings.index_job_max_attempts:
                job.status = "failed"
                job.error_message = "索引任务处理超时。"
                job.finished_at = datetime.utcnow()
                if document is not None:
                    document.status = "failed"
                    document.error_message = job.error_message
                continue
            job.status = "queued"
            job.error_message = "索引任务处理超时，已重新排队。"
            job.started_at = None
            if document is not None:
                document.status = "queued"
                document.error_message = job.error_message
            recovered += 1
        if stale_jobs:
            db.commit()
        return recovered

    def _handle_job_failure(self, db: Session, job_id: int, document_id: int, error_message: str) -> None:
        failed_job = db.get(IndexJob, job_id)
        failed_document = db.get(Document, document_id)
        if failed_job is None:
            return
        message = error_message[:500]
        failed_job.error_message = message
        if failed_job.attempt_count < self.settings.index_job_max_attempts:
            failed_job.status = "queued"
            failed_job.started_at = None
            if failed_document is not None:
                failed_document.status = "queued"
                failed_document.error_message = message
            db.commit()
            return
        failed_job.status = "failed"
        failed_job.finished_at = datetime.utcnow()
        previous_status = (failed_job.payload_json or {}).get("previous_status")
        if failed_document is not None:
            failed_document.status = previous_status if failed_job.job_type == "reindex" and previous_status else "failed"
            failed_document.error_message = message
            failed_document.retry_count += 1
        db.commit()

    def _queue_document(self, db: Session, knowledge_base_id: int, file_path: Path) -> Document:
        knowledge_base = db.get(KnowledgeBase, knowledge_base_id)
        if knowledge_base is None:
            raise ValueError("知识库不存在。")

        suffix = file_path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise ValueError(f"不支持的文件类型：{suffix}")

        sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
        existing = db.execute(
            select(Document).where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.sha256 == sha256,
            )
        ).scalar_one_or_none()
        if existing:
            raise DuplicateDocumentError(file_path.name)

        document = Document(
            knowledge_base_id=knowledge_base_id,
            file_name=file_path.name,
            file_path=file_path.as_posix(),
            media_type=SUPPORTED_SUFFIXES[suffix],
            sha256=sha256,
            status="queued",
        )
        db.add(document)
        db.flush()
        db.add(
            IndexJob(
                knowledge_base_id=knowledge_base_id,
                document_id=document.id,
                job_type="ingest",
                status="queued",
                payload_json={"file_path": file_path.as_posix()},
            )
        )
        db.commit()
        db.refresh(document)
        return document

    @sqlite_retry(max_attempts=3, base_delay=0.1)
    def _process_document(self, db: Session, document: Document, job_type: str = "ingest") -> Document:
        file_path = Path(document.file_path)
        if not file_path.exists():
            raise ValueError("源文件不存在。")

        try:
            text_content = self._extract_text(file_path, document.sha256[:16] if document.sha256 else None)
            chunk_result = self._build_chunks(text_content, file_path)
            if not chunk_result.chunks:
                raise ValueError("没有提取到可索引文本。")

            parser_version, chunker_version = self._get_index_versions(file_path)
            runtime_settings = get_runtime_settings(db)
            index_table = self.retrieval_service.get_table_name(db)

            chunks: list[Chunk] = []
            for index, payload in enumerate(chunk_result.chunks):
                chunk = Chunk(
                    knowledge_base_id=document.knowledge_base_id,
                    document_id=document.id,
                    chunk_index=index,
                    title=payload["title"],
                    section=payload["section"],
                    symbol_name=payload["symbol_name"],
                    content=payload["content"],
                    meta_json=payload["meta_json"],
                )
                db.add(chunk)
                chunks.append(chunk)
            db.flush()
            new_chunk_ids = [chunk.id for chunk in chunks]

            db.execute(delete(Chunk).where(Chunk.document_id == document.id, Chunk.id.not_in(new_chunk_ids)))
            self._replace_fts_rows(db, document.knowledge_base_id, document.id, chunks)
            db.execute(delete(SymbolDependency).where(SymbolDependency.document_id == document.id))
            for caller, callee in chunk_result.relationships:
                db.add(SymbolDependency(
                    knowledge_base_id=document.knowledge_base_id,
                    document_id=document.id,
                    caller_symbol=caller,
                    callee_symbol=callee,
                ))

            embedding_is_fallback = self.retrieval_service.replace_document_chunks(db, document, chunks)

            document = db.get(Document, document.id)
            if document is None:
                raise ValueError("文档不存在。")
            document.status = "ready"
            document.error_message = None
            document.parser_version = parser_version
            document.chunker_version = chunker_version
            document.embedding_model = runtime_settings.embedding_model
            document.embedding_dimensions = self.settings.embedding_dimensions
            document.embedding_is_fallback = embedding_is_fallback
            document.index_table = index_table
            document.last_indexed_at = datetime.utcnow()
            db.commit()
            db.refresh(document)
            return document
        except Exception:
            db.rollback()
            if job_type != "reindex":
                self.retrieval_service.remove_document(document.id)
                db.execute(delete(Chunk).where(Chunk.document_id == document.id))
                self.full_text_store.remove_document(db, document.id)
                db.execute(delete(SymbolDependency).where(SymbolDependency.document_id == document.id))
                db.commit()
            raise

    def _resolve_import_path(self, path: str) -> Path:
        source_path = Path(path).expanduser().resolve()
        if self.settings.allow_arbitrary_import_path:
            return source_path
        roots = self.settings.import_root_paths
        if not roots:
            raise ValueError("服务器路径导入未启用，请使用上传或配置 IDLRAG_IMPORT_ROOTS。")
        if not any(source_path == root or source_path.is_relative_to(root) for root in roots):
            raise ValueError("不允许导入该路径。")
        return source_path

    def _validate_upload_batch(self, files: list[UploadFile]) -> None:
        named_files = [upload for upload in files if upload.filename]
        if len(named_files) > self.settings.max_upload_files:
            raise ValueError(f"单次最多上传 {self.settings.max_upload_files} 个文件。")

    def _validate_file_limits(self, file_path: Path) -> None:
        max_file_bytes = self.settings.max_upload_file_mb * 1024 * 1024
        file_size = file_path.stat().st_size
        if file_size > max_file_bytes:
            raise ValueError(f"单个文件不能超过 {self.settings.max_upload_file_mb} MB。")
        if file_path.suffix.lower() == ".pdf":
            self._validate_pdf_page_count(file_path)

    def _validate_pdf_page_count(self, file_path: Path) -> None:
        reader = PdfReader(file_path)
        if len(reader.pages) > self.settings.max_pdf_pages:
            raise ValueError(f"PDF 页数不能超过 {self.settings.max_pdf_pages} 页。")

    def _collect_files(self, source_path: Path, recursive: bool) -> list[Path]:
        if source_path.is_file():
            return [source_path] if source_path.suffix.lower() in SUPPORTED_SUFFIXES else []
        iterator: Iterable[Path] = source_path.rglob("*") if recursive else source_path.glob("*")
        return sorted(
            [item for item in iterator if item.is_file() and item.suffix.lower() in SUPPORTED_SUFFIXES]
        )

    def _extract_text(self, file_path: Path, file_hash: str | None = None) -> str:
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            text_content = _sanitize_unicode_text(self._extract_pdf_text(file_path))
            if self._has_meaningful_text(text_content):
                return text_content
            ocr_text = self._extract_pdf_text_with_ocr(file_path, file_hash)
            return _sanitize_unicode_text(ocr_text).strip()
        return _sanitize_unicode_text(file_path.read_text(encoding="utf-8", errors="ignore")).strip()

    def _extract_pdf_text(self, file_path: Path) -> str:
        reader = PdfReader(file_path)
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages).strip()

    def _extract_pdf_text_with_ocr(self, file_path: Path, file_hash: str | None = None) -> str:
        global _OCR_AVAILABLE
        if _OCR_AVAILABLE is False:
            return ""

        try:
            import fitz
            _OCR_AVAILABLE = True
        except ImportError:
            _OCR_AVAILABLE = False
            return ""

        engine = _get_rapid_ocr_engine()

        # OCR 页面级缓存：按文件 hash + 页码缓存结果
        cache_dir: Path | None = None
        if file_hash:
            cache_dir = self.settings.ocr_cache_dir
            cache_dir.mkdir(parents=True, exist_ok=True)

        page_texts: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            document = fitz.open(file_path)
            try:
                for page_index in range(document.page_count):
                    # 检查缓存
                    cache_file = cache_dir / f"{file_hash}_p{page_index}.txt" if cache_dir else None
                    if cache_file and cache_file.exists():
                        cached = cache_file.read_text(encoding="utf-8").strip()
                        page_texts.append(cached)
                        continue

                    page = document.load_page(page_index)
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                    image_path = temp_path / f"page-{page_index + 1}.png"
                    pixmap.save(image_path.as_posix())
                    # 立即释放 pixmap 和 page 引用，避免大 PDF 峰值内存过高
                    del pixmap
                    del page

                    result, _ = engine(image_path.as_posix())

                    # OCR 完成后删除临时图片
                    image_path.unlink(missing_ok=True)

                    page_text = ""
                    if result:
                        lines = [item[1].strip() for item in result if len(item) > 1 and item[1].strip()]
                        if lines:
                            page_text = "\n".join(lines)

                    # 写入缓存（空白页也缓存，避免重复 OCR）
                    if cache_file is not None:
                        cache_file.write_text(page_text, encoding="utf-8")

                    if page_text:
                        page_texts.append(page_text)
            finally:
                document.close()
        return "\n\n".join(page_texts).strip()

    @staticmethod
    def _tokenize_for_fts(text: str) -> str:
        """用 jieba 对文本分词，结果用空格连接存入 FTS5。"""
        try:
            import jieba
            tokens = jieba.cut_for_search(text)
            return " ".join(tokens)
        except ImportError:
            return text

    def _has_meaningful_text(self, text_content: str) -> bool:
        compact = "".join(text_content.split())
        return len(compact) >= 30

    def _build_chunks(self, text_content: str, file_path: Path) -> ChunkResult:
        suffix = file_path.suffix.lower()
        if suffix in {".pro", ".idl"}:
            return self.chunker.chunk(text_content, file_path.as_posix())
        plain_chunks = self._chunk_plain_text(text_content, file_path)
        return ChunkResult(chunks=plain_chunks, symbols=[], relationships=[])

    def _chunk_plain_text(self, text_content: str, file_path: Path) -> list[dict]:
        paragraphs = [paragraph.strip() for paragraph in text_content.split("\n\n") if paragraph.strip()]
        chunks: list[dict] = []
        current_title: str | None = None
        current_lines: list[str] = []
        current_length = 0

        def flush() -> None:
            nonlocal current_lines, current_length
            if not current_lines:
                return
            chunks.append(
                {
                    "title": current_title or file_path.stem,
                    "section": current_title,
                    "symbol_name": None,
                    "content": "\n\n".join(current_lines).strip(),
                    "meta_json": {
                        "source_path": file_path.as_posix(),
                        "language": "text",
                        "parser_version": PARSER_VERSION_TEXT,
                        "chunker_version": CHUNKER_VERSION_TEXT,
                        "chunk_kind": "paragraph",
                    },
                }
            )
            current_lines = []
            current_length = 0

        for paragraph in paragraphs:
            normalized = paragraph.splitlines()[0].strip()
            if normalized.startswith("#"):
                flush()
                current_title = normalized.lstrip("# ").strip() or current_title
                continue
            if current_lines and current_length + len(paragraph) > 1200:
                flush()
            current_lines.append(paragraph)
            current_length += len(paragraph)
        flush()
        return chunks

    @staticmethod
    def _build_fts_content(chunk: Chunk) -> str:
        meta = chunk.meta_json or {}
        symbol_name = chunk.symbol_name or ""
        weighted_symbol = " ".join([symbol_name] * 3) if symbol_name else ""
        aliases = " ".join(
            [
                "栅格 raster image",
                "批处理 batch headless",
                "近红外 NIR near infrared",
                "重采样 resampling resize grid",
                "坐标系 spatial reference projection coordinate",
                "依赖 dependency caller callee",
                "调用 call invoke",
            ]
        )
        return "\n".join(
            [
                chunk.content,
                f"title: {chunk.title or ''}",
                f"section: {chunk.section or ''}",
                f"symbol_name: {symbol_name} {weighted_symbol}",
                f"chunk_kind: {meta.get('chunk_kind', '')}",
                aliases,
            ]
        )

    @sqlite_retry(max_attempts=3, base_delay=0.1)
    def _replace_fts_rows(
        self,
        db: Session,
        knowledge_base_id: int,
        document_id: int,
        chunks: list[Chunk],
    ) -> None:
        rows = [
            {
                "chunk_id": chunk.id,
                "content": self._tokenize_for_fts(self._build_fts_content(chunk)),
                "title": self._tokenize_for_fts(chunk.title or ""),
                "section": self._tokenize_for_fts(chunk.section or ""),
                "symbol_name": chunk.symbol_name or "",
            }
            for chunk in chunks
        ]
        self.full_text_store.replace_document_chunks(db, knowledge_base_id, document_id, rows)

    def _get_index_versions(self, file_path: Path) -> tuple[str, str]:
        suffix = file_path.suffix.lower()
        if suffix in {".pro", ".idl"}:
            return IDL_PARSER_VERSION, CHUNKER_VERSION_IDL
        parser_version = PARSER_VERSION_PDF if suffix == ".pdf" else PARSER_VERSION_TEXT
        return parser_version, CHUNKER_VERSION_TEXT

    def _make_unique_path(self, base_path: Path) -> Path:
        if not base_path.exists():
            return base_path
        stem = base_path.stem
        suffix = base_path.suffix
        index = 1
        while True:
            candidate = base_path.with_name(f"{stem}-{index}{suffix}")
            if not candidate.exists():
                return candidate
            index += 1

    def _to_document_response(self, db: Session, document: Document) -> DocumentResponse:
        chunk_count = db.execute(
            select(func.count(Chunk.id)).where(Chunk.document_id == document.id)
        ).scalar_one()
        return DocumentResponse(
            id=document.id,
            knowledge_base_id=document.knowledge_base_id,
            file_name=document.file_name,
            file_path=document.file_path,
            media_type=document.media_type,
            status=document.status,
            error_message=document.error_message,
            chunk_count=int(chunk_count),
            retry_count=document.retry_count,
            parser_version=document.parser_version,
            chunker_version=document.chunker_version,
            embedding_model=document.embedding_model,
            embedding_dimensions=document.embedding_dimensions,
            embedding_is_fallback=document.embedding_is_fallback,
            index_table=document.index_table,
            last_indexed_at=document.last_indexed_at,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )
