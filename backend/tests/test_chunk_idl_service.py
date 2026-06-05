from app.services.chunk_idl_service import IdlChunker


def test_idl_chunker_splits_overview_and_symbols() -> None:
    text = """; File overview
; More context

pro load_scene, input
  print, input
end

function compute_value, a, b
  return, a + b
end
"""

    result = IdlChunker(max_chars=1000).chunk(text, "E:/samples/demo.pro")
    chunks = result.chunks

    assert len(chunks) == 5
    assert chunks[0]["section"] == "overview"
    assert chunks[0]["meta_json"]["chunk_kind"] == "overview"
    assert chunks[1]["symbol_name"] == "load_scene"
    assert chunks[1]["meta_json"]["chunk_kind"] == "symbol_summary"
    assert chunks[2]["symbol_name"] == "load_scene"
    assert chunks[2]["meta_json"]["chunk_kind"] == "symbol_body"
    assert chunks[3]["symbol_name"] == "compute_value"
    assert chunks[3]["meta_json"]["chunk_kind"] == "symbol_summary"
    assert chunks[4]["symbol_name"] == "compute_value"
    assert chunks[4]["meta_json"]["chunk_kind"] == "symbol_body"


def test_idl_chunker_handles_nested_begin_end() -> None:
    """状态机应正确处理嵌套的 begin...end，不在内层 end 处误判符号结束。"""
    text = """pro process_data, input
  if n_elements(input) gt 0 then begin
    for i = 0, n_elements(input) - 1 do begin
      print, input[i]
    endfor
  endif else begin
    print, 'empty'
  endelse
end
"""
    result = IdlChunker(max_chars=2000).chunk(text, "E:/samples/nested.pro")
    chunks = result.chunks
    # 应该有 2 个 chunk: summary + body
    assert len(chunks) == 2
    assert chunks[0]["meta_json"]["chunk_kind"] == "symbol_summary"
    assert chunks[1]["meta_json"]["chunk_kind"] == "symbol_body"
    assert "process_data" in chunks[1]["content"]
    assert "endelse" in chunks[1]["content"]


def test_idl_chunker_ignores_pro_keyword_in_string() -> None:
    """字符串中的 pro 关键字不应被识别为符号开始。"""
    text = """pro safe_print
  msg = 'this is not a pro definition'
  print, msg
end
"""
    result = IdlChunker(max_chars=2000).chunk(text, "E:/samples/string.pro")
    chunks = result.chunks
    # 只应识别到 1 个符号: safe_print（summary + body = 2 个 chunk）
    symbol_names = list({c["symbol_name"] for c in chunks if c["symbol_name"]})
    assert symbol_names == ["safe_print"]


def test_idl_chunker_ignores_pro_keyword_in_comment() -> None:
    """注释中的 pro 关键字不应被识别为符号开始。"""
    text = """; This file defines pro helper functions
pro main_task
  print, 'main'
end
"""
    result = IdlChunker(max_chars=2000).chunk(text, "E:/samples/comment.pro")
    chunks = result.chunks
    symbol_names = list({c["symbol_name"] for c in chunks if c["symbol_name"]})
    assert symbol_names == ["main_task"]


def test_idl_chunker_body_overlap() -> None:
    """大函数体切分时，相邻 chunk 应有 overlap。"""
    # 生成一个足够长的函数体
    body_lines = ["  ; line " + str(i) + " " + "x" * 80 for i in range(30)]
    text = "pro long_func\n" + "\n".join(body_lines) + "\nend\n"

    result = IdlChunker(max_chars=400, overlap_ratio=0.2).chunk(text, "E:/samples/long.pro")
    chunks = result.chunks
    body_chunks = [c for c in chunks if c["meta_json"]["chunk_kind"] == "symbol_body"]

    # 应该被切成多块
    assert len(body_chunks) >= 2
    # 验证 overlap: 第二块的开头内容应出现在第一块的末尾
    if len(body_chunks) >= 2:
        first_lines = body_chunks[0]["content"].splitlines()
        second_lines = body_chunks[1]["content"].splitlines()
        # 至少有 1 行重叠
        overlap_found = any(line in first_lines for line in second_lines[:3])
        assert overlap_found, "相邻 chunk 之间应有 overlap"


def test_idl_chunker_plain_text_fallback() -> None:
    """非 IDL 代码应降级为纯文本分块。"""
    text = """# Title One
This is a paragraph under title one.

# Title Two
This is a paragraph under title two.
"""
    result = IdlChunker(max_chars=1000).chunk(text, "E:/samples/readme.md")
    chunks = result.chunks
    assert len(chunks) == 2
    assert chunks[0]["meta_json"]["chunk_kind"] == "paragraph"
    assert chunks[1]["meta_json"]["chunk_kind"] == "paragraph"


def test_chunk_result_contains_symbols_and_relationships() -> None:
    """ChunkResult 应包含符号元数据和调用关系。"""
    text = """pro helper, x
  print, x
end

pro main
  helper, 42
end
"""
    result = IdlChunker(max_chars=2000).chunk(text, "E:/samples/deps.pro")
    assert len(result.symbols) == 2
    assert result.symbols[0]["name"] == "helper"
    assert result.symbols[1]["name"] == "main"
    assert ("main", "helper") in result.relationships


def test_extract_call_relationships_ignores_builtins() -> None:
    """内置函数不应出现在调用关系中。"""
    text = """pro demo
  n = n_elements(arr)
  print, n
end
"""
    result = IdlChunker(max_chars=2000).chunk(text, "E:/samples/builtins.pro")
    # 只有 demo 一个符号，不应该有调用关系
    assert len(result.relationships) == 0


def test_idl_chunker_preserves_comma_style_parameters() -> None:
    text = """pro classify_scene, raster, classes, output_file, threshold=threshold
  compile_opt idl2
  print, raster, classes, output_file, threshold
end

function compute_index, nir, red, mask=mask
  compile_opt idl2
  return, (nir - red) / (nir + red)
end
"""
    result = IdlChunker(max_chars=2000).chunk(text, "E:/samples/params.pro")

    assert result.symbols[0]["params"] == "raster, classes, output_file, threshold=threshold"
    assert result.symbols[1]["params"] == "nir, red, mask=mask"
    summaries = [chunk for chunk in result.chunks if chunk["meta_json"]["chunk_kind"] == "symbol_summary"]
    assert "pro classify_scene(raster, classes, output_file, threshold=threshold)" in summaries[0]["content"]
    assert "function compute_index(nir, red, mask=mask)" in summaries[1]["content"]


def test_idl_chunker_handles_continuation_case_switch_and_object_calls() -> None:
    text = """function build_mask, raster, $
  threshold=threshold, $
  invert=invert
  compile_opt idl2
  data = raster.getData()
  case 1 of
    keyword_set(invert): begin
      mask = data le threshold
    end
    else: begin
      mask = data gt threshold
    end
  endcase
  return, mask
end

pro run_mask
  compile_opt idl2
  raster = obj_new('ENVIRaster')
  mask = build_mask(raster, threshold=0.3)
end
"""
    result = IdlChunker(max_chars=3000).chunk(text, "E:/samples/continuation.pro")

    assert [symbol["name"] for symbol in result.symbols] == ["build_mask", "run_mask"]
    assert "threshold=threshold" in result.symbols[0]["params"]
    assert ("run_mask", "build_mask") in result.relationships
    build_body = next(
        chunk for chunk in result.chunks
        if chunk["symbol_name"] == "build_mask" and chunk["meta_json"]["chunk_kind"] == "symbol_body"
    )
    assert "endcase" in build_body["content"]
    assert "raster.getData()" in build_body["content"]


def test_idl_chunk_metadata_contains_line_ranges_and_symbol_kind() -> None:
    text = """; header
pro line_demo, input
  compile_opt idl2
  print, input
end
"""
    result = IdlChunker(max_chars=2000).chunk(text, "E:/samples/lines.pro")
    summary = next(chunk for chunk in result.chunks if chunk["meta_json"]["chunk_kind"] == "symbol_summary")
    body = next(chunk for chunk in result.chunks if chunk["meta_json"]["chunk_kind"] == "symbol_body")

    assert summary["meta_json"]["symbol_kind"] == "pro"
    assert summary["meta_json"]["start_line"] == 2
    assert summary["meta_json"]["end_line"] == 2
    assert body["meta_json"]["symbol_kind"] == "pro"
    assert body["meta_json"]["start_line"] == 2
    assert body["meta_json"]["end_line"] == 5
    assert result.symbols[0]["start_line"] == 2
    assert result.symbols[0]["end_line"] == 5
