"""Agent 工具系统 — 为 Agent Loop 提供可调用的工具函数。

设计决策：
- 每个工具是一个纯函数，接收 db + 参数，返回 ToolResult
- 工具不依赖 AgentService，避免循环依赖
- 工具结果是字符串，LLM 可以直接读取
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.api.schemas import Citation
from app.core.config import get_app_settings


@dataclass
class ToolResult:
    name: str
    output: str
    citations: list[Citation] = field(default_factory=list)
    strategy: str | None = None
    query: str | None = None
    next_suggestion: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def citations_count(self) -> int:
        return len(self.citations)


def tool_kb_search(
    db: Session,
    query: str,
    knowledge_base_id: int,
    top_k: int = 4,
) -> ToolResult:
    """检索知识库，返回相关代码片段。

    为什么独立成工具？
    - Agent 可以根据中间结果决定是否需要再次搜索（多轮检索）
    - 搜索 query 由 LLM 生成，比用户原始问题更精准
    """
    from app.services.retrieve_service import RetrievalService

    service = RetrievalService()
    citations = service.search(
        db=db,
        knowledge_base_id=knowledge_base_id,
        query=query,
        top_k=top_k,
    )
    return _citation_tool_result(
        "kb_search",
        query,
        citations,
        empty_output=f"未找到与「{query}」相关的内容。",
        max_excerpt=400,
        next_suggestion="如果语义检索结果不够精确，可以改用 grep_search 或 symbol_search 缩小范围。",
    )


def _format_citations_for_tool(citations: list[Citation], *, max_excerpt: int = 600) -> str:
    lines = [f"找到 {len(citations)} 个相关片段：\n"]
    for index, citation in enumerate(citations, 1):
        label = citation.symbol_name or citation.title or citation.file_name
        meta = []
        if citation.match_type:
            meta.append(citation.match_type)
        if citation.chunk_kind:
            meta.append(citation.chunk_kind)
        if citation.line_start:
            line_range = f"L{citation.line_start}"
            if citation.line_end and citation.line_end != citation.line_start:
                line_range += f"-L{citation.line_end}"
            meta.append(line_range)
        suffix = f" [{' / '.join(meta)}]" if meta else ""
        lines.append(f"[{index}] {label}（{citation.file_name}）{suffix}")
        lines.append(citation.excerpt.strip()[:max_excerpt])
        lines.append("")
    return "\n".join(lines).strip()


def _citation_tool_result(
    name: str,
    query: str,
    citations: list[Citation],
    *,
    empty_output: str,
    max_excerpt: int = 600,
    next_suggestion: str | None = None,
) -> ToolResult:
    if not citations:
        return ToolResult(
            name=name,
            output=empty_output,
            strategy=name,
            query=query,
            next_suggestion=next_suggestion,
            metadata={"citations_count": 0},
        )
    return ToolResult(
        name=name,
        output=_format_citations_for_tool(citations, max_excerpt=max_excerpt),
        citations=citations,
        strategy=name,
        query=query,
        next_suggestion=next_suggestion,
        metadata={"citations_count": len(citations)},
    )


def tool_grep_search(
    db: Session,
    query: str,
    knowledge_base_id: int,
    top_k: int = 6,
    mode: str = "auto",
) -> ToolResult:
    """在知识库 chunk 中执行 grep 风格精确检索。"""
    from app.services.retrieve_service import RetrievalService

    service = RetrievalService()
    citations = service.grep_chunks(
        db=db,
        knowledge_base_id=knowledge_base_id,
        pattern=query,
        top_k=top_k,
        mode=mode,
    )
    return _citation_tool_result(
        "grep_search",
        query,
        citations,
        empty_output=f"未找到包含「{query}」的片段。",
        next_suggestion="如果精确匹配为空，可以改用 symbol_search 查符号名，或用 kb_search 做语义检索。",
    )


def tool_symbol_search(
    db: Session,
    symbol: str,
    knowledge_base_id: int,
    top_k: int = 6,
    include_dependencies: bool = False,
) -> ToolResult:
    """按 IDL pro/function 符号名查找定义、摘要、函数体。"""
    from app.services.retrieve_service import RetrievalService

    service = RetrievalService()
    citations = service.search_symbol(
        db=db,
        knowledge_base_id=knowledge_base_id,
        symbol=symbol,
        top_k=top_k,
        exact=True,
        include_dependencies=include_dependencies,
    )
    if not citations:
        citations = service.search_symbol(
            db=db,
            knowledge_base_id=knowledge_base_id,
            symbol=symbol,
            top_k=top_k,
            exact=False,
            include_dependencies=include_dependencies,
        )
    return _citation_tool_result(
        "symbol_search",
        symbol,
        citations,
        empty_output=f"未找到符号「{symbol}」。",
        next_suggestion="如果符号名不完整，可以改用 grep_search 做文本匹配，或用 kb_search 做语义检索。",
    )


def tool_read_context(
    db: Session,
    knowledge_base_id: int,
    chunk_id: int | None = None,
    symbol_name: str | None = None,
    max_chunks: int = 8,
    include_dependencies: bool = True,
) -> ToolResult:
    """读取已命中 chunk 或符号周围的完整上下文。"""
    from app.services.retrieve_service import RetrievalService

    service = RetrievalService()
    if chunk_id is not None:
        citations = service.read_context_by_chunk_id(
            db=db,
            knowledge_base_id=knowledge_base_id,
            chunk_id=chunk_id,
            max_chunks=max_chunks,
            include_dependencies=include_dependencies,
        )
    elif symbol_name:
        citations = service.read_context_by_symbol(
            db=db,
            knowledge_base_id=knowledge_base_id,
            symbol=symbol_name,
            max_chunks=max_chunks,
            include_callees=include_dependencies,
        )
    else:
        return ToolResult(name="read_context", output="read_context 需要 chunk_id 或 symbol_name。")
    target = f"chunk_id={chunk_id}" if chunk_id is not None else f"symbol={symbol_name}"
    return _citation_tool_result(
        "read_context",
        target,
        citations,
        empty_output=f"未找到上下文：{target}。",
        max_excerpt=900,
        next_suggestion="如果上下文为空，可以先用 symbol_search 找到准确符号，再读取上下文。",
    )


def tool_find_callers(
    db: Session,
    symbol: str,
    knowledge_base_id: int,
    top_k: int = 8,
) -> ToolResult:
    """查找调用某个 IDL 符号的上游符号。"""
    from app.services.retrieve_service import RetrievalService

    service = RetrievalService()
    citations = service.find_callers(db, knowledge_base_id, symbol, top_k=top_k)
    return _citation_tool_result(
        "find_callers",
        symbol,
        citations,
        empty_output=f"未找到调用「{symbol}」的符号。",
        next_suggestion="如果 caller 为空，可以先用 symbol_search 确认符号名是否准确。",
    )


def tool_find_callees(
    db: Session,
    symbol: str,
    knowledge_base_id: int,
    top_k: int = 8,
) -> ToolResult:
    """查找某个 IDL 符号调用的下游符号。"""
    from app.services.retrieve_service import RetrievalService

    service = RetrievalService()
    citations = service.find_callees(db, knowledge_base_id, symbol, top_k=top_k)
    return _citation_tool_result(
        "find_callees",
        symbol,
        citations,
        empty_output=f"未找到「{symbol}」调用的符号。",
        next_suggestion="如果 callee 为空，可以用 read_context 直接读取符号正文确认调用关系。",
    )


def tool_analyze_code(code: str) -> ToolResult:
    """静态分析 IDL 代码，提取 pro/function 名、检查语法、统计行数。

    为什么需要？
    - LLM 不擅长精确统计代码结构
    - 快速提取符号名用于后续检索
    """
    lines = code.strip().splitlines()
    total_lines = len(lines)

    symbols: list[dict[str, str]] = []
    symbol_re = re.compile(r"^\s*(pro|function)\s+([A-Za-z_][\w$]*)", re.IGNORECASE)
    end_re = re.compile(r"^\s*end\b", re.IGNORECASE)

    current_symbol: str | None = None
    current_kind: str | None = None
    start_line = 0
    issues: list[str] = []

    for i, line in enumerate(lines, 1):
        match = symbol_re.match(line)
        if match:
            if current_symbol is not None:
                issues.append(f"第 {start_line} 行的 {current_kind} {current_symbol} 可能缺少 end 结束符")
            current_kind = match.group(1).lower()
            current_symbol = match.group(2)
            start_line = i
        elif end_re.match(line) and current_symbol is not None:
            symbols.append({
                "kind": current_kind,
                "name": current_symbol,
                "line": str(start_line),
            })
            current_symbol = None
            current_kind = None

    if current_symbol is not None:
        issues.append(f"第 {start_line} 行的 {current_kind} {current_symbol} 缺少 end 结束符")

    # 检查 compile_opt
    has_compile_opt = any("compile_opt" in line.lower() for line in lines)
    if not has_compile_opt and symbols:
        issues.append("建议添加 compile_opt idl2 声明")

    output_parts = [f"代码分析结果（共 {total_lines} 行）："]

    if symbols:
        output_parts.append(f"\n发现 {len(symbols)} 个符号定义：")
        for s in symbols:
            output_parts.append(f"  - {s['kind']} {s['name']}（第 {s['line']} 行）")
    else:
        output_parts.append("\n未发现 pro/function 定义。")

    if issues:
        output_parts.append("\n潜在问题：")
        for issue in issues:
            output_parts.append(f"  ⚠ {issue}")

    return ToolResult(name="analyze_code", output="\n".join(output_parts))


def tool_read_artifact(
    db: Session,
    session_id: int,
    artifact_id: str,
    owner_user_id: int,
) -> ToolResult:
    """读取之前生成的 .pro 文件内容。

    用于迭代修复：LLM 需要看到原代码才能修复。
    """
    from app.db.models import ChatMessage, ChatSession

    session = db.get(ChatSession, session_id)
    if session is None or session.owner_user_id != owner_user_id:
        return ToolResult(name="read_artifact", output="访问被拒绝：会话不存在。")

    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .all()
    )

    for message in messages:
        for artifact in message.artifacts_json or []:
            if not isinstance(artifact, dict):
                continue
            if artifact.get("id") != artifact_id:
                continue
            storage_path = artifact.get("storage_path")
            if not storage_path:
                continue
            resolved = Path(str(storage_path)).resolve()
            sandbox = get_app_settings().chat_artifacts_dir.resolve()
            if not resolved.is_relative_to(sandbox):
                return ToolResult(name="read_artifact", output="访问被拒绝：路径越界。")
            file_path = resolved
            if not file_path.is_file():
                return ToolResult(name="read_artifact", output=f"文件不存在：{file_path}")
            content = file_path.read_text(encoding="utf-8")
            file_name = artifact.get("file_name", "unknown.pro")
            return ToolResult(
                name="read_artifact",
                output=f"文件 {file_name} 的内容：\n\n{content}",
            )

    return ToolResult(name="read_artifact", output=f"未找到附件 {artifact_id}。")


def tool_fix_code(
    original_code: str,
    feedback: str,
    context: str = "",
) -> ToolResult:
    """基于用户反馈修复代码（返回修复指令，实际修复由 Agent Loop 的 LLM 完成）。

    为什么这个工具不直接调 LLM？
    - fix_code 的本质是把「原代码 + 用户反馈 + 参考资料」打包成 prompt
    - 真正的代码修复由 Agent Loop 的主 LLM 完成，这样它能看到完整上下文
    - 这个工具只负责格式化输入
    """
    parts = [
        "请基于以下信息修复代码：",
        "",
        "=== 原始代码 ===",
        original_code,
        "",
        "=== 用户反馈 ===",
        feedback,
    ]
    if context:
        parts.extend(["", "=== 参考资料 ===", context])

    parts.extend([
        "",
        "请输出修复后的完整代码，并解释修改了哪些地方以及为什么。",
        "输出格式：",
        "```idl",
        "修复后的完整代码",
        "```",
        "",
        "修改说明：",
        "1. ...",
    ])

    return ToolResult(name="fix_code", output="\n".join(parts))


def tool_lint_code(code: str) -> ToolResult:
    """使用 IDL 编译器检查代码语法。

    如果本地 IDL 环境不可用，降级到 tool_analyze_code 静态分析。
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".pro", encoding="utf-8", delete=False,
    ) as tmp:
        tmp.write(code)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            ["idl", "-e", f".compile '{tmp_path}'"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()

        if result.returncode == 0 and not stderr:
            return ToolResult(name="lint_code", output="✅ 代码编译通过，未发现语法错误。")

        # 解析错误信息
        errors = stderr or stdout
        error_lines = [line.strip() for line in errors.splitlines() if line.strip()]
        output_parts = [f"编译发现 {len(error_lines)} 个问题："]
        for line in error_lines[:20]:
            output_parts.append(f"  ❌ {line}")

        return ToolResult(name="lint_code", output="\n".join(output_parts))

    except FileNotFoundError:
        # IDL 命令不可用，降级到静态分析
        return tool_analyze_code(code)
    except subprocess.TimeoutExpired:
        return ToolResult(name="lint_code", output="⚠ 编译超时（30 秒），降级为静态分析。")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _research_project_context_payload(db: Session, project_id: int, owner_user_id: int) -> dict:
    """Return a safe, metadata-only view of a research project for the Agent.

    Raw paths, URIs, credentials, protocol blobs and raster metadata are
    intentionally excluded.  The Agent should use the research UI for any
    mutation or artifact download; this context only helps it plan the next
    researcher-reviewed step.
    """
    from app.db.models import (
        FormulaSpec,
        ResearchDataSnapshot,
        ResearchExperiment,
        ResearchKnowledgeSource,
    )
    from app.services.research_service import ResearchService

    project = ResearchService._get_owned_project(db, project_id, owner_user_id)
    protocol = project.protocol_json if isinstance(project.protocol_json, dict) else {}
    question = protocol.get("research_question", protocol.get("question"))
    temporal_scope = protocol.get("temporal_scope")
    safe_temporal = temporal_scope if isinstance(temporal_scope, dict) else None
    snapshots = (
        db.query(ResearchDataSnapshot)
        .filter(ResearchDataSnapshot.project_id == project_id)
        .order_by(ResearchDataSnapshot.id.desc())
        .limit(12)
        .all()
    )
    formulas = (
        db.query(FormulaSpec)
        .filter(FormulaSpec.project_id == project_id)
        .order_by(FormulaSpec.id.desc())
        .limit(20)
        .all()
    )
    experiments = (
        db.query(ResearchExperiment)
        .filter(ResearchExperiment.project_id == project_id)
        .order_by(ResearchExperiment.id.desc())
        .limit(20)
        .all()
    )
    sources = (
        db.query(ResearchKnowledgeSource)
        .filter(ResearchKnowledgeSource.project_id == project_id)
        .order_by(ResearchKnowledgeSource.id.asc())
        .all()
    )
    return {
        "project_id": project.id,
        "name": project.name,
        "entry_mode": project.entry_mode,
        "status": project.status,
        "egress_policy": project.egress_policy,
        "research_question": question if isinstance(question, str) else None,
        "temporal_scope": safe_temporal,
        "protocol_saved": bool(project.protocol_json),
        "snapshots": [
            {
                "id": snapshot.id,
                "is_frozen": bool(snapshot.is_frozen),
                "snapshot_hash": snapshot.snapshot_hash,
                "asset_count": len(snapshot.asset_ids_json or []),
            }
            for snapshot in snapshots
        ],
        "formula_specs": [
            {
                "id": formula.id,
                "name": formula.name,
                "version": formula.version,
                "status": formula.status,
                "operation": (formula.spec_json or {}).get("operation"),
                "evidence_card_count": len(formula.evidence_card_ids_json or []),
            }
            for formula in formulas
        ],
        "experiments": [
            {
                "id": experiment.id,
                "name": experiment.name,
                "runner_type": experiment.runner_type,
                "execution_mode": experiment.execution_mode,
                "status": experiment.status,
                "formula_spec_id": experiment.formula_spec_id,
                "data_snapshot_id": experiment.data_snapshot_id,
            }
            for experiment in experiments
        ],
        "bound_rag_sources": [
            {
                "knowledge_base_id": source.knowledge_base_id,
                "category": source.category,
            }
            for source in sources
        ],
    }


def tool_research_project_context(db: Session, project_id: int, owner_user_id: int) -> ToolResult:
    context = _research_project_context_payload(db, project_id, owner_user_id)
    return ToolResult(
        name="research_project_context",
        output=json.dumps(context, ensure_ascii=False, indent=2),
        strategy="research_project_context",
        metadata={"project_id": project_id, "private_raw_data_included": False},
        next_suggestion="根据项目状态决定先检索项目 RAG、补齐协议，还是让研究者在研究页明确创建/运行实验。",
    )


def tool_research_rag_search(
    db: Session,
    project_id: int,
    owner_user_id: int,
    query: str,
    category: str = "all",
    top_k: int = 6,
) -> ToolResult:
    from app.services.research_rag_service import ResearchRagService

    result = ResearchRagService().search(
        db,
        project_id,
        owner_user_id,
        query,
        category,
        top_k,
        "hybrid_rrf_no_rerank",
    )
    return ToolResult(
        name="research_rag_search",
        output=_format_citations_for_tool(result.citations, max_excerpt=700) if result.citations else result.notice,
        citations=result.citations,
        strategy=result.strategy,
        query=result.query,
        metadata={
            "project_id": project_id,
            "category": result.category,
            "searched_knowledge_base_ids": result.searched_knowledge_base_ids,
            "private_raw_data_included": False,
        },
        next_suggestion="引用仅代表项目成员显式绑定的文本 RAG 片段；阅读原文并人工核验后才能创建 EvidenceCard。",
    )


def tool_research_protocol_draft(
    db: Session,
    project_id: int,
    owner_user_id: int,
    research_question: str,
) -> ToolResult:
    from app.api.schemas import ResearchProtocolDraftRequest
    from app.services.research_service import ResearchService

    draft = ResearchService().draft_protocol(
        db,
        project_id,
        ResearchProtocolDraftRequest(research_question=research_question),
        owner_user_id,
    )
    # The draft service is intentionally non-persistent. Keep the output
    # bounded while preserving the complete structured plan for the caller.
    return ToolResult(
        name="research_protocol_draft",
        output=json.dumps(draft.protocol, ensure_ascii=False, indent=2)[:12000],
        metadata={"project_id": project_id, "persisted": False, "private_raw_data_included": False},
        next_suggestion="这是未保存草案；请研究者审阅、补全证据/数据/验证方案并在研究页主动保存。",
    )


def tool_research_protocol_readiness(db: Session, project_id: int, owner_user_id: int) -> ToolResult:
    from app.services.research_protocol_readiness_service import ResearchProtocolReadinessService

    result = ResearchProtocolReadinessService().check(db, project_id, owner_user_id)
    payload = {
        "project_id": result.project_id,
        "ready": result.ready,
        "protocol_hash": result.protocol_hash,
        "protocol_revision_id": result.protocol_revision_id,
        "missing": [item.model_dump(mode="json") for item in result.missing],
        "notice": result.notice,
    }
    return ToolResult(
        name="research_protocol_readiness",
        output=json.dumps(payload, ensure_ascii=False, indent=2),
        metadata={"project_id": project_id, "private_raw_data_included": False},
        next_suggestion="就绪检查只读且不替代正式实验创建时的具体数据/样本校验。",
    )


_PRIVATE_RESEARCH_KEYS = {
    "source_uri",
    "uri",
    "href",
    "path",
    "run_dir",
    "output_dir",
    "input_path",
    "local_path",
    "storage_uri",
    "asset_uri",
}


def _safe_research_value(value: object, key: str | None = None, *, depth: int = 0) -> object:
    """Return bounded research metadata without leaking local paths or private URIs."""
    normalized_key = key.lower() if key else ""
    if key and (
        normalized_key in _PRIVATE_RESEARCH_KEYS
        or any(token in normalized_key for token in ("_uri", "_path", "_href", "_directory", "_dir"))
    ):
        return "[redacted: private path/URI]"
    if normalized_key == "error_message" and isinstance(value, str):
        value = re.sub(r"(?:research://|[A-Za-z]:[\\/]|/)[^\s,;]+", "[redacted path]", value)
    if depth > 5:
        return "[truncated]"
    if isinstance(value, dict):
        return {
            str(item_key): _safe_research_value(item_value, str(item_key), depth=depth + 1)
            for item_key, item_value in list(value.items())[:80]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_research_value(item, depth=depth + 1) for item in list(value)[:80]]
    if isinstance(value, str):
        return value[:2000]
    return value


def tool_research_data_catalog(db: Session, project_id: int, owner_user_id: int) -> ToolResult:
    """Describe project data/formula/snapshot choices without returning source locations."""
    from app.db.models import FormulaSpec, ResearchDataAsset, ResearchDataSnapshot, ResearchExperiment
    from app.services.research_service import ResearchService

    ResearchService._get_owned_project(db, project_id, owner_user_id)
    assets = (
        db.query(ResearchDataAsset)
        .filter(ResearchDataAsset.project_id == project_id)
        .order_by(ResearchDataAsset.created_at.asc(), ResearchDataAsset.id.asc())
        .all()
    )
    snapshots = (
        db.query(ResearchDataSnapshot)
        .filter(ResearchDataSnapshot.project_id == project_id)
        .order_by(ResearchDataSnapshot.created_at.desc(), ResearchDataSnapshot.id.desc())
        .all()
    )
    formulas = (
        db.query(FormulaSpec)
        .filter(FormulaSpec.project_id == project_id)
        .order_by(FormulaSpec.name.asc(), FormulaSpec.version.desc(), FormulaSpec.id.desc())
        .all()
    )
    experiments = (
        db.query(ResearchExperiment)
        .filter(ResearchExperiment.project_id == project_id)
        .order_by(ResearchExperiment.created_at.desc(), ResearchExperiment.id.desc())
        .all()
    )
    payload = {
        "project_id": project_id,
        # Keep exact counts before the detailed arrays. Agent tool output is
        # intentionally bounded by AgentService; putting these facts first
        # prevents truncation from turning an audit into a guess.
        "counts": {
            "assets": len(assets),
            "snapshots": len(snapshots),
            "formula_specs": len(formulas),
            "experiments": len(experiments),
        },
        "assets": [
            {
                "id": asset.id,
                "name": asset.name,
                "asset_kind": asset.asset_kind,
                "source_type": asset.source_type,
                "sha256": asset.sha256,
                "metadata": _safe_research_value(asset.metadata_json or {}),
            }
            for asset in assets
        ],
        "snapshots": [
            {
                "id": snapshot.id,
                "name": snapshot.name,
                "description": snapshot.description,
                "asset_ids": snapshot.asset_ids_json,
                "snapshot_hash": snapshot.snapshot_hash,
                "is_frozen": bool(snapshot.is_frozen),
            }
            for snapshot in snapshots
        ],
        "formula_specs": [
            {
                "id": formula.id,
                "name": formula.name,
                "version": formula.version,
                "status": formula.status,
                "operation": (formula.spec_json or {}).get("operation"),
                "spec": _safe_research_value(formula.spec_json or {}),
                "evidence_card_ids": formula.evidence_card_ids_json or [],
            }
            for formula in formulas
        ],
        "experiments": [
            {
                "id": experiment.id,
                "name": experiment.name,
                "runner_type": experiment.runner_type,
                "execution_mode": experiment.execution_mode,
                "status": experiment.status,
                "formula_spec_id": experiment.formula_spec_id,
                "data_snapshot_id": experiment.data_snapshot_id,
            }
            for experiment in experiments
        ],
        "private_source_locations_included": False,
    }
    return ToolResult(
        name="research_data_catalog",
        output=json.dumps(payload, ensure_ascii=False, indent=2),
        metadata={"project_id": project_id, "private_raw_data_included": False},
        next_suggestion="选择一个已有公式规格和数据快照；若需要试跑，可在明确确认后创建 Python preview 实验。",
    )


def _safe_run_payload(run, experiment) -> dict[str, object]:
    outputs = []
    for output in run.outputs_json or []:
        if not isinstance(output, dict):
            continue
        # Preserve names, hashes and visualization metadata, but never expose storage locations.
        safe_output = {
            key: _safe_research_value(value, key)
            for key, value in output.items()
            if key.lower() not in {"uri", "href", "path", "run_dir", "output_dir", "source_uri"}
        }
        if output.get("file_name"):
            safe_output["file_name"] = Path(str(output["file_name"])).name
        outputs.append(safe_output)
    return {
        "run_id": run.id,
        "experiment_id": run.experiment_id,
        "experiment_name": experiment.name,
        "runner_type": run.runner_type,
        "execution_mode": experiment.execution_mode,
        "status": run.status,
        "manifest": _safe_research_value(run.manifest_json or {}),
        "outputs": outputs,
        "error_message": _safe_research_value(run.error_message[:2000], "error_message") if run.error_message else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


def tool_research_run_summary(
    db: Session,
    project_id: int,
    owner_user_id: int,
    experiment_id: int | None = None,
    run_id: int | None = None,
) -> ToolResult:
    """Read metrics, stages and image descriptors from completed or active runs."""
    from app.db.models import ResearchRun
    from app.services.research_run_service import ResearchRunService

    service = ResearchRunService()
    if run_id is not None:
        run = service._get_owned_run_any_experiment(db, project_id, run_id, owner_user_id)
        experiment = service._get_owned_experiment(db, project_id, run.experiment_id, owner_user_id)
        if experiment_id is not None and experiment.id != experiment_id:
            raise LookupError("run_id 不属于指定的 experiment_id。")
        runs = [run]
    elif experiment_id is not None:
        experiment = service._get_owned_experiment(db, project_id, experiment_id, owner_user_id)
        runs = (
            db.query(ResearchRun)
            .filter(ResearchRun.project_id == project_id, ResearchRun.experiment_id == experiment_id)
            .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
            .limit(10)
            .all()
        )
    else:
        # The Agent often knows the active project before it knows a particular
        # experiment/run ID. Keep this project-level status query bounded and
        # owner-scoped so it is useful for overview questions.
        service._get_owned_project(db, project_id, owner_user_id)
        runs = (
            db.query(ResearchRun)
            .filter(ResearchRun.project_id == project_id)
            .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
            .limit(12)
            .all()
        )
    run_summaries = []
    for item in runs:
        item_experiment = service._get_owned_experiment(db, project_id, item.experiment_id, owner_user_id)
        formula_spec = service._get_formula_spec(db, project_id, item_experiment.formula_spec_id)
        snapshot = service._get_snapshot(db, project_id, item_experiment.data_snapshot_id)
        snapshot_assets = service._get_snapshot_assets(db, project_id, snapshot)
        safe_item = _safe_run_payload(item, item_experiment)
        output_refs = []
        for output in safe_item.get("outputs", []):
            if not isinstance(output, dict):
                continue
            file_name = Path(str(output.get("file_name") or "")).name
            if not file_name or file_name == ".":
                continue
            suffix = Path(file_name).suffix.lower()
            output_refs.append({
                "file_name": file_name,
                "kind": str(output.get("kind") or "output"),
                "size": output.get("size", 0),
                "previewable": suffix in {".png", ".jpg", ".jpeg", ".webp"},
            })
        run_summary = {
            "run_id": item.id,
            "experiment_id": item.experiment_id,
            "experiment_name": item_experiment.name,
            "status": item.status,
            "execution_mode": item_experiment.execution_mode,
            "parameters": _safe_research_value(item_experiment.parameters_json or {}),
            "validation_plan": _safe_research_value(item_experiment.validation_plan_json or {}),
            "visualization_contract": _safe_research_value(item_experiment.visualization_contract_json or []),
            "formula": {
                "id": formula_spec.id,
                "name": formula_spec.name,
                "version": formula_spec.version,
                "status": formula_spec.status,
                "operation": _safe_research_value((formula_spec.spec_json or {}).get("operation")),
            },
            "data_snapshot": {
                "id": snapshot.id,
                "name": snapshot.name,
                "snapshot_hash": snapshot.snapshot_hash,
                "asset_count": len(snapshot.asset_ids_json or []),
            },
            "input_assets": [
                {
                    "id": asset.id,
                    "name": asset.name,
                    "asset_kind": asset.asset_kind,
                    "source_type": asset.source_type,
                }
                for asset in snapshot_assets
            ],
            "output_count": len(output_refs),
            "outputs": output_refs,
        }
        safe_manifest = safe_item.get("manifest")
        if isinstance(safe_manifest, dict):
            validation_metrics = safe_manifest.get("validation_metrics") or safe_manifest.get("metrics")
            if isinstance(validation_metrics, dict):
                run_summary["validation_metrics"] = _safe_research_value(validation_metrics)
        run_summaries.append(run_summary)

    payload = {
        "project_id": project_id,
        "runs": [_safe_run_payload(item, service._get_owned_experiment(db, project_id, item.experiment_id, owner_user_id)) for item in runs],
        "private_paths_included": False,
    }
    return ToolResult(
        name="research_run_summary",
        output=json.dumps(payload, ensure_ascii=False, indent=2),
        metadata={
            "project_id": project_id,
            "run_count": len(runs),
            "runs": run_summaries,
            "research_run": True,
            "private_raw_data_included": False,
        },
        next_suggestion="结合 validation_metrics 和 outputs 判断结果；不要把 preview 或代理标签当成正式科学结论。",
    )


def tool_research_verify_run(
    db: Session, project_id: int, owner_user_id: int, experiment_id: int, run_id: int
) -> ToolResult:
    from app.services.research_run_service import ResearchRunService

    result = ResearchRunService().verify_run_integrity(db, project_id, experiment_id, run_id, owner_user_id)
    payload = result.model_dump(mode="json")
    return ToolResult(
        name="research_verify_run",
        output=json.dumps(payload, ensure_ascii=False, indent=2),
        metadata={"project_id": project_id, "run_id": run_id, "private_raw_data_included": False},
        next_suggestion="只有 verified 的证据包才适合作为可交付研究证据；failed/not_available 需要人工处理。",
    )


def tool_research_compare_runs(
    db: Session, project_id: int, owner_user_id: int, experiment_id: int, run_id: int, reference_run_id: int
) -> ToolResult:
    from app.services.research_run_service import ResearchRunService

    result = ResearchRunService().compare_formal_runs(
        db, project_id, experiment_id, run_id, owner_user_id, reference_run_id=reference_run_id
    )
    payload = _safe_research_value(result.model_dump(mode="json"))
    return ToolResult(
        name="research_compare_runs",
        output=json.dumps(payload, ensure_ascii=False, indent=2),
        metadata={"project_id": project_id, "run_id": run_id, "reference_run_id": reference_run_id},
        next_suggestion="比较结果只说明两个 completed formal runs 是否可比；若是 preview，请回到参数/验证设计而非宣称优劣。",
    )


def tool_research_create_preview_experiment(
    db: Session,
    project_id: int,
    owner_user_id: int,
    name: str,
    formula_spec_id: int,
    data_snapshot_id: int,
    parameters: dict | None = None,
    validation_plan: dict | None = None,
    visualization_contract: list[str] | None = None,
    confirm: bool = False,
) -> ToolResult:
    """Create only a bounded Python preview experiment after explicit confirmation."""
    if not confirm:
        return ToolResult(name="research_create_preview_experiment", output="工具调用被拒绝：需要 confirm=true。")
    from app.api.schemas import ResearchExperimentCreate
    from app.services.research_service import ResearchService

    payload = ResearchExperimentCreate(
        name=name,
        formula_spec_id=formula_spec_id,
        data_snapshot_id=data_snapshot_id,
        runner_type="python",
        execution_mode="preview",
        parameters=parameters or {},
        validation_plan=validation_plan or {},
        visualization_contract=visualization_contract or [],
    )
    experiment = ResearchService().create_experiment(db, project_id, payload, owner_user_id)
    output = {
        "experiment_id": experiment.id,
        "project_id": experiment.project_id,
        "name": experiment.name,
        "formula_spec_id": experiment.formula_spec_id,
        "data_snapshot_id": experiment.data_snapshot_id,
        "runner_type": experiment.runner_type,
        "execution_mode": experiment.execution_mode,
        "status": experiment.status,
        "notice": "已创建 Python preview 计划；尚未排队或执行，也不构成正式科学结论。",
    }
    if not (validation_plan or {}).get("reference_asset_id") and not (validation_plan or {}).get("sample_validation"):
        output["validation_notice"] = (
            "当前 preview 未声明 reference_asset_id 或 sample_validation；执行后只能生成阶段图和运行清单，"
            "不会产生可核验的 validation_metrics。"
        )
    return ToolResult(
        name="research_create_preview_experiment",
        output=json.dumps(output, ensure_ascii=False, indent=2),
        metadata={
            "project_id": project_id,
            "experiment_id": experiment.id,
            "research_experiment": True,
            "status": experiment.status,
            "mutating": True,
        },
        next_suggestion=(
            "如果用户本轮已经明确同意执行 preview 并要求排队，可继续调用 "
            f"research_queue_preview(confirm=true, experiment_id={experiment.id})；"
            "否则先停在 planned 状态并请求确认。"
        ),
    )


def tool_research_literature_search(
    db: Session,
    project_id: int,
    owner_user_id: int,
    query: str,
    provider: str = "crossref",
    rows: int = 5,
) -> ToolResult:
    from app.services.research_literature_search import ResearchLiteratureSearchService

    result = ResearchLiteratureSearchService().search(
        db,
        project_id,
        owner_user_id,
        query,
        rows,
        provider,
    )
    candidates = [
        {
            "provider": candidate.provider,
            "external_id": candidate.external_id,
            "title": candidate.title,
            "authors": candidate.authors[:5],
            "published_year": candidate.published_year,
            "doi": candidate.doi,
            "source_url": candidate.source_url,
            "abstract": candidate.abstract[:1200] if candidate.abstract else None,
        }
        for candidate in result.candidates
    ]
    return ToolResult(
        name="research_literature_search",
        output=json.dumps({"query": result.query, "provider": result.provider, "candidates": candidates, "notice": result.notice}, ensure_ascii=False, indent=2),
        strategy=f"external:{result.provider}",
        query=result.query,
        metadata={
            "project_id": project_id,
            "provider": result.provider,
            "external_search": True,
            "raw_project_data_sent": False,
        },
        next_suggestion="候选文献仍需人工阅读、核对全文/许可和适用条件，不能自动成为已核验方法依据。",
    )


def tool_research_queue_preview(
    db: Session,
    project_id: int,
    experiment_id: int,
    owner_user_id: int,
    confirm: bool = False,
) -> ToolResult:
    """Queue an existing Python preview experiment after explicit consent.

    The Agent cannot create or mutate an experiment here.  The caller must
    have enabled the separate execution-consent flag, and this function still
    rechecks the project membership and preview/Python boundary before
    delegating to the normal queue service.
    """
    if not confirm:
        return ToolResult(name="research_queue_preview", output="工具调用被拒绝：需要 confirm=true。")
    from app.services.research_run_service import ResearchRunService

    run_service = ResearchRunService()
    experiment = run_service._get_owned_experiment(db, project_id, experiment_id, owner_user_id)
    if experiment.execution_mode != "preview":
        return ToolResult(
            name="research_queue_preview",
            output="工具调用被拒绝：Agent 只能排队 preview 实验，不能排队 formal 实验。",
        )
    if experiment.runner_type != "python":
        return ToolResult(
            name="research_queue_preview",
            output="工具调用被拒绝：Agent 只能排队 Python preview 实验，IDL 需要研究者显式操作。",
        )
    run = run_service.queue_run(db, project_id, experiment_id, owner_user_id)
    payload = {
        "run_id": run.id,
        "experiment_id": run.experiment_id,
        "project_id": run.project_id,
        "status": run.status,
        "execution_mode": "queue",
        "agent_action": "research_queue_preview",
        "notice": "已将已有 Python preview 实验加入队列；不会自动冻结公式或生成正式科学结论。",
    }
    return ToolResult(
        name="research_queue_preview",
        output=json.dumps(payload, ensure_ascii=False, indent=2),
        metadata={
            "project_id": project_id,
            "experiment_id": experiment_id,
            "run_id": run.id,
            "research_run": True,
            "runs": [{
                "run_id": run.id,
                "experiment_id": experiment_id,
                "experiment_name": experiment.name,
                "status": run.status,
                "execution_mode": experiment.execution_mode,
                "output_count": 0,
                "outputs": [],
            }],
            "status": run.status,
            "mutating": True,
        },
        next_suggestion=(
            "运行已进入 queued；研究页/worker 负责实际执行。调用 research_run_summary "
            "检查状态和阶段图件；queued/running/completed 均不得改写成 formal 科学结论。"
        ),
    )


def tool_research_fetch_gee_asset(
    db: Session,
    project_id: int,
    owner_user_id: int,
    dataset_id: str,
    bbox: list[float],
    bands: list[str] | None = None,
    scale: int = 30,
    crs: str = "EPSG:4326",
    composite: str = "median",
    start_date: str | None = None,
    end_date: str | None = None,
    label: str | None = None,
    confirm: bool = False,
) -> ToolResult:
    """Fetch a bounded GEE result into a private project DataAsset.

    This deliberately stops at DataAsset registration.  Snapshot freezing,
    experiment creation and execution remain explicit research-page actions.
    The response sent back to the Agent redacts the private storage URI.
    """
    if not confirm:
        return ToolResult(name="research_fetch_gee_asset", output="工具调用被拒绝：需要 confirm=true。")
    from app.api.schemas import ResearchGeeFetchRequest
    from app.services.gee_service import GeeService

    response = GeeService().fetch_research_asset(
        db,
        project_id,
        ResearchGeeFetchRequest(
            dataset_id=dataset_id,
            start_date=start_date,
            end_date=end_date,
            bbox=bbox,
            bands=bands or [],
            scale=scale,
            crs=crs,
            composite=composite,
            label=label,
        ),
        owner_user_id,
    )
    asset = response.asset
    payload = {
        "asset_id": asset.id,
        "name": asset.name,
        "asset_kind": asset.asset_kind,
        "source_type": asset.source_type,
        "sha256": asset.sha256,
        "metadata": asset.metadata,
        "notice": response.notice,
        "source_uri_exposed_to_agent": False,
    }
    return ToolResult(
        name="research_fetch_gee_asset",
        output=json.dumps(payload, ensure_ascii=False, indent=2),
        metadata={
            "project_id": project_id,
            "asset_id": asset.id,
            "mutating": True,
            "raw_pixels_exposed": False,
            "private_uri_exposed": False,
        },
        next_suggestion="GEE 资产已登记但尚未冻结快照；请在研究页核对元数据并显式创建 DataSnapshot。",
    )


# 工具注册表
AGENT_TOOLS = {
    "kb_search": {
        "description": "语义检索知识库，返回相关代码片段。用于查找函数用法、参数说明、示例代码。",
        "parameters": {
            "query": {"type": "string", "description": "搜索关键词"},
        },
        "function": tool_kb_search,
    },
    "grep_search": {
        "description": "在知识库 chunk 中执行精确文本或正则检索。用于查找具体 API、变量名、错误文本。",
        "parameters": {
            "query": {"type": "string", "description": "文本或正则表达式"},
            "mode": {"type": "string", "description": "auto、literal、regex 或 fts"},
        },
        "function": tool_grep_search,
    },
    "symbol_search": {
        "description": "按 IDL pro/function 符号名查找定义、摘要和函数体。",
        "parameters": {
            "symbol": {"type": "string", "description": "符号名"},
            "include_dependencies": {"type": "boolean", "description": "是否包含被调用符号"},
        },
        "function": tool_symbol_search,
    },
    "read_context": {
        "description": "按 chunk_id 或 symbol_name 读取命中代码周围上下文和依赖上下文。",
        "parameters": {
            "chunk_id": {"type": "integer", "description": "chunk ID，可选"},
            "symbol_name": {"type": "string", "description": "符号名，可选"},
        },
        "function": tool_read_context,
    },
    "find_callers": {
        "description": "查找调用某个 IDL 符号的上游符号。",
        "parameters": {
            "symbol": {"type": "string", "description": "符号名"},
        },
        "function": tool_find_callers,
    },
    "find_callees": {
        "description": "查找某个 IDL 符号调用的下游符号。",
        "parameters": {
            "symbol": {"type": "string", "description": "符号名"},
        },
        "function": tool_find_callees,
    },
    "analyze_code": {
        "description": "分析 IDL 代码结构，提取函数名、检查语法、统计行数。",
        "parameters": {
            "code": {"type": "string", "description": "要分析的 IDL 代码"},
        },
        "function": tool_analyze_code,
    },
    "read_artifact": {
        "description": "读取之前会话中生成的 .pro 文件内容。用于修复或改进之前生成的代码。",
        "parameters": {
            "session_id": {"type": "integer", "description": "会话 ID"},
            "artifact_id": {"type": "string", "description": "附件 ID"},
        },
        "function": tool_read_artifact,
    },
    "fix_code": {
        "description": "基于用户反馈和参考资料，准备修复代码的指令。",
        "parameters": {
            "original_code": {"type": "string", "description": "原始代码"},
            "feedback": {"type": "string", "description": "用户反馈的问题"},
            "context": {"type": "string", "description": "参考资料（可选）"},
        },
        "function": tool_fix_code,
    },
    "lint_code": {
        "description": "使用 IDL 编译器检查代码语法。生成代码后应调用此工具验证。",
        "parameters": {
            "code": {"type": "string", "description": "要检查的 IDL 代码"},
        },
        "function": tool_lint_code,
    },
    "research_project_context": {
        "description": "读取研究项目的安全摘要、快照/公式/实验状态和已绑定 RAG 类别；不返回原始影像路径、URI 或凭据。",
        "parameters": {
            "project_id": {"type": "integer", "description": "研究项目 ID"},
        },
        "function": tool_research_project_context,
    },
    "research_rag_search": {
        "description": "在研究项目已显式绑定的 Method、IDL Code 或 Python Code RAG 中检索并返回带引用片段。",
        "parameters": {
            "project_id": {"type": "integer", "description": "研究项目 ID"},
            "query": {"type": "string", "description": "研究方法或代码检索问题"},
            "category": {"type": "string", "description": "method、idl_code、python_code 或 all"},
            "top_k": {"type": "integer", "description": "最多返回片段数"},
        },
        "function": tool_research_rag_search,
    },
    "research_protocol_draft": {
        "description": "从研究问题生成未持久化的结构化协议草案；不会保存协议或修改项目。",
        "parameters": {
            "project_id": {"type": "integer", "description": "研究项目 ID"},
            "research_question": {"type": "string", "description": "至少 8 个字符的研究问题"},
        },
        "function": tool_research_protocol_draft,
    },
    "research_protocol_readiness": {
        "description": "读取研究协议就绪检查；这是只读诊断，不替代正式实验校验。",
        "parameters": {
            "project_id": {"type": "integer", "description": "研究项目 ID"},
        },
        "function": tool_research_protocol_readiness,
    },
    "research_data_catalog": {
        "description": "读取研究项目的数据资产、数据快照、公式规格和实验选择；不返回原始路径、私有 URI 或像元。",
        "parameters": {
            "project_id": {"type": "integer", "description": "研究项目 ID"},
        },
        "function": tool_research_data_catalog,
    },
    "research_run_summary": {
        "description": "读取研究运行的状态、验证指标、阶段信息和输出图件描述；只传 project_id 可查看当前项目最近运行，传 experiment_id 或 run_id 可聚焦单个实验/运行；不返回私有文件路径。",
        "parameters": {
            "project_id": {"type": "integer", "description": "研究项目 ID"},
            "experiment_id": {"type": "integer", "description": "实验 ID，与 run_id 二选一或同时提供"},
            "run_id": {"type": "integer", "description": "运行 ID，与 experiment_id 二选一或同时提供"},
        },
        "function": tool_research_run_summary,
    },
    "research_verify_run": {
        "description": "校验证据包 manifest、哈希和输出描述；这是只读检查。",
        "parameters": {
            "project_id": {"type": "integer", "description": "研究项目 ID"},
            "experiment_id": {"type": "integer", "description": "实验 ID"},
            "run_id": {"type": "integer", "description": "运行 ID"},
        },
        "function": tool_research_verify_run,
    },
    "research_compare_runs": {
        "description": "比较同一研究项目中两个 completed formal runs 的可比性和验证指标差异；不比较 preview 结论。",
        "parameters": {
            "project_id": {"type": "integer", "description": "研究项目 ID"},
            "experiment_id": {"type": "integer", "description": "候选实验 ID"},
            "run_id": {"type": "integer", "description": "候选运行 ID"},
            "reference_run_id": {"type": "integer", "description": "基线运行 ID"},
        },
        "function": tool_research_compare_runs,
    },
    "research_create_preview_experiment": {
        "description": "在用户显式授权且 confirm=true 时，使用当前项目已有公式规格和数据快照创建 Python preview 计划；不能创建 formal/IDL。",
        "parameters": {
            "project_id": {"type": "integer", "description": "研究项目 ID"},
            "name": {"type": "string", "description": "preview 实验名称"},
            "formula_spec_id": {"type": "integer", "description": "已有公式规格 ID"},
            "data_snapshot_id": {"type": "integer", "description": "已有数据快照 ID"},
            "parameters": {"type": "object", "description": "受控 PythonRunner 参数"},
            "validation_plan": {"type": "object", "description": "可选的开发集验证计划"},
            "visualization_contract": {"type": "array", "items": {"type": "string"}, "description": "期望输出的图件/表格类型"},
            "confirm": {"type": "boolean", "description": "用户已明确要求创建 preview 计划"},
        },
        "function": tool_research_create_preview_experiment,
    },
    "research_literature_search": {
        "description": "在用户显式允许外部研究搜索后，查询 Crossref/OpenAlex/Semantic Scholar 公开元数据；只发送查询词，不发送项目资产。",
        "parameters": {
            "project_id": {"type": "integer", "description": "研究项目 ID"},
            "query": {"type": "string", "description": "文献检索词"},
            "provider": {"type": "string", "description": "crossref、openalex 或 semantic_scholar"},
            "rows": {"type": "integer", "description": "最多返回候选数"},
        },
        "function": tool_research_literature_search,
    },
    "research_queue_preview": {
        "description": "在用户显式授权且 confirm=true 时，将当前项目已有的 Python preview 实验加入队列；不能创建/修改实验或运行 formal/IDL。",
        "parameters": {
            "project_id": {"type": "integer", "description": "研究项目 ID"},
            "experiment_id": {"type": "integer", "description": "已有 preview 实验 ID"},
            "confirm": {"type": "boolean", "description": "用户已明确要求排队该 preview 实验"},
        },
        "function": tool_research_queue_preview,
    },
    "research_fetch_gee_asset": {
        "description": "在用户显式授权且 confirm=true 时，按白名单规则获取受限 GEE 数据并登记为私有 DataAsset；不冻结快照、不创建实验。",
        "parameters": {
            "project_id": {"type": "integer", "description": "研究项目 ID"},
            "dataset_id": {"type": "string", "description": "已配置白名单的 GEE 数据集 ID"},
            "bbox": {"type": "array", "items": {"type": "number"}, "description": "[min_lon,min_lat,max_lon,max_lat]"},
            "bands": {"type": "array", "items": {"type": "string"}, "description": "波段名称"},
            "scale": {"type": "integer", "description": "目标尺度（米）"},
            "crs": {"type": "string", "description": "目标 CRS"},
            "composite": {"type": "string", "enum": ["median", "mean", "first"]},
            "start_date": {"type": "string", "description": "可选 YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "可选 YYYY-MM-DD"},
            "label": {"type": "string", "description": "可选资产名称"},
            "confirm": {"type": "boolean", "description": "用户已明确要求获取并登记该 GEE 数据"},
        },
        "function": tool_research_fetch_gee_asset,
    },
}


# OpenAI Function Calling 格式的工具定义
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "kb_search",
            "description": "检索知识库，返回相关代码片段。用于查找函数用法、参数说明、示例代码。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "在知识库 chunk 中执行精确文本或正则检索。用于查找具体 API、变量名、错误文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "文本或正则表达式"},
                    "mode": {"type": "string", "enum": ["auto", "literal", "regex", "fts"], "description": "匹配模式"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "symbol_search",
            "description": "按 IDL pro/function 符号名查找定义、摘要和函数体。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "符号名"},
                    "include_dependencies": {"type": "boolean", "description": "是否包含被调用符号"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_context",
            "description": "按 chunk_id 或 symbol_name 读取命中代码周围上下文和依赖上下文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "integer", "description": "chunk ID"},
                    "symbol_name": {"type": "string", "description": "符号名"},
                    "max_chunks": {"type": "integer", "description": "最多返回片段数"},
                    "include_dependencies": {"type": "boolean", "description": "是否包含依赖上下文"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_callers",
            "description": "查找调用某个 IDL 符号的上游符号。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "符号名"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_callees",
            "description": "查找某个 IDL 符号调用的下游符号。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "符号名"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_code",
            "description": "分析 IDL 代码结构，提取函数名、检查语法、统计行数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要分析的 IDL 代码"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_artifact",
            "description": "读取之前会话中生成的 .pro 文件内容。用于修复或改进之前生成的代码。",
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string", "description": "附件 ID"},
                },
                "required": ["artifact_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fix_code",
            "description": "基于用户反馈和参考资料，准备修复代码的指令。",
            "parameters": {
                "type": "object",
                "properties": {
                    "original_code": {"type": "string", "description": "原始代码"},
                    "feedback": {"type": "string", "description": "用户反馈的问题"},
                    "context": {"type": "string", "description": "参考资料（可选）"},
                },
                "required": ["original_code", "feedback"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lint_code",
            "description": "使用 IDL 编译器检查代码语法。生成代码后应调用此工具验证。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要检查的 IDL 代码"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_project_context",
            "description": "读取研究项目安全摘要和研究资产状态，不返回原始影像路径、URI 或凭据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "研究项目 ID"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_rag_search",
            "description": "在当前研究项目显式绑定的 Method/IDL/Python 文本 RAG 中检索带引用片段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "研究项目 ID"},
                    "query": {"type": "string", "description": "研究方法或代码问题"},
                    "category": {"type": "string", "enum": ["method", "idl_code", "python_code", "all"]},
                    "top_k": {"type": "integer", "description": "最多返回片段数"},
                },
                "required": ["project_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_protocol_draft",
            "description": "生成未持久化的研究协议草案，不保存或修改项目。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "研究项目 ID"},
                    "research_question": {"type": "string", "description": "至少 8 个字符的研究问题"},
                },
                "required": ["project_id", "research_question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_protocol_readiness",
            "description": "读取研究协议就绪检查的只读结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "研究项目 ID"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_data_catalog",
            "description": "读取研究项目的数据资产、快照、公式和实验选择，不返回原始路径、私有 URI 或像元。",
            "parameters": {
                "type": "object",
                "properties": {"project_id": {"type": "integer", "description": "研究项目 ID"}},
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_run_summary",
            "description": "读取实验/运行状态、验证指标、阶段信息和输出图件描述，不返回私有文件路径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "研究项目 ID"},
                    "experiment_id": {"type": "integer", "description": "实验 ID"},
                    "run_id": {"type": "integer", "description": "运行 ID"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_verify_run",
            "description": "校验证据包 manifest、哈希和输出描述；这是只读检查。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "experiment_id": {"type": "integer"},
                    "run_id": {"type": "integer"},
                },
                "required": ["project_id", "experiment_id", "run_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_compare_runs",
            "description": "比较两个 completed formal runs 的可比性和验证指标差异；不把 preview 当作正式结论。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "experiment_id": {"type": "integer"},
                    "run_id": {"type": "integer"},
                    "reference_run_id": {"type": "integer"},
                },
                "required": ["project_id", "experiment_id", "run_id", "reference_run_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_create_preview_experiment",
            "description": "在明确授权且 confirm=true 时，使用已有公式和数据快照创建 Python preview 计划；不能创建 formal/IDL。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "formula_spec_id": {"type": "integer"},
                    "data_snapshot_id": {"type": "integer"},
                    "parameters": {"type": "object"},
                    "validation_plan": {"type": "object"},
                    "visualization_contract": {"type": "array", "items": {"type": "string"}},
                    "confirm": {"type": "boolean"},
                },
                "required": ["project_id", "name", "formula_spec_id", "data_snapshot_id", "confirm"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_literature_search",
            "description": "只有在用户显式允许时，搜索公开文献元数据；只发送查询词，不发送项目数据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "研究项目 ID"},
                    "query": {"type": "string", "description": "文献查询词"},
                    "provider": {"type": "string", "enum": ["crossref", "openalex", "semantic_scholar"]},
                    "rows": {"type": "integer", "description": "最多返回候选数"},
                },
                "required": ["project_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_queue_preview",
            "description": "在用户显式授权且 confirm=true 时，把已有 Python preview 实验加入队列；不能执行 formal/IDL 或修改实验。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "研究项目 ID"},
                    "experiment_id": {"type": "integer", "description": "已有 preview 实验 ID"},
                    "confirm": {"type": "boolean", "description": "用户已明确要求排队该 preview 实验"},
                },
                "required": ["project_id", "experiment_id", "confirm"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_fetch_gee_asset",
            "description": "在用户显式授权且 confirm=true 时，按现有白名单/范围/大小规则获取 GEE 数据并登记为私有 DataAsset；不冻结快照或创建实验。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "研究项目 ID"},
                    "dataset_id": {"type": "string", "description": "GEE 数据集 ID"},
                    "bbox": {"type": "array", "items": {"type": "number"}, "description": "四元素经纬度 bbox"},
                    "bands": {"type": "array", "items": {"type": "string"}},
                    "scale": {"type": "integer"},
                    "crs": {"type": "string"},
                    "composite": {"type": "string", "enum": ["median", "mean", "first"]},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "label": {"type": "string"},
                    "confirm": {"type": "boolean", "description": "用户已明确要求获取数据"},
                },
                "required": ["project_id", "dataset_id", "bbox", "confirm"],
            },
        },
    },
]


def get_openai_tools() -> list[dict]:
    """返回 OpenAI function calling 格式的工具定义。"""
    return OPENAI_TOOLS


def get_tool_descriptions() -> str:
    """生成工具描述文本，用于 LLM system prompt。"""
    lines = []
    for name, tool in AGENT_TOOLS.items():
        params = ", ".join(
            f"{k}: {v['description']}" for k, v in tool["parameters"].items()
        )
        lines.append(f"- {name}({params})：{tool['description']}")
    return "\n".join(lines)
