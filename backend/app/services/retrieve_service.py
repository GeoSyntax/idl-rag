from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import Citation, RetrievalDebugCandidate, RetrievalDebugResponse
from app.core.config import get_app_settings
from app.db.models import Chunk, Document, SymbolDependency
from app.services.embedding_service import EmbeddingService
from app.services.rerank_service import RerankService
from app.services.storage_stores import LanceVectorStore, MetadataStore, SQLiteFullTextStore


DEFAULT_RETRIEVAL_STRATEGY = "hybrid_rrf_no_rerank"
_RERANK_STRATEGIES = {"hybrid_rrf", "rag_fusion", "hyde", "parent_child", "dependency_graphrag", "agentic_react"}
_TOOL_STRATEGIES = {"grep", "grep_search", "symbol", "symbol_search", "read_context", "symbol_context", "find_callers", "find_callees"}


class RetrievalService:
    def __init__(self) -> None:
        self.settings = get_app_settings()
        self.embedding_service = EmbeddingService()
        self.rerank_service = RerankService()
        self.vector_store = LanceVectorStore(self.embedding_service, self.settings.lancedb_dir)
        self.full_text_store = SQLiteFullTextStore()
        self.metadata_store = MetadataStore()
        self.last_timing: dict[str, float] = {}

    def get_table_name(self, db: Session) -> str:
        return self.vector_store.get_table_name(db)

    def replace_document_chunks(self, db: Session, document: Document, chunks: list[Chunk]) -> bool:
        if not chunks:
            return False
        embeddings, is_fallback = self.embedding_service.embed_texts(db, [chunk.content for chunk in chunks])
        self.vector_store.replace_document_chunks(db, document, chunks, embeddings)
        return is_fallback

    def remove_document(self, document_id: int) -> None:
        self.vector_store.remove_document(document_id)

    def remove_knowledge_base(self, knowledge_base_id: int) -> None:
        self.vector_store.remove_knowledge_base(knowledge_base_id)

    def search_multiple(
        self,
        db: Session,
        knowledge_base_ids: list[int],
        query: str,
        top_k: int = 6,
        *,
        strategy: str = DEFAULT_RETRIEVAL_STRATEGY,
        use_rerank: bool | None = None,
    ) -> list[Citation]:
        if not knowledge_base_ids:
            return []
        strategy = self._normalize_strategy(strategy)
        if strategy in _TOOL_STRATEGIES or strategy not in {"hybrid_rrf", "hybrid_rrf_no_rerank", "fts_only", "vector_only"}:
            return self._merge_citations(
                [
                    citation
                    for kb_id in knowledge_base_ids
                    for citation in self.search_with_strategy(db, kb_id, query, strategy, top_k=top_k)
                ],
                top_k,
                strategy,
            )

        candidate_limit = max(top_k * 8, 32)
        if strategy == "fts_only":
            ranked_lists = [self._search_fts(db, kb_id, query, candidate_limit) for kb_id in knowledge_base_ids]
        elif strategy == "vector_only":
            ranked_lists = [self._search_vectors(db, kb_id, query, candidate_limit) for kb_id in knowledge_base_ids]
        else:
            ranked_lists = [self._hybrid_ranked_rows(db, kb_id, query, candidate_limit) for kb_id in knowledge_base_ids]

        rows = self._fuse_ranked_lists(ranked_lists)
        should_rerank = use_rerank if use_rerank is not None else strategy in _RERANK_STRATEGIES
        if should_rerank and rows:
            rows = self.rerank_service.rerank(db, query, rows, top_k=max(candidate_limit, top_k))
        seen: set[int] = set()
        citations: list[Citation] = []
        for row in rows:
            chunk_id = int(row["chunk_id"])
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            citations.append(Citation(**self._prepare_citation_row(db, row, strategy)))
            if len(citations) >= top_k:
                break
        return citations

    def debug_search(
        self,
        db: Session,
        knowledge_base_ids: list[int],
        query: str,
        strategy: str,
        top_k: int = 6,
    ) -> RetrievalDebugResponse:
        strategy = self._normalize_strategy(strategy)
        candidate_limit = max(top_k * 8, 32)
        rows: list[dict[str, Any]] = []
        if len(knowledge_base_ids) == 1:
            rows = self._debug_rows_for_strategy(db, knowledge_base_ids[0], query, strategy, candidate_limit)
        else:
            if strategy == "fts_only":
                ranked_lists = [self._search_fts(db, kb_id, query, candidate_limit) for kb_id in knowledge_base_ids]
            elif strategy == "vector_only":
                ranked_lists = [self._search_vectors(db, kb_id, query, candidate_limit) for kb_id in knowledge_base_ids]
            else:
                ranked_lists = [self._hybrid_ranked_rows(db, kb_id, query, candidate_limit) for kb_id in knowledge_base_ids]
            rows = self._fuse_ranked_lists(ranked_lists)
            if strategy in _RERANK_STRATEGIES and rows:
                rows = self.rerank_service.rerank(db, query, rows, top_k=max(candidate_limit, top_k))

        candidates: list[RetrievalDebugCandidate] = []
        seen: set[int] = set()
        for row in rows:
            chunk_id = int(row["chunk_id"])
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            citation_row = self._prepare_citation_row(db, row, row.get("source_strategy") or strategy)
            candidates.append(
                RetrievalDebugCandidate(
                    **citation_row,
                    rank=len(candidates) + 1,
                    debug=self._debug_payload(row),
                )
            )
            if len(candidates) >= top_k:
                break
        return RetrievalDebugResponse(
            query=query,
            strategy=strategy,
            candidate_count=len(candidates),
            candidates=candidates,
        )

    def search(self, db: Session, knowledge_base_id: int, query: str, top_k: int = 6) -> list[Citation]:
        return self.search_with_strategy(db, knowledge_base_id, query, DEFAULT_RETRIEVAL_STRATEGY, top_k=top_k)

    def search_with_strategy(
        self,
        db: Session,
        knowledge_base_id: int,
        query: str,
        strategy: str,
        top_k: int = 6,
        *,
        use_rerank: bool | None = None,
    ) -> list[Citation]:
        strategy = self._normalize_strategy(strategy)
        candidate_limit = max(top_k * 8, 32)
        t0 = time.perf_counter()

        if strategy in {"grep", "grep_search"}:
            result = self.grep_chunks(db, knowledge_base_id, query, top_k=top_k, mode="auto")
            self.last_timing = {"retrieve_ms": (time.perf_counter() - t0) * 1000}
            return result
        if strategy in {"symbol", "symbol_search"}:
            result = self.search_symbol(db, knowledge_base_id, query, top_k=top_k, exact=False, include_dependencies=True)
            self.last_timing = {"retrieve_ms": (time.perf_counter() - t0) * 1000}
            return result
        if strategy in {"read_context", "symbol_context"}:
            result = self.read_context_by_symbol(db, knowledge_base_id, query, max_chunks=top_k)
            self.last_timing = {"retrieve_ms": (time.perf_counter() - t0) * 1000}
            return result
        if strategy == "find_callers":
            result = self.find_callers(db, knowledge_base_id, query, top_k=top_k)
            self.last_timing = {"retrieve_ms": (time.perf_counter() - t0) * 1000}
            return result
        if strategy == "find_callees":
            result = self.find_callees(db, knowledge_base_id, query, top_k=top_k)
            self.last_timing = {"retrieve_ms": (time.perf_counter() - t0) * 1000}
            return result
        if strategy == "fts_only":
            result = self._finalize_rows(
                db,
                knowledge_base_id,
                query,
                self._search_fts(db, knowledge_base_id, query, candidate_limit),
                top_k,
                use_rerank=False,
                expand_context=False,
                source_strategy=strategy,
            )
            self.last_timing = {"retrieve_ms": (time.perf_counter() - t0) * 1000}
            return result
        if strategy == "vector_only":
            result = self._finalize_rows(
                db,
                knowledge_base_id,
                query,
                self._search_vectors(db, knowledge_base_id, query, candidate_limit),
                top_k,
                use_rerank=False,
                expand_context=False,
                source_strategy=strategy,
            )
            self.last_timing = {"retrieve_ms": (time.perf_counter() - t0) * 1000}
            return result
        if strategy == "hybrid_rrf_no_rerank":
            result = self._hybrid_search(
                db, knowledge_base_id, query, top_k, candidate_limit, use_rerank=False, source_strategy=strategy
            )
            self.last_timing = {"retrieve_ms": (time.perf_counter() - t0) * 1000}
            return result
        if strategy in {"multi_query", "rag_fusion"}:
            variants = self._query_variants(query)
            ranked_lists = [self._hybrid_ranked_rows(db, knowledge_base_id, item, candidate_limit) for item in variants]
            rows = self._fuse_ranked_lists(ranked_lists)
            should_rerank = use_rerank if use_rerank is not None else strategy == "rag_fusion"
            result = self._finalize_rows(db, knowledge_base_id, query, rows, top_k, use_rerank=should_rerank, source_strategy=strategy)
            self.last_timing = {"retrieve_ms": (time.perf_counter() - t0) * 1000}
            return result
        if strategy == "hyde":
            hyde_query = self._hyde_query(query)
            ranked_lists = [
                self._search_vectors(db, knowledge_base_id, hyde_query, candidate_limit),
                self._search_fts(db, knowledge_base_id, query, candidate_limit),
            ]
            rows = self._fuse_ranked_lists(ranked_lists)
            should_rerank = use_rerank if use_rerank is not None else True
            result = self._finalize_rows(db, knowledge_base_id, query, rows, top_k, use_rerank=should_rerank, source_strategy=strategy)
            self.last_timing = {"retrieve_ms": (time.perf_counter() - t0) * 1000}
            return result
        if strategy == "parent_child":
            should_rerank = use_rerank if use_rerank is not None else True
            result = self._hybrid_search(
                db,
                knowledge_base_id,
                query,
                top_k,
                candidate_limit,
                use_rerank=should_rerank,
                parent_child=True,
                source_strategy=strategy,
            )
            self.last_timing = {"retrieve_ms": (time.perf_counter() - t0) * 1000}
            return result
        if strategy == "dependency_graphrag":
            should_rerank = use_rerank if use_rerank is not None else True
            result = self._hybrid_search(
                db,
                knowledge_base_id,
                query,
                top_k,
                candidate_limit,
                use_rerank=should_rerank,
                dependency_max=8,
                force_dependency=True,
                source_strategy=strategy,
            )
            self.last_timing = {"retrieve_ms": (time.perf_counter() - t0) * 1000}
            return result
        if strategy == "agentic_react":
            should_rerank = use_rerank if use_rerank is not None else True
            result = self._hybrid_search(db, knowledge_base_id, query, top_k, candidate_limit, use_rerank=should_rerank, source_strategy=strategy)
            self.last_timing = {"retrieve_ms": (time.perf_counter() - t0) * 1000}
            return result
        should_rerank = use_rerank if use_rerank is not None else strategy in _RERANK_STRATEGIES
        result = self._hybrid_search(db, knowledge_base_id, query, top_k, candidate_limit, use_rerank=should_rerank, source_strategy=strategy)
        self.last_timing = {"retrieve_ms": (time.perf_counter() - t0) * 1000}
        return result

    def grep_chunks(
        self,
        db: Session,
        knowledge_base_id: int,
        pattern: str,
        top_k: int = 10,
        *,
        case_sensitive: bool = False,
        mode: str = "auto",
        context_lines: int = 2,
        max_scan: int = 1000,
    ) -> list[Citation]:
        """Search chunk text like grep and return line-oriented citations."""
        pattern = pattern.strip()
        if not pattern:
            return []
        top_k = min(max(top_k, 1), 20)
        context_lines = min(max(context_lines, 0), 5)
        max_scan = min(max(max_scan, top_k), 2000)
        mode = mode.strip().lower() or "auto"
        if mode not in {"auto", "fts", "literal", "regex"}:
            mode = "auto"
        if mode == "auto":
            mode = "regex" if self._looks_like_regex(pattern) else "literal"

        if mode == "fts":
            rows = self._search_fts(db, knowledge_base_id, pattern, top_k)
            return [
                Citation(**self._prepare_citation_row(db, {**row, "match_type": "grep", "metadata": {"mode": "fts"}}, "grep"))
                for row in rows
            ]

        if mode == "regex" and len(pattern) > 200:
            pattern = re.escape(pattern[:200])
            mode = "literal"

        candidates = self._grep_candidate_chunks(db, knowledge_base_id, pattern, case_sensitive, mode, max_scan)
        results: list[Citation] = []
        for row in candidates:
            matches = self._line_matches(str(row["content"]), pattern, mode, case_sensitive, context_lines)
            for match in matches:
                result = {
                    "chunk_id": int(row["chunk_id"]),
                    "knowledge_base_id": int(row["knowledge_base_id"]),
                    "document_id": int(row["document_id"]),
                    "file_name": row["file_name"],
                    "file_path": row["file_path"],
                    "title": row["title"],
                    "section": row["section"],
                    "symbol_name": row["symbol_name"],
                    "chunk_kind": row["chunk_kind"] or "",
                    "excerpt": match["excerpt"],
                    "match_type": "grep",
                    "line_start": match["line_start"],
                    "line_end": match["line_end"],
                    "metadata": {
                        "mode": mode,
                        "pattern": pattern,
                        "match_line": match["match_line"],
                    },
                    "source_strategy": "grep",
                }
                results.append(Citation(**self._prepare_citation_row(db, result, "grep")))
                if len(results) >= top_k:
                    return results
        return results

    def search_symbol(
        self,
        db: Session,
        knowledge_base_id: int,
        symbol: str,
        top_k: int = 10,
        *,
        exact: bool = True,
        include_body: bool = True,
        include_summary: bool = True,
        include_dependencies: bool = False,
    ) -> list[Citation]:
        """Find IDL symbol chunks with deterministic summary/body ordering."""
        symbol = symbol.strip()
        if not symbol:
            return []
        top_k = min(max(top_k, 1), 20)
        rows = self._symbol_rows(db, knowledge_base_id, symbol, exact=exact)
        priority = {"symbol_summary": 0, "overview": 1, "symbol_body": 2}
        filtered: list[dict[str, Any]] = []
        for row in rows:
            chunk_kind = row.get("chunk_kind") or ""
            if chunk_kind == "symbol_summary" and not include_summary:
                continue
            if chunk_kind == "symbol_body" and not include_body:
                continue
            row["match_type"] = "symbol"
            row["source_strategy"] = "symbol"
            row["metadata"] = {"query_symbol": symbol, "exact": exact}
            filtered.append(row)
        filtered.sort(key=lambda item: (priority.get(item.get("chunk_kind") or "", 99), item.get("chunk_id") or 0))

        selected = filtered[:top_k]
        seen = {int(row["chunk_id"]) for row in selected}
        if include_dependencies and len(selected) < top_k:
            selected.extend(
                self._expand_by_dependencies(
                    db,
                    knowledge_base_id,
                    selected,
                    seen,
                    top_k,
                    max_expansion=top_k - len(selected),
                )
            )
        return [Citation(**self._prepare_citation_row(db, row, row.get("source_strategy") or "symbol")) for row in selected[:top_k]]

    def read_context_by_chunk_id(
        self,
        db: Session,
        knowledge_base_id: int,
        chunk_id: int,
        *,
        before: int = 1,
        after: int = 1,
        include_companion: bool = True,
        include_dependencies: bool = True,
        max_chunks: int = 8,
    ) -> list[Citation]:
        """Read stable context around a chunk: current, companions, neighbors, dependencies."""
        max_chunks = min(max(max_chunks, 1), 20)
        before = min(max(before, 0), 5)
        after = min(max(after, 0), 5)
        current = self._chunk_row_by_id(db, knowledge_base_id, chunk_id)
        if current is None:
            return []

        selected: list[dict[str, Any]] = [{**current, "match_type": "read_context", "source_strategy": "read_context"}]
        seen = {int(current["chunk_id"])}

        if include_companion:
            companion = self._find_companion(db, current, seen)
            if companion is not None:
                companion["match_type"] = "read_context"
                selected.append(companion)
                seen.add(int(companion["chunk_id"]))

        for row in self._neighbor_rows(db, current, before=before, after=after):
            if len(selected) >= max_chunks:
                break
            if int(row["chunk_id"]) in seen:
                continue
            row["match_type"] = "read_context"
            row["source_strategy"] = "neighbor_context"
            selected.append(row)
            seen.add(int(row["chunk_id"]))

        if include_dependencies and len(selected) < max_chunks:
            selected.extend(
                self._expand_by_dependencies(
                    db,
                    knowledge_base_id,
                    selected,
                    seen,
                    max_chunks,
                    max_expansion=max_chunks - len(selected),
                )
            )
        return [Citation(**self._prepare_citation_row(db, row, row.get("source_strategy") or "read_context")) for row in selected[:max_chunks]]

    def read_context_by_symbol(
        self,
        db: Session,
        knowledge_base_id: int,
        symbol: str,
        *,
        include_callers: bool = False,
        include_callees: bool = True,
        max_chunks: int = 10,
    ) -> list[Citation]:
        """Read summary/body and dependency context for a symbol."""
        base = self.search_symbol(db, knowledge_base_id, symbol, top_k=max_chunks, include_dependencies=False)
        rows = [citation.model_dump() for citation in base]
        seen = {int(row["chunk_id"]) for row in rows}
        if include_callees and len(rows) < max_chunks:
            rows.extend(
                self._expand_by_dependencies(
                    db,
                    knowledge_base_id,
                    rows,
                    seen,
                    max_chunks,
                    max_expansion=max_chunks - len(rows),
                )
            )
        if include_callers and len(rows) < max_chunks:
            rows.extend(self._caller_context_rows(db, knowledge_base_id, symbol, seen, max_chunks - len(rows)))
        return [Citation(**self._prepare_citation_row(db, row, row.get("source_strategy") or "read_context")) for row in rows[:max_chunks]]

    def find_callers(self, db: Session, knowledge_base_id: int, symbol: str, top_k: int = 8) -> list[Citation]:
        symbol = symbol.strip()
        if not symbol:
            return []
        base_rows = self._symbol_rows(db, knowledge_base_id, symbol, exact=True)
        if not base_rows:
            base_rows = self._symbol_rows(db, knowledge_base_id, symbol, exact=False)
        canonical_symbol = next((row["symbol_name"] for row in base_rows if row.get("symbol_name")), symbol)
        seen: set[int] = set()
        rows = self._caller_context_rows(db, knowledge_base_id, canonical_symbol, seen, min(max(top_k, 1), 20))
        return [Citation(**self._prepare_citation_row(db, row, "find_callers")) for row in rows]

    def find_callees(self, db: Session, knowledge_base_id: int, symbol: str, top_k: int = 8) -> list[Citation]:
        symbol = symbol.strip()
        if not symbol:
            return []
        base_rows = self._symbol_rows(db, knowledge_base_id, symbol, exact=True)
        if not base_rows:
            base_rows = self._symbol_rows(db, knowledge_base_id, symbol, exact=False)
        seed_rows = []
        seen = set()
        for row in base_rows:
            if row.get("symbol_name") and row["symbol_name"].lower() == symbol.lower():
                seed_rows.append(row)
                seen.add(int(row["chunk_id"]))
                break
        if not seed_rows and base_rows:
            seed_rows.append(base_rows[0])
            seen.add(int(base_rows[0]["chunk_id"]))
        rows = self._expand_by_dependencies(
            db,
            knowledge_base_id,
            seed_rows,
            seen,
            min(max(top_k, 1), 20),
            max_expansion=min(max(top_k, 1), 20),
        )
        for row in rows:
            row["source_strategy"] = "find_callees"
            row["match_type"] = "dependency"
        return [Citation(**self._prepare_citation_row(db, row, "find_callees")) for row in rows]

    def _hybrid_search(
        self,
        db: Session,
        knowledge_base_id: int,
        query: str,
        top_k: int,
        candidate_limit: int,
        *,
        use_rerank: bool,
        parent_child: bool = False,
        dependency_max: int = 4,
        force_dependency: bool = False,
        source_strategy: str = DEFAULT_RETRIEVAL_STRATEGY,
    ) -> list[Citation]:
        rows = self._hybrid_ranked_rows(db, knowledge_base_id, query, candidate_limit)
        return self._finalize_rows(
            db,
            knowledge_base_id,
            query,
            rows,
            top_k,
            use_rerank=use_rerank,
            parent_child=parent_child,
            dependency_max=dependency_max,
            force_dependency=force_dependency,
            source_strategy=source_strategy,
        )

    def _hybrid_ranked_rows(
        self,
        db: Session,
        knowledge_base_id: int,
        query: str,
        candidate_limit: int,
    ) -> list[dict[str, Any]]:
        return self._rank_rows(
            query,
            [
                self._search_fts(db, knowledge_base_id, query, candidate_limit),
                self._search_vectors(db, knowledge_base_id, query, candidate_limit),
            ],
        )[:candidate_limit]

    def _rank_rows(self, query: str, ranked_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        merged: dict[int, dict[str, Any]] = {}
        rrf_scores: dict[int, float] = defaultdict(float)
        for rows in ranked_lists:
            for rank, row in enumerate(rows, start=1):
                merged[row["chunk_id"]] = row
                rrf_scores[row["chunk_id"]] += 1.0 / (rank + 10)
        if not merged:
            return []

        rrf_min = min(rrf_scores.values())
        rrf_max = max(rrf_scores.values())
        rrf_range = rrf_max - rrf_min or 1.0
        rrf_normalized = {cid: (score - rrf_min) / rrf_range for cid, score in rrf_scores.items()}

        document_counts = Counter(row["document_id"] for row in merged.values())
        lowered_query = query.lower()
        heuristic_raw: dict[int, float] = defaultdict(float)
        for chunk_id, row in merged.items():
            symbol_name = (row.get("symbol_name") or "").lower()
            title = (row.get("title") or "").lower()
            section = (row.get("section") or "").lower()
            chunk_kind = row.get("chunk_kind") or ""
            if symbol_name and symbol_name in lowered_query:
                heuristic_raw[chunk_id] += 3.5
            if title and title in lowered_query:
                heuristic_raw[chunk_id] += 2.0
            if section and section in lowered_query:
                heuristic_raw[chunk_id] += 1.0
            if chunk_kind == "symbol_summary":
                heuristic_raw[chunk_id] += 1.5
            elif chunk_kind == "overview":
                heuristic_raw[chunk_id] += 0.8
            heuristic_raw[chunk_id] += min(document_counts[row["document_id"]], 3) * 0.2

        h_min = min(heuristic_raw.values()) if heuristic_raw else 0.0
        h_max = max(heuristic_raw.values()) if heuristic_raw else 1.0
        h_range = h_max - h_min or 1.0
        heuristic_normalized = {cid: (score - h_min) / h_range for cid, score in heuristic_raw.items()}

        scores = {
            chunk_id: 0.65 * rrf_normalized.get(chunk_id, 0.0) + 0.35 * heuristic_normalized.get(chunk_id, 0.0)
            for chunk_id in merged
        }
        for chunk_id, row in merged.items():
            row["rrf_score"] = rrf_scores.get(chunk_id, 0.0)
            row["rrf_normalized"] = rrf_normalized.get(chunk_id, 0.0)
            row["heuristic_score"] = heuristic_raw.get(chunk_id, 0.0)
            row["heuristic_normalized"] = heuristic_normalized.get(chunk_id, 0.0)
            row["combined_score"] = scores[chunk_id]
        return sorted(
            merged.values(),
            key=lambda row: (row["combined_score"], row.get("chunk_kind") == "symbol_summary"),
            reverse=True,
        )

    def _fuse_ranked_lists(self, ranked_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        merged: dict[int, dict[str, Any]] = {}
        scores: dict[int, float] = defaultdict(float)
        source_ranks: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for list_index, rows in enumerate(ranked_lists):
            for rank, row in enumerate(rows, start=1):
                chunk_id = int(row["chunk_id"])
                merged[chunk_id] = row
                scores[chunk_id] += 1.0 / (rank + 60)
                source_ranks[chunk_id].append({"list_index": list_index, "rank": rank})
        for chunk_id, row in merged.items():
            row["fused_score"] = scores[chunk_id]
            row["source_ranks"] = source_ranks[chunk_id]
        return sorted(merged.values(), key=lambda row: row["fused_score"], reverse=True)

    def _finalize_rows(
        self,
        db: Session,
        knowledge_base_id: int,
        query: str,
        rows: list[dict[str, Any]],
        top_k: int,
        *,
        use_rerank: bool,
        parent_child: bool = False,
        dependency_max: int = 4,
        force_dependency: bool = False,
        expand_context: bool = True,
        source_strategy: str = DEFAULT_RETRIEVAL_STRATEGY,
    ) -> list[Citation]:
        candidate_limit = max(top_k * 8, 32)
        ordered_rows = rows[:candidate_limit]
        if use_rerank:
            t_rerank = time.perf_counter()
            ordered_rows = self.rerank_service.rerank(db, query, ordered_rows, top_k=max(candidate_limit, top_k))
            self.last_timing["rerank_ms"] = (time.perf_counter() - t_rerank) * 1000

        selected: list[dict[str, Any]] = []
        seen_chunk_ids: set[int] = set()
        group_counts: dict[tuple[int, str], int] = defaultdict(int)
        reserved_for_dependencies = min(dependency_max, max(1, top_k // 2)) if force_dependency else 0
        if parent_child and not force_dependency:
            initial_limit = max(1, top_k // 2)
        else:
            initial_limit = max(1, top_k - reserved_for_dependencies)
        for row in ordered_rows:
            group_key = (row["document_id"], row.get("symbol_name") or row.get("title") or "")
            if group_counts[group_key] >= 2:
                continue
            selected.append(row)
            seen_chunk_ids.add(row["chunk_id"])
            group_counts[group_key] += 1
            if len(selected) >= initial_limit:
                break

        if expand_context and force_dependency:
            selected.extend(
                self._expand_by_dependencies(
                    db,
                    knowledge_base_id,
                    selected,
                    seen_chunk_ids,
                    top_k,
                    max_expansion=dependency_max,
                )
            )

        if expand_context:
            for row in list(selected):
                if len(selected) >= top_k and not parent_child:
                    break
                if row.get("chunk_kind") != "symbol_body" and not parent_child:
                    continue
                companion = self._find_companion(db, row, seen_chunk_ids)
                if companion is not None:
                    selected.append(companion)
                    seen_chunk_ids.add(companion["chunk_id"])

        if expand_context and not force_dependency and len(selected) < top_k:
            selected.extend(
                self._expand_by_dependencies(
                    db,
                    knowledge_base_id,
                    selected,
                    seen_chunk_ids,
                    top_k,
                    max_expansion=dependency_max,
                )
            )

        return [Citation(**self._prepare_citation_row(db, row, source_strategy)) for row in selected[:top_k]]

    def _normalize_strategy(self, strategy: str | None) -> str:
        return (strategy or DEFAULT_RETRIEVAL_STRATEGY).strip().lower() or DEFAULT_RETRIEVAL_STRATEGY

    def _merge_citations(self, citations: list[Citation], top_k: int, source_strategy: str) -> list[Citation]:
        seen: set[int] = set()
        merged: list[Citation] = []
        for citation in citations:
            if citation.chunk_id in seen:
                continue
            seen.add(citation.chunk_id)
            merged.append(citation.model_copy(update={"source_strategy": source_strategy}))
            if len(merged) >= top_k:
                break
        return merged

    def _debug_rows_for_strategy(
        self,
        db: Session,
        knowledge_base_id: int,
        query: str,
        strategy: str,
        candidate_limit: int,
    ) -> list[dict[str, Any]]:
        if strategy in {"grep", "grep_search"}:
            return [citation.model_dump() for citation in self.grep_chunks(db, knowledge_base_id, query, top_k=candidate_limit)]
        if strategy in {"symbol", "symbol_search"}:
            return [
                citation.model_dump()
                for citation in self.search_symbol(db, knowledge_base_id, query, top_k=candidate_limit, exact=False, include_dependencies=True)
            ]
        if strategy in {"read_context", "symbol_context"}:
            return [citation.model_dump() for citation in self.read_context_by_symbol(db, knowledge_base_id, query, max_chunks=candidate_limit)]
        if strategy == "find_callers":
            return [citation.model_dump() for citation in self.find_callers(db, knowledge_base_id, query, top_k=candidate_limit)]
        if strategy == "find_callees":
            return [citation.model_dump() for citation in self.find_callees(db, knowledge_base_id, query, top_k=candidate_limit)]
        if strategy == "fts_only":
            return self._search_fts(db, knowledge_base_id, query, candidate_limit)
        if strategy == "vector_only":
            return self._search_vectors(db, knowledge_base_id, query, candidate_limit)
        if strategy == "hybrid_rrf_no_rerank":
            return self._hybrid_ranked_rows(db, knowledge_base_id, query, candidate_limit)
        if strategy in {"multi_query", "rag_fusion"}:
            ranked_lists = [self._hybrid_ranked_rows(db, knowledge_base_id, item, candidate_limit) for item in self._query_variants(query)]
            rows = self._fuse_ranked_lists(ranked_lists)
            return self.rerank_service.rerank(db, query, rows, top_k=candidate_limit) if strategy == "rag_fusion" and rows else rows
        if strategy == "hyde":
            rows = self._fuse_ranked_lists([
                self._search_vectors(db, knowledge_base_id, self._hyde_query(query), candidate_limit),
                self._search_fts(db, knowledge_base_id, query, candidate_limit),
            ])
            return self.rerank_service.rerank(db, query, rows, top_k=candidate_limit) if rows else rows
        rows = self._hybrid_ranked_rows(db, knowledge_base_id, query, candidate_limit)
        if strategy in {"hybrid_rrf", "parent_child", "dependency_graphrag", "agentic_react"}:
            return self.rerank_service.rerank(db, query, rows, top_k=candidate_limit) if rows else rows
        return rows

    def _prepare_citation_row(self, db: Session, row: dict[str, Any], source_strategy: str) -> dict[str, Any]:
        knowledge_base_id = row.get("knowledge_base_id")
        knowledge_base_name = row.get("knowledge_base_name")
        if (knowledge_base_id is None or knowledge_base_name is None) and row.get("document_id") is not None:
            document_info = self.metadata_store.document_info(db, int(row["document_id"]))
            if document_info is not None:
                knowledge_base_id = knowledge_base_id if knowledge_base_id is not None else document_info[0]
                knowledge_base_name = knowledge_base_name or document_info[1]
        line_start = row.get("line_start")
        line_end = row.get("line_end")
        if (line_start is None or line_end is None) and row.get("chunk_id") is not None:
            chunk_meta = self.metadata_store.chunk_metadata(db, int(row["chunk_id"]))
            line_start = line_start if line_start is not None else chunk_meta.get("start_line")
            line_end = line_end if line_end is not None else chunk_meta.get("end_line")
        metadata = dict(row.get("metadata") or {})
        for key in (
            "fts_score",
            "vector_score",
            "rrf_score",
            "combined_score",
            "fused_score",
            "rerank_score",
            "source_ranks",
        ):
            if row.get(key) is not None and key not in metadata:
                metadata[key] = row[key]
        return {
            "chunk_id": int(row["chunk_id"]),
            "document_id": int(row["document_id"]),
            "file_name": row.get("file_name") or "",
            "file_path": row.get("file_path") or "",
            "title": row.get("title"),
            "section": row.get("section"),
            "symbol_name": row.get("symbol_name"),
            "excerpt": row.get("excerpt") or "",
            "knowledge_base_id": knowledge_base_id,
            "knowledge_base_name": knowledge_base_name,
            "chunk_kind": row.get("chunk_kind") or None,
            "score": self._row_score(row),
            "source_strategy": source_strategy,
            "match_type": row.get("match_type"),
            "line_start": line_start,
            "line_end": line_end,
            "metadata": metadata,
        }

    @staticmethod
    def _row_score(row: dict[str, Any]) -> float | None:
        for key in ("rerank_score", "combined_score", "fused_score", "vector_score", "fts_score", "rrf_score", "score"):
            value = row.get(key)
            if value is not None:
                return float(value)
        return None

    def _debug_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        score_keys = (
            "fts_score",
            "vector_score",
            "rrf_score",
            "rrf_normalized",
            "heuristic_score",
            "heuristic_normalized",
            "combined_score",
            "fused_score",
            "rerank_score",
        )
        scores = {key: row[key] for key in score_keys if row.get(key) is not None}
        match = {
            "match_type": row.get("match_type"),
            "source_strategy": row.get("source_strategy"),
            "chunk_kind": row.get("chunk_kind") or None,
            "symbol_name": row.get("symbol_name"),
            "line_start": row.get("line_start"),
            "line_end": row.get("line_end"),
        }
        fusion = {"source_ranks": row.get("source_ranks")} if row.get("source_ranks") is not None else {}
        payload = {
            "score": self._row_score(row),
            "scores": scores,
            "match": {key: value for key, value in match.items() if value is not None},
            "fusion": fusion,
        }
        for key, value in {**scores, **fusion}.items():
            payload[key] = value
        payload["chunk_kind"] = row.get("chunk_kind") or None
        payload["source"] = row.get("source_strategy") or None
        return payload

    def _query_variants(self, query: str) -> list[str]:
        variants = [query]
        lowered = query.lower()
        symbol_terms = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", query)
        for term in symbol_terms:
            split_term = term.replace("_", " ")
            variants.append(term)
            if split_term != term:
                variants.append(split_term)
        aliases = {
            "栅格": "raster image ENVI raster",
            "批处理": "batch headless envi /headless",
            "近红外": "NIR near infrared",
            "重采样": "resampling resize grid",
            "坐标系": "spatial reference projection coordinate",
            "依赖": "dependency caller callee function procedure",
            "调用": "call dependency caller callee",
        }
        for key, value in aliases.items():
            if key in query or value.lower() in lowered:
                variants.append(f"{query} {value}")
        return list(dict.fromkeys(item.strip() for item in variants if item.strip()))[:5]

    def _hyde_query(self, query: str) -> str:
        return (
            f"{query}\n"
            "IDL ENVI answer should mention relevant routines, parameters, symbols, files, "
            "raster processing steps, error handling, and code examples."
        )

    def _find_companion(
        self,
        db: Session,
        row: dict[str, Any],
        seen_chunk_ids: set[int],
    ) -> dict[str, Any] | None:
        chunk_kinds = ["symbol_summary", "struct_def", "common_block", "overview"]
        rows = db.execute(
            select(
                Chunk.id,
                Chunk.document_id,
                Document.file_name,
                Document.file_path,
                Chunk.title,
                Chunk.section,
                Chunk.symbol_name,
                Chunk.content,
                Chunk.meta_json,
            )
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.document_id == row["document_id"], Document.status == "ready")
        ).mappings()
        candidates: list[dict[str, Any]] = []
        for candidate in rows:
            chunk_id = int(candidate["id"])
            if chunk_id in seen_chunk_ids:
                continue
            meta = candidate["meta_json"] or {}
            chunk_kind = meta.get("chunk_kind", "")
            if chunk_kind not in chunk_kinds:
                continue
            if chunk_kind == "symbol_summary" and candidate["symbol_name"] != row.get("symbol_name"):
                continue
            candidates.append(
                {
                    "chunk_id": chunk_id,
                    "knowledge_base_id": row.get("knowledge_base_id"),
                    "document_id": int(candidate["document_id"]),
                    "file_name": candidate["file_name"],
                    "file_path": candidate["file_path"],
                    "title": candidate["title"],
                    "section": candidate["section"],
                    "symbol_name": candidate["symbol_name"],
                    "chunk_kind": chunk_kind,
                    "line_start": meta.get("start_line"),
                    "line_end": meta.get("end_line"),
                    "excerpt": str(candidate["content"])[:600],
                    "source_strategy": "companion",
                }
            )
        candidates.sort(key=lambda item: chunk_kinds.index(item["chunk_kind"]))
        return candidates[0] if candidates else None

    def _expand_by_dependencies(
        self,
        db: Session,
        knowledge_base_id: int,
        selected: list[dict[str, Any]],
        seen_chunk_ids: set[int],
        top_k: int,
        max_expansion: int = 4,
    ) -> list[dict[str, Any]]:
        """根据符号依赖图谱，拉入初始结果中符号调用的其他符号的代码。"""
        # 收集初始结果中的符号名
        initial_symbols: set[str] = set()
        for row in selected:
            sym = row.get("symbol_name")
            if sym:
                initial_symbols.add(sym)

        if not initial_symbols:
            return []

        # 查询这些符号调用了哪些其他符号
        deps = db.execute(
            select(SymbolDependency.callee_symbol)
            .where(
                SymbolDependency.knowledge_base_id == knowledge_base_id,
                SymbolDependency.caller_symbol.in_(list(initial_symbols)),
            )
            .distinct()
        ).all()

        callee_symbols = [row[0] for row in deps if row[0] not in initial_symbols]
        if not callee_symbols:
            return []

        # 从 SQL 获取被调用符号的 chunk（优先 symbol_summary）
        expanded: list[dict[str, Any]] = []
        for callee_sym in callee_symbols:
            if len(expanded) >= max_expansion:
                break
            rows = db.execute(
                select(
                    Chunk.id,
                    Chunk.document_id,
                    Document.file_name,
                    Document.file_path,
                    Chunk.title,
                    Chunk.section,
                    Chunk.symbol_name,
                    Chunk.content,
                    Chunk.meta_json,
                )
                .join(Document, Document.id == Chunk.document_id)
                .where(
                    Chunk.knowledge_base_id == knowledge_base_id,
                    Chunk.symbol_name == callee_sym,
                    Document.status == "ready",
                )
                .limit(3)
            ).mappings()

            priority = {"symbol_summary": 0, "symbol_body": 1}
            sorted_rows = sorted(rows, key=lambda r: priority.get((r["meta_json"] or {}).get("chunk_kind", ""), 99))

            for row in sorted_rows:
                if len(expanded) >= max_expansion:
                    break
                chunk_id = int(row["id"])
                if chunk_id in seen_chunk_ids:
                    continue
                meta = row["meta_json"] or {}
                expanded.append({
                    "chunk_id": chunk_id,
                    "knowledge_base_id": knowledge_base_id,
                    "document_id": int(row["document_id"]),
                    "file_name": row["file_name"],
                    "file_path": row["file_path"],
                    "title": row["title"],
                    "section": row["section"],
                    "symbol_name": row["symbol_name"],
                    "chunk_kind": meta.get("chunk_kind", ""),
                    "line_start": meta.get("start_line"),
                    "line_end": meta.get("end_line"),
                    "excerpt": str(row["content"])[:600],
                    "source_strategy": "dependency_expansion",
                })
                seen_chunk_ids.add(chunk_id)

        return expanded

    def _grep_candidate_chunks(
        self,
        db: Session,
        knowledge_base_id: int,
        pattern: str,
        case_sensitive: bool,
        mode: str,
        max_scan: int,
    ) -> list[dict[str, Any]]:
        content_expr = Chunk.content if case_sensitive else func.lower(Chunk.content)
        pattern_value = pattern if case_sensitive else pattern.lower()
        stmt = (
            select(
                Chunk.id.label("chunk_id"),
                Chunk.knowledge_base_id.label("knowledge_base_id"),
                Chunk.document_id.label("document_id"),
                Document.file_name,
                Document.file_path,
                Chunk.title,
                Chunk.section,
                Chunk.symbol_name,
                Chunk.content,
                Chunk.meta_json,
                Chunk.chunk_index,
            )
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.knowledge_base_id == knowledge_base_id, Document.status == "ready")
            .order_by(Document.created_at.desc(), Chunk.chunk_index.asc(), Chunk.id.asc())
            .limit(max_scan)
        )
        if mode == "literal":
            stmt = stmt.where(content_expr.contains(pattern_value))
        rows = db.execute(stmt).mappings().all()
        return [self._chunk_mapping_to_row(row, include_content=True) for row in rows]

    @staticmethod
    def _looks_like_regex(pattern: str) -> bool:
        return bool(re.search(r"[\\[\\]().*+?{}|^$]", pattern))

    def _line_matches(
        self,
        content: str,
        pattern: str,
        mode: str,
        case_sensitive: bool,
        context_lines: int,
    ) -> list[dict[str, Any]]:
        flags = 0 if case_sensitive else re.IGNORECASE
        if mode == "regex":
            try:
                regex = re.compile(pattern, flags)
            except re.error:
                regex = re.compile(re.escape(pattern), flags)
        else:
            regex = re.compile(re.escape(pattern), flags)

        lines = content.splitlines() or [content]
        matches: list[dict[str, Any]] = []
        for index, line in enumerate(lines):
            if not regex.search(line):
                continue
            start = max(0, index - context_lines)
            end = min(len(lines), index + context_lines + 1)
            matches.append(
                {
                    "line_start": start + 1,
                    "line_end": end,
                    "match_line": index + 1,
                    "excerpt": "\n".join(lines[start:end]).strip(),
                }
            )
        return matches

    def _symbol_rows(self, db: Session, knowledge_base_id: int, symbol: str, *, exact: bool) -> list[dict[str, Any]]:
        stmt = (
            select(
                Chunk.id.label("chunk_id"),
                Chunk.knowledge_base_id.label("knowledge_base_id"),
                Chunk.document_id.label("document_id"),
                Document.file_name,
                Document.file_path,
                Chunk.title,
                Chunk.section,
                Chunk.symbol_name,
                Chunk.content,
                Chunk.meta_json,
                Chunk.chunk_index,
            )
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.knowledge_base_id == knowledge_base_id, Document.status == "ready")
        )
        if exact:
            stmt = stmt.where(func.lower(Chunk.symbol_name) == symbol.lower())
        else:
            like = f"%{symbol.lower()}%"
            stmt = stmt.where(
                func.lower(Chunk.symbol_name).like(like)
                | func.lower(Chunk.title).like(like)
                | func.lower(Chunk.content).like(like)
            )
        stmt = stmt.order_by(Chunk.document_id.asc(), Chunk.chunk_index.asc(), Chunk.id.asc()).limit(50)
        return [self._chunk_mapping_to_row(row) for row in db.execute(stmt).mappings().all()]

    def _chunk_row_by_id(self, db: Session, knowledge_base_id: int, chunk_id: int) -> dict[str, Any] | None:
        row = db.execute(
            select(
                Chunk.id.label("chunk_id"),
                Chunk.knowledge_base_id.label("knowledge_base_id"),
                Chunk.document_id.label("document_id"),
                Document.file_name,
                Document.file_path,
                Chunk.title,
                Chunk.section,
                Chunk.symbol_name,
                Chunk.content,
                Chunk.meta_json,
                Chunk.chunk_index,
            )
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.id == chunk_id, Chunk.knowledge_base_id == knowledge_base_id, Document.status == "ready")
        ).mappings().one_or_none()
        return self._chunk_mapping_to_row(row, include_content=True) if row is not None else None

    def _neighbor_rows(self, db: Session, row: dict[str, Any], *, before: int, after: int) -> list[dict[str, Any]]:
        chunk_index = row.get("chunk_index")
        if chunk_index is None:
            return []
        start = int(chunk_index) - before
        end = int(chunk_index) + after
        rows = db.execute(
            select(
                Chunk.id.label("chunk_id"),
                Chunk.knowledge_base_id.label("knowledge_base_id"),
                Chunk.document_id.label("document_id"),
                Document.file_name,
                Document.file_path,
                Chunk.title,
                Chunk.section,
                Chunk.symbol_name,
                Chunk.content,
                Chunk.meta_json,
                Chunk.chunk_index,
            )
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Chunk.document_id == int(row["document_id"]),
                Chunk.chunk_index >= start,
                Chunk.chunk_index <= end,
                Document.status == "ready",
            )
            .order_by(Chunk.chunk_index.asc(), Chunk.id.asc())
        ).mappings()
        return [self._chunk_mapping_to_row(item) for item in rows]

    def _caller_context_rows(
        self,
        db: Session,
        knowledge_base_id: int,
        symbol: str,
        seen_chunk_ids: set[int],
        limit: int,
    ) -> list[dict[str, Any]]:
        callers = db.execute(
            select(SymbolDependency.caller_symbol)
            .where(
                SymbolDependency.knowledge_base_id == knowledge_base_id,
                SymbolDependency.callee_symbol == symbol,
            )
            .distinct()
            .limit(limit)
        ).scalars().all()
        rows: list[dict[str, Any]] = []
        for caller in callers:
            for row in self._symbol_rows(db, knowledge_base_id, caller, exact=True):
                if len(rows) >= limit:
                    return rows
                chunk_id = int(row["chunk_id"])
                if chunk_id in seen_chunk_ids:
                    continue
                row["source_strategy"] = "caller_context"
                row["match_type"] = "read_context"
                row["metadata"] = {"caller_for": symbol}
                rows.append(row)
                seen_chunk_ids.add(chunk_id)
                break
        return rows

    @staticmethod
    def _chunk_mapping_to_row(row, *, include_content: bool = False) -> dict[str, Any]:
        meta = row["meta_json"] or {}
        content = str(row["content"] or "")
        payload = {
            "chunk_id": int(row["chunk_id"]),
            "knowledge_base_id": int(row["knowledge_base_id"]),
            "document_id": int(row["document_id"]),
            "file_name": row["file_name"],
            "file_path": row["file_path"],
            "title": row["title"],
            "section": row["section"],
            "symbol_name": row["symbol_name"],
            "chunk_kind": meta.get("chunk_kind", ""),
            "excerpt": content[:1200 if include_content else 600],
            "chunk_index": row.get("chunk_index"),
            "line_start": meta.get("start_line"),
            "line_end": meta.get("end_line"),
        }
        if include_content:
            payload["content"] = content
        return payload

    def _search_fts(self, db: Session, knowledge_base_id: int, query: str, top_k: int) -> list[dict[str, Any]]:
        return self.full_text_store.search(db, knowledge_base_id, self._fts_match_queries(query), top_k)

    def _fts_match_queries(self, query: str) -> list[str]:
        match_queries: list[str] = []
        for variant in self._query_variants(query):
            try:
                import jieba
                tokenized = " ".join(jieba.cut_for_search(variant))
            except ImportError:
                tokenized = variant
            cleaned = re.sub(r"[^\w\s]", " ", tokenized)
            terms = [term for term in cleaned.split() if len(term) > 1]
            for symbol in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", variant):
                terms.append(symbol)
                terms.extend(part for part in symbol.split("_") if len(part) > 1)
            unique_terms = list(dict.fromkeys(terms))[:20]
            if unique_terms:
                match_queries.append(" OR ".join(unique_terms))
        return list(dict.fromkeys(match_queries))

    def _search_vectors(self, db: Session, knowledge_base_id: int, query: str, top_k: int) -> list[dict[str, Any]]:
        vector = self.embedding_service.embed_query(db, query)
        results = self.vector_store.search(db, knowledge_base_id, vector, top_k)
        document_ids = {int(row["document_id"]) for row in results}
        if not document_ids:
            return []
        ready_document_ids = self.metadata_store.ready_document_ids(db, document_ids)

        rows: list[dict[str, Any]] = []
        for row in results:
            document_id = int(row["document_id"])
            if document_id not in ready_document_ids:
                continue
            rows.append(
                {
                    "chunk_id": int(row["chunk_id"]),
                    "knowledge_base_id": int(row.get("knowledge_base_id") or knowledge_base_id),
                    "document_id": document_id,
                    "file_name": row["file_name"],
                    "file_path": row["file_path"],
                    "title": row.get("title"),
                    "section": row.get("section"),
                    "symbol_name": row.get("symbol_name"),
                    "chunk_kind": row.get("chunk_kind") or "",
                    "vector_score": float(row.get("_distance") or 0.0),
                    "source_strategy": "vector",
                    "excerpt": str(row["content"])[:600],
                }
            )
        return rows
