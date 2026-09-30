"""对真实知识库跑多策略 Golden QA 评测，输出对比表格。

用法：
    uv run --project backend python backend/scripts/run_real_eval.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# 确保 backend 在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_app_settings
from app.db.database import get_engine, get_session_factory, init_database
from app.db.models import KnowledgeBase
from app.services.eval_runner import EvalRunner
from app.services.settings_service import get_runtime_settings


def main() -> None:
    init_database()
    session_factory = get_session_factory()
    db = session_factory()

    # 找到 chunk 最多的知识库（通常是主库）
    kbs = db.query(KnowledgeBase).order_by(KnowledgeBase.id).all()
    if not kbs:
        print("没有知识库，请先创建知识库并导入文档。")
        return

    # 选 chunk 最多的 KB
    from sqlalchemy import func
    from app.db.models import Chunk, Document

    kb_chunk_counts = []
    for kb in kbs:
        count = db.query(func.count(Chunk.id)).join(Document).filter(Document.knowledge_base_id == kb.id).scalar() or 0
        kb_chunk_counts.append((kb, count))
    kb_chunk_counts.sort(key=lambda x: x[1], reverse=True)

    print("=== 可用知识库 ===")
    for kb, cnt in kb_chunk_counts:
        print(f"  [{kb.id}] {kb.name}: {cnt} chunks")

    kb, chunk_count = kb_chunk_counts[0]
    if chunk_count == 0:
        print("主知识库没有 chunk，请先导入文档。")
        return

    print(f"\n使用知识库: [{kb.id}] {kb.name} ({chunk_count} chunks)")
    print(f"Golden QA: backend/tests/eval/golden_qa.json")

    # 策略列表
    strategies = [
        "fts_only",
        "vector_only",
        "hybrid_rrf_no_rerank",
    ]

    runner = EvalRunner(db, kb.id)

    all_results = {}
    for strategy in strategies:
        print(f"\n>>> 策略: {strategy}")
        t0 = time.perf_counter()
        reports = runner.run_all(top_k=6, strategy=strategy)
        elapsed = time.perf_counter() - t0
        summary = runner.summary(reports)
        summary["wall_time_s"] = round(elapsed, 2)
        all_results[strategy] = summary
        print(f"    total={summary['total']}, passed={summary['passed']}, "
              f"hit_rate={summary['hit_rate']:.3f}, mrr={summary['mrr']:.3f}, "
              f"precision={summary['precision_at_k']:.3f}, recall={summary['recall_at_k']:.3f}, "
              f"answer_relevance={summary['answer_relevance']:.3f}, "
              f"faithfulness={summary['faithfulness']:.3f}, "
              f"latency={summary['latency_ms']:.0f}ms, wall={elapsed:.1f}s")

    # 按类别拆分
    print("\n" + "=" * 80)
    print("=== 策略对比总表 ===")
    print(f"{'策略':<30} {'Total':>6} {'Pass':>6} {'Hit%':>7} {'MRR':>7} {'Prec':>7} {'Recall':>7} {'AnsRel':>7} {'Faith':>7} {'Latms':>7}")
    print("-" * 95)
    for strategy, s in all_results.items():
        print(f"{strategy:<30} {s['total']:>6} {s['passed']:>6} {s['hit_rate']:>7.3f} {s['mrr']:>7.3f} "
              f"{s['precision_at_k']:>7.3f} {s['recall_at_k']:>7.3f} "
              f"{s['answer_relevance']:>7.3f} {s['faithfulness']:>7.3f} {s['latency_ms']:>7.0f}")

    # Hybrid 相比 FTS 和 Vector 的提升
    h = all_results.get("hybrid_rrf_no_rerank", {})
    f = all_results.get("fts_only", {})
    v = all_results.get("vector_only", {})
    if h and f:
        print(f"\n=== Hybrid RRF vs FTS Only ===")
        print(f"  Hit Rate:  {f['hit_rate']:.3f} -> {h['hit_rate']:.3f} (delta {h['hit_rate'] - f['hit_rate']:+.3f})")
        print(f"  MRR:       {f['mrr']:.3f} -> {h['mrr']:.3f} (delta {h['mrr'] - f['mrr']:+.3f})")
        print(f"  Recall:    {f['recall_at_k']:.3f} -> {h['recall_at_k']:.3f} (delta {h['recall_at_k'] - f['recall_at_k']:+.3f})")
    if h and v:
        print(f"\n=== Hybrid RRF vs Vector Only ===")
        print(f"  Hit Rate:  {v['hit_rate']:.3f} -> {h['hit_rate']:.3f} (delta {h['hit_rate'] - v['hit_rate']:+.3f})")
        print(f"  MRR:       {v['mrr']:.3f} -> {h['mrr']:.3f} (delta {h['mrr'] - v['mrr']:+.3f})")
        print(f"  Recall:    {v['recall_at_k']:.3f} -> {h['recall_at_k']:.3f} (delta {h['recall_at_k'] - v['recall_at_k']:+.3f})")

    # 按类别拆分
    print("\n=== 按类别拆分 ===")
    categories = set()
    for s in all_results.values():
        categories.update(s.get("by_category", {}).keys())

    for cat in sorted(categories):
        print(f"\n  [{cat}]")
        print(f"  {'策略':<25} {'N':>4} {'Hit%':>7} {'MRR':>7} {'Recall':>7} {'Latms':>7}")
        print(f"  {'-' * 60}")
        for strategy in strategies:
            cs = all_results[strategy].get("by_category", {}).get(cat, {})
            if cs:
                print(f"  {strategy:<25} {cs.get('total', 0):>4} {cs.get('hit_rate', 0):>7.3f} "
                      f"{cs.get('mrr', 0):>7.3f} {cs.get('recall_at_k', 0):>7.3f} {cs.get('latency_ms', 0):>7.0f}")

    # 保存完整结果
    output_path = get_app_settings().logs_dir / "eval_multi_strategy.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n完整结果已保存: {output_path}")

    db.close()


if __name__ == "__main__":
    main()
