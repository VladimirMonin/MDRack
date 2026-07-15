"""Contracts for structural retrieval chunking."""

from __future__ import annotations

from pathlib import Path

from mdrack.adapters.markdown_it import MarkdownItParser
from mdrack.application.chunking import StructuralChunker, StructuralChunkingConfig
from mdrack.domain.blocks import BlockType
from mdrack.domain.chunks import RetrievalContentType


def _document(content: str):
    return MarkdownItParser().parse(
        Path("/tmp/example.md"),
        content=content,
        document_id="doc_test",
        relative_path="example.md",
    )


def _chunk(
    content: str,
    *,
    target_chars: int | None = None,
    hard_chars: int = 180,
    max_tokens: int = 80,
):
    config = StructuralChunkingConfig(
        target_chars=target_chars or max(40, hard_chars - 40),
        hard_limit_chars=hard_chars,
        max_tokens=max_tokens,
        overlap_chars=20,
        code_window_lines=4,
        table_rows_per_chunk=2,
        mermaid_window_lines=3,
    )
    document = _document(content)
    return document, StructuralChunker(config).build(document)


def test_long_prose_splits_only_inside_prose_and_keeps_heading_context() -> None:
    text = " ".join(f"Sentence {index} has useful words." for index in range(40))
    document, chunks = _chunk(f"# Guide\n\n{text}\n", hard_chars=160, max_tokens=60)

    paragraph = next(block for block in document.blocks if block.block_type == BlockType.PARAGRAPH)
    assert len(chunks) > 2
    assert all(chunk.parent_block_ids == (paragraph.block_id,) for chunk in chunks)
    assert all(chunk.heading_path == ("Guide",) for chunk in chunks)
    assert all(len(chunk.display_content) <= 160 for chunk in chunks)
    assert all(chunk.estimated_tokens <= 60 for chunk in chunks)
    assert all(chunk.embedding_text.startswith("Guide\n\n") for chunk in chunks)
    assert all(chunk.display_content != chunk.embedding_text for chunk in chunks)


def test_large_code_preserves_source_block_and_uses_line_windows() -> None:
    source = "\n".join(f"value_{index} = {index}" for index in range(11))
    document, chunks = _chunk(f"## Code\n\n```python\n{source}\n```\n")

    block = next(block for block in document.blocks if block.block_type == BlockType.CODE)
    code_chunks = [chunk for chunk in chunks if chunk.content_type == RetrievalContentType.CODE]
    assert block.raw_markdown.startswith("```python") and block.raw_markdown.endswith("```")
    assert len(code_chunks) == 3
    assert all(chunk.parent_block_ids == (block.block_id,) for chunk in code_chunks)
    assert all(chunk.source_span.start_line >= block.source_span.start_line for chunk in code_chunks)
    assert all(chunk.display_content not in {"```", "```python"} for chunk in code_chunks)
    assert "\n".join(chunk.display_content for chunk in code_chunks) == source


def test_large_table_repeats_header_and_keeps_parent_provenance() -> None:
    rows = "\n".join(f"| {index} | value {index} |" for index in range(5))
    document, chunks = _chunk(f"# Data\n\n| id | value |\n|---|---|\n{rows}\n")

    block = next(block for block in document.blocks if block.block_type == BlockType.TABLE)
    table_chunks = [chunk for chunk in chunks if chunk.content_type == RetrievalContentType.TABLE]
    assert len(table_chunks) == 3
    assert all(chunk.display_content.startswith("| id | value |\n|---|---|") for chunk in table_chunks)
    assert all(chunk.parent_block_ids == (block.block_id,) for chunk in table_chunks)


def test_mermaid_splits_on_complete_lines_and_headings_do_not_emit_chunks() -> None:
    diagram = "\n".join(["graph TD", "A-->B", "B-->C", "C-->D", "D-->E", "E-->F"])
    document, chunks = _chunk(f"# Flow\n\n```mermaid\n{diagram}\n```\n")

    block = next(block for block in document.blocks if block.block_type == BlockType.MERMAID)
    diagram_chunks = [chunk for chunk in chunks if chunk.content_type == RetrievalContentType.MERMAID]
    assert len(diagram_chunks) == 2
    assert all(chunk.parent_block_ids == (block.block_id,) for chunk in diagram_chunks)
    assert all("mermaid" in chunk.embedding_text.casefold() for chunk in diagram_chunks)
    assert not any(chunk.content_type == RetrievalContentType.HEADING for chunk in chunks)
    assert "\n".join(chunk.display_content for chunk in diagram_chunks) == diagram


def test_ids_are_stable_and_chunks_have_ordered_source_spans() -> None:
    content = "# Stable\n\nFirst paragraph.\n\nSecond paragraph.\n"
    _, first = _chunk(content)
    _, second = _chunk(content)

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert all(chunk.source_span.start_line <= chunk.source_span.end_line for chunk in first)
    assert [chunk.chunk_index for chunk in first] == list(range(len(first)))


def test_final_bounds_cover_oversized_table_rows_and_multibyte_prose() -> None:
    table = f"| id | value |\n|---|---|\n| 1 | {'x' * 300} |"
    _, table_chunks = _chunk(table, target_chars=60, hard_chars=80, max_tokens=20)
    _, prose_chunks = _chunk(
        "# H\n\n" + "漢字" * 100,
        target_chars=60,
        hard_chars=80,
        max_tokens=20,
    )

    chunks = (*table_chunks, *prose_chunks)
    assert chunks
    assert all(len(chunk.display_content) <= 80 for chunk in chunks)
    assert all(chunk.estimated_tokens <= 20 for chunk in chunks)
    assert any("table row" in chunk.display_content for chunk in table_chunks)


def test_oversized_mermaid_line_uses_one_bounded_provenance_marker() -> None:
    source_line = "A" + "-->B" * 100
    document, chunks = _chunk(
        f"```mermaid\n{source_line}\n```",
        target_chars=60,
        hard_chars=80,
        max_tokens=20,
    )

    block = next(block for block in document.blocks if block.block_type == BlockType.MERMAID)
    diagram_chunks = [chunk for chunk in chunks if chunk.content_type == RetrievalContentType.MERMAID]
    assert len(diagram_chunks) == 1
    assert diagram_chunks[0].parent_block_ids == (block.block_id,)
    assert diagram_chunks[0].source_span.start_line == 2
    assert diagram_chunks[0].source_span.end_line == 2
    assert "mermaid line" in diagram_chunks[0].display_content
    assert source_line not in diagram_chunks[0].display_content
    assert len(diagram_chunks[0].display_content) <= 80
    assert diagram_chunks[0].estimated_tokens <= 20


def test_target_chars_changes_normal_prose_boundaries_deterministically() -> None:
    content = " ".join(f"word{index}" for index in range(30))
    _, small = _chunk(content, target_chars=40, hard_chars=200, max_tokens=100)
    _, large = _chunk(content, target_chars=120, hard_chars=200, max_tokens=100)
    _, small_again = _chunk(content, target_chars=40, hard_chars=200, max_tokens=100)

    assert [chunk.display_content for chunk in small] != [chunk.display_content for chunk in large]
    assert [chunk.chunk_id for chunk in small] == [chunk.chunk_id for chunk in small_again]
    assert all(len(chunk.display_content) <= 200 for chunk in (*small, *large))
    assert all(chunk.estimated_tokens <= 100 for chunk in (*small, *large))
