"""ENVI/IDL 代码分块器 — 基于状态机解析，替代正则表达式。

设计决策：
- 为什么不用正则？
  正则是无状态的，无法处理嵌套 begin/end、多行注释中的 pro/function 关键字、
  字符串常量中包含关键字等情况。状态机可以逐字符跟踪上下文，精准识别符号边界。

- 为什么不用 tree-sitter？
  tree-sitter-idl 对 ENVI/IDL 的方言支持不完整（ENVI 扩展了标准 IDL），
  引入外部 C 依赖增加部署复杂度。状态机方案零依赖、可维护、足够覆盖 ENVI/IDL 子集。

- 分块策略：
  每个 pro/function 生成两种 chunk：
  1. symbol_summary: 函数签名 + 头部注释（用于精确匹配函数名）
  2. symbol_body: 函数体（超长时按 max_chars 切分，相邻块有 overlap）

- 大块切分使用 overlap 避免上下文断裂
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import TypedDict

PARSER_VERSION = "idl-parser-v3"
CHUNKER_VERSION = "idl-chunker-v4"

# 旧版本号，用于兼容检测
_PREVIOUS_CHUNKER_VERSIONS = {"idl-chunker-v2", "idl-chunker-v3"}

# IDL 内置关键字/函数（排除后不应作为用户自定义符号的调用关系）
_IDL_BUILTINS: set[str] = {
    "print", "size", "where", "n_elements", "n_params", "n_tags",
    "total", "mean", "min", "max", "stddev", "median", "sqrt", "abs",
    "alog", "alog10", "sin", "cos", "tan", "asin", "acos", "atan",
    "exp", "fix", "long", "float", "double", "string", "byte", "uint",
    "ulong", "long64", "ulong64", "complex", "dcomplex",
    "indgen", "findgen", "bindgen", "lindgen", "uindgen",
    "replicate", "reform", "transpose", "invert", "determ",
    "execute", "call_function", "call_procedure", "obj_new", "obj_destroy",
    "obj_valid", "obj_isa", "obj_class", "ptr_new", "ptr_free", "ptr_valid",
    "widget_base", "widget_button", "widget_control", "widget_event", "widget_info",
    "read_image", "write_image", "read_tiff", "write_tiff", "read_ascii", "read_binary",
    "write_ascii", "hdf_sd_start", "hdf_sd_select", "hdf_sd_getdata", "hdf_sd_end",
    "ncdf_open", "ncdf_create", "ncdf_varget", "ncdf_attcopy", "ncdf_close",
    "ncdf_diminq", "ncdf_varget1", "ncdf_varput", "ncdf_vardef", "ncdf_control",
    "compile_opt", "forward_function", "forward_procedure", "resolve_routine",
    "file_search", "file_basename", "file_dirname", "file_test", "file_mkdir",
    "file_copy", "file_delete", "file_lines", "strsplit", "strtrim", "strmid",
    "strpos", "strlowcase", "strupcase", "strlen", "strjoin", "string",
    "keyword_set", "arg_present", "n_tags", "tag_names", "create_struct",
    "idl_validname", "message", "on_error", "catch", "return", "begin", "end",
    "if", "then", "else", "for", "do", "while", "repeat", "until", "case",
    "switch", "of", "goto", "pro", "function", "common",
    "envi_doit", "envi_open_file", "envi_select", "envi_get_data",
    "envi_file_query", "envi_map_info", "envi_proj_create",
    "enviraster", "enviurlraster", "envidimension",
    "envistart", "envistop",
    "plot", "oplot", "contour", "surface", "shade_surf", "tv", "tvscl",
    "device", "window", "wset", "wdelete", "polyfill", "plots", "xyouts",
    "loadct", "tvlct", "colorbar",
}

# 汇总内置名称的小写集合，用于 O(1) 查找
_IDL_BUILTINS_LOWER: frozenset[str] = frozenset(b.lower() for b in _IDL_BUILTINS)


class ChunkDraft(TypedDict):
    title: str | None
    section: str | None
    symbol_name: str | None
    content: str
    meta_json: dict


@dataclass
class ChunkResult:
    """分块器完整输出，包含 chunks、符号元数据和调用关系。"""
    chunks: list[ChunkDraft]
    symbols: list[dict]                   # [{name, kind, params, start_line}]
    relationships: list[tuple[str, str]]  # [(caller, callee)]


class _State(Enum):
    IDLE = auto()
    IN_LINE_COMMENT = auto()    # 行注释 ; 到行尾
    IN_STRING_SINGLE = auto()   # 单引号字符串
    IN_STRING_DOUBLE = auto()   # 双引号字符串
    IN_SYMBOL = auto()          # 在 pro/function 块内部


class _SymbolInfo:
    __slots__ = ("name", "kind", "params", "start_line", "end_line", "comment_lines", "body_lines")

    def __init__(self, name: str, kind: str, params: str, start_line: int) -> None:
        self.name = name
        self.kind = kind  # "pro" or "function"
        self.params = params
        self.start_line = start_line
        self.end_line = start_line
        self.comment_lines: list[str] = []
        self.body_lines: list[str] = []


def _find_keyword(text: str, pos: int, keyword: str) -> int:
    """在 text[pos:] 中查找独立的 keyword（前后非字母数字下划线），返回位置或 -1。"""
    lower = text.lower()
    kw_lower = keyword.lower()
    while True:
        idx = lower.find(kw_lower, pos)
        if idx == -1:
            return -1
        # 前一个字符不能是字母/数字/下划线
        if idx > 0 and (lower[idx - 1].isalnum() or lower[idx - 1] == "_"):
            pos = idx + 1
            continue
        # 后一个字符不能是字母/数字/下划线
        end = idx + len(kw_lower)
        if end < len(lower) and (lower[end].isalnum() or lower[end] == "_"):
            pos = idx + 1
            continue
        return idx


def _extract_symbols(text: str) -> list[_SymbolInfo]:
    """用状态机从 IDL 源码中提取所有 pro/function 符号。"""
    lines = text.splitlines()
    symbols: list[_SymbolInfo] = []
    pending_comments: list[str] = []
    overview_lines: list[str] = []

    state = _State.IDLE
    current_symbol: _SymbolInfo | None = None
    bracket_depth = 0  # ( ) [ ] 嵌套计数
    begin_depth = 0    # begin...end 嵌套计数

    # 行级符号开始的匹配
    symbol_re = re.compile(
        r"^\s*(pro|function)\s+([A-Za-z_][\w$]*)\s*(?:(?:\(([^)]*)\))|(?:,\s*(.*)))?",
        re.IGNORECASE,
    )
    end_re = re.compile(r"^\s*end\s*(?:\b.*)?$", re.IGNORECASE)
    begin_re = re.compile(r"\bbegin\b", re.IGNORECASE)

    for line_idx, line in enumerate(lines):
        stripped = line.strip()

        # === 状态: IDLE ===
        if state == _State.IDLE:
            # 检查行首是否有 symbol 定义（在处理注释之前）
            match = symbol_re.match(stripped)
            if match:
                # 先把 overview 输出
                if not symbols and (pending_comments or overview_lines):
                    pass  # overview 会在最后处理

                kind = match.group(1).lower()
                name = match.group(2)
                params = _normalize_signature_params(match.group(3) or match.group(4) or "")
                signature_line_idx = line_idx
                while lines[signature_line_idx].rstrip().endswith("$") and signature_line_idx + 1 < len(lines):
                    signature_line_idx += 1
                    params = _normalize_signature_params(f"{params}, {lines[signature_line_idx].strip()}")
                sym = _SymbolInfo(name, kind, params, line_idx + 1)
                sym.comment_lines = list(pending_comments)
                pending_comments = []
                current_symbol = sym
                symbols.append(sym)
                state = _State.IN_SYMBOL
                bracket_depth = 0
                begin_depth = 0
                # 计算这一行的括号和 begin/end
                _count_brackets(stripped, None, bracket_depth, begin_depth)
                # 重新精确计算
                bracket_depth = stripped.count("(") - stripped.count(")")
                bracket_depth += stripped.count("[") - stripped.count("]")
                if begin_re.search(stripped):
                    begin_depth += 1
                current_symbol.body_lines.append(line)
                continue

            # 注释行（仅在 IDLE 状态收集）
            if stripped.startswith(";"):
                pending_comments.append(line)
                continue

            # 非注释非符号的行 → overview
            if stripped:
                pending_comments.clear()  # 注释如果不紧跟符号就丢弃
                overview_lines.append(line)
            continue

        # === 状态: IN_SYMBOL ===
        if state == _State.IN_SYMBOL:
            assert current_symbol is not None
            current_symbol.body_lines.append(line)

            # 跳过行内的字符串和注释来计数括号
            bracket_depth_delta, begin_delta, end_delta = _count_brackets(
                stripped, current_symbol.kind, 0, 0
            )
            bracket_depth += bracket_depth_delta
            begin_depth += begin_delta
            previous_begin_depth = begin_depth

            # 检查是否有独立的 end 关键字（不在字符串/注释中）
            if end_re.match(stripped) and bracket_depth <= 0 and begin_depth <= 0:
                current_symbol.end_line = line_idx + 1
                state = _State.IDLE
                current_symbol = None
                bracket_depth = 0
                begin_depth = 0
                pending_comments = []
            elif end_delta > 0:
                begin_depth -= end_delta
                if previous_begin_depth <= 0 and begin_depth <= 0 and bracket_depth <= 0:
                    clean = _strip_comments_and_strings(stripped)
                    if re.search(r"\bend\b", clean, re.IGNORECASE):
                        current_symbol.end_line = line_idx + 1
                        state = _State.IDLE
                        current_symbol = None
                        bracket_depth = 0
                        begin_depth = 0
                        pending_comments = []

            # 清除挂起的注释（如果符号已经开始了）
            if stripped.startswith(";"):
                continue  # 行内注释已经在 body_lines 中了
            continue

    # 处理文件末尾还在符号中的情况
    # （已在循环中处理）

    return symbols


def _normalize_signature_params(params: str) -> str:
    cleaned = params.replace("$", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    return cleaned


def _strip_comments_and_strings(line: str) -> str:
    """移除行内的注释和字符串，返回纯代码部分。"""
    result: list[str] = []
    i = 0
    in_single = False
    in_double = False
    while i < len(line):
        ch = line[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if ch == ";" and not in_single and not in_double:
            break  # 行注释开始，后面全部丢弃
        if not in_single and not in_double:
            result.append(ch)
        i += 1
    return "".join(result)


def _count_brackets(line: str, kind: str | None, init_depth: int, init_begin: int) -> tuple[int, int, int]:
    """计算一行中的括号和 begin/end 深度变化。

    返回 (bracket_delta, begin_delta, end_delta)。
    跳过字符串和注释中的内容。
    """
    clean = _strip_comments_and_strings(line)
    bracket_delta = clean.count("(") - clean.count(")")
    bracket_delta += clean.count("[") - clean.count("]")

    begin_delta = len(re.findall(r"\bbegin\b", clean, re.IGNORECASE))
    end_delta = len(re.findall(r"\bend\b", clean, re.IGNORECASE))

    return bracket_delta, begin_delta, end_delta


def _extract_call_relationships(
    symbols: list[_SymbolInfo], all_names: set[str]
) -> list[tuple[str, str]]:
    """提取符号间的调用关系。

    扫描每个符号体内的函数调用，与同文件中定义的符号名取交集，
    排除自身和 IDL 内置关键字。
    返回 [(caller_name, callee_name), ...]。
    """
    relationships: list[tuple[str, str]] = []
    ident_re = re.compile(r"\b([A-Za-z_]\w*)\b")

    for sym in symbols:
        body = "\n".join(sym.body_lines)
        clean = _strip_comments_and_strings(body)
        found_names = {m.group(1).lower() for m in ident_re.finditer(clean)}
        for callee_lower in found_names:
            if callee_lower == sym.name.lower():
                continue
            if callee_lower in _IDL_BUILTINS_LOWER:
                continue
            # 检查是否是同文件中定义的符号
            for callee_sym in symbols:
                if callee_sym.name.lower() == callee_lower and callee_sym.name != sym.name:
                    relationships.append((sym.name, callee_sym.name))
                    break
    # 去重
    return list(dict.fromkeys(relationships))


class IdlChunker:
    def __init__(self, max_chars: int = 1200, overlap_ratio: float = 0.15) -> None:
        self.max_chars = max_chars
        self.overlap_ratio = overlap_ratio

    def chunk(self, text: str, source_path: str) -> ChunkResult:
        symbols = _extract_symbols(text)
        chunks: list[ChunkDraft] = []

        def build_meta(chunk_kind: str, **extra: object) -> dict:
            return {
                "source_path": source_path,
                "language": "envi-idl",
                "parser_version": PARSER_VERSION,
                "chunker_version": CHUNKER_VERSION,
                "chunk_kind": chunk_kind,
                **extra,
            }

        def append_chunk(
            *,
            title: str | None,
            section: str | None,
            symbol_name: str | None,
            content: str,
            chunk_kind: str,
            part: int | None = None,
            symbol_kind: str | None = None,
            start_line: int | None = None,
            end_line: int | None = None,
        ) -> None:
            # Strip trailing whitespace per line, preserving leading indentation
            cleaned = "\n".join(line.rstrip() for line in content.splitlines())
            if not cleaned.strip():
                return
            meta_json = build_meta(chunk_kind)
            if section:
                meta_json["block_type"] = section
            if part is not None:
                meta_json["part"] = part
            if symbol_kind:
                meta_json["symbol_kind"] = symbol_kind
            if start_line is not None:
                meta_json["start_line"] = start_line
            if end_line is not None:
                meta_json["end_line"] = end_line
            chunks.append({
                "title": title,
                "section": section,
                "symbol_name": symbol_name,
                "content": cleaned,
                "meta_json": meta_json,
            })

        # 处理 overview（第一个符号之前的所有内容）
        if symbols:
            first_sym = symbols[0]
            overview_content = "\n".join(
                line for line in text.splitlines()[:first_sym.start_line - 1]
            ).strip()
        else:
            overview_content = ""

        if overview_content:
            append_chunk(
                title="Overview",
                section="overview",
                symbol_name=None,
                content=overview_content,
                chunk_kind="overview",
            )

        # 处理每个符号
        for sym in symbols:
            # symbol_summary: 头部注释 + 函数签名
            summary_parts = list(sym.comment_lines)
            if sym.params:
                summary_parts.append(f"{sym.kind} {sym.name}({sym.params})")
            else:
                summary_parts.append(f"{sym.kind} {sym.name}")
            summary_content = "\n".join(summary_parts)
            append_chunk(
                title=sym.name,
                section=sym.kind,
                symbol_name=sym.name,
                content=summary_content,
                chunk_kind="symbol_summary",
                symbol_kind=sym.kind,
                start_line=sym.start_line,
                end_line=sym.start_line,
            )

            # symbol_body: 函数体
            body_text = "\n".join(sym.body_lines).strip()
            if body_text:
                pieces = _split_with_overlap(body_text, self.max_chars, self.overlap_ratio)
                for idx, piece in enumerate(pieces):
                    append_chunk(
                        title=sym.name,
                        section=sym.kind,
                        symbol_name=sym.name,
                        content=piece,
                        chunk_kind="symbol_body",
                        part=idx,
                        symbol_kind=sym.kind,
                        start_line=sym.start_line + _leading_overlap_offset(body_text, piece),
                        end_line=sym.start_line + _leading_overlap_offset(body_text, piece) + len(piece.splitlines()) - 1,
                    )

        # 提取结构体定义（全局作用域或函数内的 = {...} 赋值）
        struct_defs = _extract_struct_definitions(text)
        for struct_name, struct_body in struct_defs:
            append_chunk(
                title=struct_name,
                section="struct",
                symbol_name=struct_name,
                content=struct_body,
                chunk_kind="struct_def",
            )

        # 提取 COMMON 块声明
        common_defs = _extract_common_blocks(text)
        for common_name, common_body in common_defs:
            append_chunk(
                title=common_name,
                section="common",
                symbol_name=common_name,
                content=common_body,
                chunk_kind="common_block",
            )

        # 如果没有识别到任何符号，按纯文本处理
        if not symbols:
            plain_chunks = _chunk_plain_text(text, self.max_chars, source_path)
            chunks.extend(plain_chunks)

        # 提取符号间调用关系
        all_names = {sym.name for sym in symbols}
        relationships = _extract_call_relationships(symbols, all_names) if symbols else []

        # 构建符号元数据
        symbol_metas = [
            {
                "name": sym.name,
                "kind": sym.kind,
                "params": sym.params,
                "start_line": sym.start_line,
                "end_line": sym.end_line,
            }
            for sym in symbols
        ]

        return ChunkResult(
            chunks=chunks,
            symbols=symbol_metas,
            relationships=relationships,
        )


def _extract_struct_definitions(text: str) -> list[tuple[str, str]]:
    """提取 IDL 结构体定义，返回 [(名称, 定义文本), ...]。"""
    results: list[tuple[str, str]] = []
    # 匹配 name = { ... } 或 name = {field: value, ...} 模式
    # 支持多行结构体定义
    lines = text.splitlines()
    i = 0
    struct_re = re.compile(
        r"^\s*([A-Za-z_]\w*)\s*=\s*\{",
        re.IGNORECASE,
    )
    while i < len(lines):
        line = lines[i]
        match = struct_re.match(line)
        if match:
            struct_name = match.group(1)
            # 排除常见非结构体赋值（如数组字面量赋值给临时变量）
            # 只捕获看起来像结构体定义的模式（包含 : 的 key-value 对）
            brace_depth = 0
            collected: list[str] = []
            j = i
            while j < len(lines):
                current = lines[j]
                collected.append(current)
                # 计算花括号深度（跳过字符串）
                clean = _strip_comments_and_strings(current)
                brace_depth += clean.count("{") - clean.count("}")
                if brace_depth <= 0:
                    break
                j += 1

            body = "\n".join(collected)
            # 验证是否包含冒号（结构体字段标记）或 Tag 标记
            if ":" in _strip_comments_and_strings(body):
                results.append((struct_name, body))
            i = j + 1
        else:
            i += 1
    return results


def _extract_common_blocks(text: str) -> list[tuple[str, str]]:
    """提取 IDL COMMON 块声明，返回 [(名称, 声明文本), ...]。"""
    results: list[tuple[str, str]] = []
    lines = text.splitlines()
    common_re = re.compile(
        r"^\s*common\s+([A-Za-z_]\w*)",
        re.IGNORECASE,
    )
    for i, line in enumerate(lines):
        match = common_re.match(line)
        if match:
            common_name = match.group(1)
            # COMMON 可能跨行（用 $ 续行），收集完整声明
            collected: list[str] = [line]
            j = i
            while j < len(lines) and lines[j].rstrip().endswith("$"):
                j += 1
                if j < len(lines):
                    collected.append(lines[j])
            results.append((common_name, "\n".join(collected)))
    return results


def _leading_overlap_offset(full_text: str, piece: str) -> int:
    piece_lines = piece.splitlines()
    if not piece_lines:
        return 0
    first_line = piece_lines[0]
    for index, line in enumerate(full_text.splitlines()):
        if line == first_line:
            return index
    return 0


def _split_with_overlap(text: str, max_chars: int, overlap_ratio: float) -> list[str]:
    """按行切分大块文本，相邻块之间有 overlap 避免上下文断裂。"""
    if len(text) <= max_chars:
        return [text]

    lines = text.splitlines()
    overlap_lines = max(1, int(len(lines) * overlap_ratio / max(len(text) // max_chars, 1)))

    pieces: list[str] = []
    current: list[str] = []
    length = 0

    for line in lines:
        if current and length + len(line) + 1 > max_chars:
            pieces.append("\n".join(current))
            # 保留最后 overlap_lines 行作为下一个 chunk 的开头
            current = current[-overlap_lines:] if overlap_lines > 0 else []
            length = sum(len(current_line) for current_line in current) + len(current)
        current.append(line)
        length += len(line) + 1

    if current:
        piece = "\n".join(current)
        if piece.strip():
            pieces.append(piece)

    return [p for p in pieces if p.strip()]


def _chunk_plain_text(text: str, max_chars: int, source_path: str) -> list[ChunkDraft]:
    """纯文本分块（非 IDL 代码时的降级方案）。"""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[ChunkDraft] = []
    current_title: str | None = None
    current_lines: list[str] = []
    current_length = 0

    def flush() -> None:
        nonlocal current_lines, current_length
        if not current_lines:
            return
        content = "\n\n".join(current_lines).strip()
        if content:
            chunks.append({
                "title": current_title or "untitled",
                "section": current_title,
                "symbol_name": None,
                "content": content,
                "meta_json": {
                    "source_path": source_path,
                    "language": "text",
                    "parser_version": "text-reader-v1",
                    "chunker_version": "plain-text-v2",
                    "chunk_kind": "paragraph",
                },
            })
        current_lines = []
        current_length = 0

    for paragraph in paragraphs:
        lines = paragraph.splitlines()
        normalized = lines[0].strip()
        if normalized.startswith("#"):
            flush()
            current_title = normalized.lstrip("# ").strip() or current_title
            # The remaining lines after the heading belong to this section
            remaining = "\n".join(lines[1:]).strip()
            if remaining:
                current_lines.append(remaining)
                current_length += len(remaining)
            continue
        if current_lines and current_length + len(paragraph) > max_chars:
            flush()
        current_lines.append(paragraph)
        current_length += len(paragraph)
    flush()
    return chunks
