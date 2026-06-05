from __future__ import annotations

from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, KnowledgeBase
from app.services.embedding_service import EmbeddingService


class LanceVectorStore:
    def __init__(self, embedding_service: EmbeddingService, lancedb_dir: Path) -> None:
        self.embedding_service = embedding_service
        self.lancedb_dir = lancedb_dir

    def get_table_name(self, db: Session) -> str:
        return self.embedding_service.get_table_name(db)

    def replace_document_chunks(
        self,
        db: Session,
        document: Document,
        chunks: list[Chunk],
        embeddings: list[list[float]],
    ) -> None:
        table = self._get_table(db)
        rows = [
            {
                "chunk_id": chunk.id,
                "knowledge_base_id": chunk.knowledge_base_id,
                "document_id": chunk.document_id,
                "file_name": document.file_name,
                "file_path": document.file_path,
                "title": chunk.title,
                "section": chunk.section,
                "symbol_name": chunk.symbol_name,
                "chunk_kind": str(chunk.meta_json.get("chunk_kind") or ""),
                "content": chunk.content,
                "vector": vector,
            }
            for chunk, vector in zip(chunks, embeddings, strict=True)
        ]
        table.delete(f"document_id = {document.id}")
        table.add(rows)

    def search(self, db: Session, knowledge_base_id: int, vector: list[float], top_k: int) -> list[dict[str, Any]]:
        table = self._get_table(db)
        try:
            return (
                table.search(vector)
                .where(f"knowledge_base_id = {knowledge_base_id}")
                .limit(top_k)
                .to_list()
            )
        except Exception:  # noqa: BLE001
            return []

    def remove_document(self, document_id: int) -> None:
        for table in self._get_existing_tables():
            table.delete(f"document_id = {document_id}")

    def remove_knowledge_base(self, knowledge_base_id: int) -> None:
        for table in self._get_existing_tables():
            table.delete(f"knowledge_base_id = {knowledge_base_id}")

    def _get_existing_tables(self) -> list[Any]:
        database = lancedb.connect(self.lancedb_dir.as_posix())
        try:
            table_names = [name for name in database.table_names() if name.startswith("chunks")]
        except Exception:  # noqa: BLE001
            table_names = []
        tables = []
        for table_name in table_names:
            try:
                tables.append(database.open_table(table_name))
            except Exception:  # noqa: BLE001
                continue
        return tables

    def _get_table(self, db: Session):
        table_name = self.get_table_name(db)
        database = lancedb.connect(self.lancedb_dir.as_posix())
        try:
            return database.open_table(table_name)
        except Exception:  # noqa: BLE001
            schema = pa.schema(
                [
                    pa.field("chunk_id", pa.int64()),
                    pa.field("knowledge_base_id", pa.int64()),
                    pa.field("document_id", pa.int64()),
                    pa.field("file_name", pa.utf8()),
                    pa.field("file_path", pa.utf8()),
                    pa.field("title", pa.utf8()),
                    pa.field("section", pa.utf8()),
                    pa.field("symbol_name", pa.utf8()),
                    pa.field("chunk_kind", pa.utf8()),
                    pa.field("content", pa.utf8()),
                    pa.field("vector", pa.list_(pa.float32(), self.embedding_service.get_dimensions())),
                ]
            )
            return database.create_table(table_name, schema=schema)


class SQLiteFullTextStore:
    def remove_document(self, db: Session, document_id: int) -> None:
        db.execute(text("DELETE FROM chunk_fts WHERE document_id = :document_id"), {"document_id": document_id})

    def remove_knowledge_base(self, db: Session, knowledge_base_id: int) -> None:
        db.execute(
            text("DELETE FROM chunk_fts WHERE knowledge_base_id = :knowledge_base_id"),
            {"knowledge_base_id": knowledge_base_id},
        )

    def replace_document_chunks(
        self,
        db: Session,
        knowledge_base_id: int,
        document_id: int,
        rows: list[dict[str, Any]],
    ) -> None:
        self.remove_document(db, document_id)
        if not rows:
            return
        db.execute(
            text(
                """
                INSERT INTO chunk_fts (
                    chunk_id,
                    content,
                    title,
                    section,
                    symbol_name,
                    document_id,
                    knowledge_base_id
                ) VALUES (
                    :chunk_id,
                    :content,
                    :title,
                    :section,
                    :symbol_name,
                    :document_id,
                    :knowledge_base_id
                )
                """
            ),
            [
                {
                    **row,
                    "document_id": document_id,
                    "knowledge_base_id": knowledge_base_id,
                }
                for row in rows
            ],
        )

    def search(self, db: Session, knowledge_base_id: int, match_queries: list[str], top_k: int) -> list[dict[str, Any]]:
        sql = text(
            """
            SELECT
                c.id AS chunk_id,
                c.knowledge_base_id AS knowledge_base_id,
                c.document_id AS document_id,
                d.file_name AS file_name,
                d.file_path AS file_path,
                c.title AS title,
                c.section AS section,
                c.symbol_name AS symbol_name,
                COALESCE(json_extract(c.meta_json, '$.chunk_kind'), '') AS chunk_kind,
                json_extract(c.meta_json, '$.start_line') AS line_start,
                json_extract(c.meta_json, '$.end_line') AS line_end,
                bm25(chunk_fts) AS fts_score,
                substr(c.content, 1, 600) AS excerpt
            FROM chunk_fts
            JOIN chunks c ON c.id = chunk_fts.chunk_id
            JOIN documents d ON d.id = c.document_id
            WHERE chunk_fts MATCH :match_query
              AND c.knowledge_base_id = :knowledge_base_id
              AND d.status = 'ready'
            ORDER BY bm25(chunk_fts)
            LIMIT :limit
            """
        )
        seen: set[int] = set()
        results: list[dict[str, Any]] = []
        for match_query in match_queries:
            try:
                rows = db.execute(
                    sql,
                    {
                        "match_query": match_query,
                        "knowledge_base_id": knowledge_base_id,
                        "limit": top_k,
                    },
                ).mappings()
            except Exception:  # noqa: BLE001
                continue
            for row in rows:
                chunk_id = int(row["chunk_id"])
                if chunk_id in seen:
                    continue
                seen.add(chunk_id)
                result = dict(row)
                result["source_strategy"] = "fts"
                results.append(result)
                if len(results) >= top_k:
                    return results
        return results


class MetadataStore:
    def document_info(self, db: Session, document_id: int) -> tuple[int, str] | None:
        row = db.execute(
            select(Document.knowledge_base_id, KnowledgeBase.name)
            .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
            .where(Document.id == document_id)
        ).one_or_none()
        return (int(row[0]), row[1]) if row is not None else None

    def chunk_metadata(self, db: Session, chunk_id: int) -> dict:
        return db.scalar(select(Chunk.meta_json).where(Chunk.id == chunk_id)) or {}

    def ready_document_ids(self, db: Session, document_ids: set[int]) -> set[int]:
        if not document_ids:
            return set()
        return set(db.scalars(select(Document.id).where(Document.id.in_(document_ids), Document.status == "ready")))
