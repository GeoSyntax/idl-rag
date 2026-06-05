from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from app.services.settings_service import get_runtime_settings


class RerankService:
    """重排服务 — 对检索候选做 cross-encoder 精排。

    设计决策：
    - 为什么需要 rerank？
      当前检索排序是 RRF（倒数排名融合）+ 启发式加分，属于"无监督"融合——
      它不知道 query 和 document 的实际语义相关性。
      Cross-encoder 模型同时读取 query 和每个候选 document，输出 0-1 相关性分数，
      比 bi-encoder（embedding 模型）更精准，因为能看到 query-document 完整交互。

    - 为什么放在 RRF 之后？
      RRF + 启发式完成"粗排"（几百 → top 30-50），
      Rerank 是"精排"——在小候选集上用更强模型做最终排序。
      API 调用成本可控（只处理 30-50 个文档）。

    - 为什么支持优雅降级？
      Rerank 是可选增强，不是必需品。无配置时直接返回原始排序，
      API 调用失败时也回退到原始排序。系统不会因为 rerank 不可用而崩溃。

    - Provider 选择：
      支持 Cohere rerank API（注册送免费额度，适合学习）。
      也可以对接 Jina、本地 cross-encoder 等。
    """

    def rerank(
        self,
        db: Session,
        query: str,
        candidates: list[dict],
        top_k: int = 6,
    ) -> list[dict]:
        """对候选文档做重排序。

        Args:
            db: 数据库会话
            query: 用户查询
            candidates: 候选文档列表（每个是 dict，包含 excerpt 等字段）
            top_k: 返回前 K 个

        Returns:
            重排序后的候选列表（可能带有 rerank_score 字段）
        """
        settings = get_runtime_settings(db)
        rerank_api_key = getattr(settings, "rerank_api_key", "")
        rerank_api_url = getattr(settings, "rerank_api_url", "")
        rerank_model = getattr(settings, "rerank_model", "")

        # 无配置时直接返回原始排序（优雅降级）
        if not rerank_api_key or not rerank_api_url:
            return candidates[:top_k]

        try:
            documents = [self._format_candidate(row) for row in candidates]
            response = httpx.post(
                rerank_api_url,
                headers={"Authorization": f"Bearer {rerank_api_key}"},
                json={
                    "model": rerank_model or "rerank-multilingual-v3.0",
                    "query": query,
                    "documents": documents,
                    "top_n": top_k,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            results = response.json().get("results", [])

            reranked: list[dict] = []
            for r in results:
                idx = r.get("index", 0)
                if 0 <= idx < len(candidates):
                    row = candidates[idx].copy()
                    row["rerank_score"] = r.get("relevance_score", 0.0)
                    reranked.append(row)
            return reranked if reranked else candidates[:top_k]
        except Exception:  # noqa: BLE001
            # API 调用失败时回退到原始排序
            return candidates[:top_k]

    @staticmethod
    def _format_candidate(row: dict) -> str:
        parts = [
            f"file_name: {row.get('file_name') or ''}",
            f"title: {row.get('title') or ''}",
            f"section: {row.get('section') or ''}",
            f"symbol_name: {row.get('symbol_name') or ''}",
            f"chunk_kind: {row.get('chunk_kind') or ''}",
            f"excerpt: {row.get('excerpt') or ''}",
        ]
        return "\n".join(parts)
