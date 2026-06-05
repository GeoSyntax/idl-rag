"""Agent 工具系统 — 为 Agent Loop 提供可调用的工具函数。

设计决策：
- 每个工具是一个纯函数，接收 db + 参数，返回 ToolResult
- 工具不依赖 AgentService，避免循环依赖
- 工具结果是字符串，LLM 可以直接读取
"""

from __future__ import annotations

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
