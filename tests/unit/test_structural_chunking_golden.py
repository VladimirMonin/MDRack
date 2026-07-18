"""Golden contracts for production structural chunking."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mdrack.adapters.markdown_it import MarkdownItParser
from mdrack.application.chunking import StructuralChunker, StructuralChunkingConfig
from mdrack.config.models import ChunkingConfig
from mdrack.domain.blocks import BlockType, SourceBlock
from mdrack.domain.chunks import RetrievalChunk, RetrievalContentType

FIXTURES = Path(__file__).parents[1] / "fixtures" / "structural_chunking"


def _config(**overrides: int) -> StructuralChunkingConfig:
    values = {
        "min_chars": 70,
        "target_chars": 120,
        "hard_limit_chars": 220,
        "max_tokens": 100,
        "overlap_chars": 0,
        "code_window_lines": 50,
        "table_rows_per_chunk": 2,
        "mermaid_window_lines": 2,
    }
    values.update(overrides)
    return StructuralChunkingConfig(**values)


def _parse(name: str):
    path = FIXTURES / name
    content = path.read_bytes().decode("utf-8")
    return content, MarkdownItParser().parse(
        path,
        content=content,
        document_id=f"doc_{path.stem}",
        relative_path=path.name,
    )


def _snapshot(chunks: tuple[RetrievalChunk, ...]) -> list[dict[str, object]]:
    return [
        {
            "id": chunk.chunk_id,
            "parents": list(chunk.parent_block_ids),
            "display": chunk.display_content,
            "type": chunk.content_type.value,
            "index": chunk.chunk_index,
            "heading_path": list(chunk.heading_path),
            "span": {
                "start_line": chunk.source_span.start_line,
                "end_line": chunk.source_span.end_line,
                "start_offset": chunk.source_span.start_offset,
                "end_offset": chunk.source_span.end_offset,
            },
        }
        for chunk in chunks
    ]


def _owned_interval(block: SourceBlock) -> tuple[int, int] | None:
    start = block.source_span.start_offset
    end = block.source_span.end_offset
    if start is None or end is None:
        return None
    if block.block_type in {BlockType.CODE, BlockType.MERMAID}:
        raw = block.raw_markdown
        if raw.lstrip().startswith(("```", "~~~")):
            relative_start = raw.index("\n") + 1
            relative_end = max(raw.rfind("\n```"), raw.rfind("\n~~~"))
            assert relative_end >= relative_start
            if raw[relative_end - 1 : relative_end] == "\r":
                relative_end -= 1
            return start + relative_start, start + relative_end
        content = block.plain_text or ""
        relative_start = raw.find(content)
        assert relative_start >= 0
        return start + relative_start, start + relative_start + len(content)
    return start, end


def test_golden_fixtures_are_lossless_non_overlapping_and_deterministic(tmp_path: Path) -> None:
    expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
    for name in (
        "multilingual.md",
        "long_prose.md",
        "python_valid.md",
        "code_fallbacks.md",
        "structures.md",
        "crlf.md",
    ):
        source, document = _parse(name)
        chunker = StructuralChunker(_config())
        first = chunker.build(document)
        second = chunker.build(document)

        assert first
        assert _snapshot(first) == _snapshot(second)
        encoded_snapshot = json.dumps(
            _snapshot(first),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert len(first) == expected[name]["chunk_count"]
        assert hashlib.sha256(encoded_snapshot).hexdigest() == expected[name]["snapshot_sha256"]
        assert all(chunk.display_content.strip() for chunk in first)
        assert all(chunk.source_span.start_offset is not None for chunk in first)
        assert all(chunk.source_span.end_offset is not None for chunk in first)

        intervals = sorted(
            (chunk.source_span.start_offset, chunk.source_span.end_offset)
            for chunk in first
        )
        assert all(start is not None and end is not None and start < end for start, end in intervals)
        assert all(left[1] <= right[0] for left, right in zip(intervals, intervals[1:], strict=False))

        owned = [
            (block.block_type, interval)
            for block in document.blocks
            if block.block_type not in {BlockType.FRONTMATTER, BlockType.HEADING, BlockType.THEMATIC_BREAK}
            if (interval := _owned_interval(block)) is not None
            if any(block.block_id in chunk.parent_block_ids for chunk in first)
        ]
        prose_family = {
            BlockType.PARAGRAPH,
            BlockType.LIST,
            BlockType.BLOCKQUOTE,
            BlockType.CALLOUT,
            BlockType.UNKNOWN,
        }
        for block_type, (owned_start, owned_end) in owned:
            if block_type not in prose_family:
                assert all(
                    character.isspace()
                    or any(
                        start is not None and end is not None and start <= offset < end
                        for start, end in intervals
                    )
                    for offset, character in enumerate(
                        source[owned_start:owned_end],
                        start=owned_start,
                    )
                )
                continue
            block_intervals = [
                (max(start, owned_start), min(end, owned_end))
                for start, end in intervals
                if start is not None and end is not None and start < owned_end and end > owned_start
            ]
            assert block_intervals[0][0] == owned_start
            assert block_intervals[-1][1] == owned_end
            assert all(
                left_end == right_start
                for (_, left_end), (right_start, _) in zip(
                    block_intervals,
                    block_intervals[1:],
                    strict=False,
                )
            )
            assert "".join(source[start:end] for start, end in block_intervals) == source[
                owned_start:owned_end
            ]

def test_minimum_size_merges_only_adjacent_compatible_blocks() -> None:
    _, document = _parse("structures.md")
    chunks = StructuralChunker(_config(min_chars=80, target_chars=150)).build(document)

    intro_blocks = [
        block
        for block in document.blocks
        if block.block_type == BlockType.PARAGRAPH and block.heading_path == ("Structures",)
    ][:2]
    merged = next(
        chunk
        for chunk in chunks
        if chunk.parent_block_ids == tuple(block.block_id for block in intro_blocks)
    )
    assert merged.content_type == RetrievalContentType.TEXT
    assert "Tiny intro." in merged.display_content
    assert "Another tiny paragraph." in merged.display_content

    table = next(chunk for chunk in chunks if chunk.content_type == RetrievalContentType.TABLE)
    mermaid = next(chunk for chunk in chunks if chunk.content_type == RetrievalContentType.MERMAID)
    assert set(merged.parent_block_ids).isdisjoint(table.parent_block_ids)
    assert set(merged.parent_block_ids).isdisjoint(mermaid.parent_block_ids)
    assert all(len(chunk.parent_block_ids) == 1 for chunk in chunks if chunk.content_type != RetrievalContentType.TEXT)


def test_python_uses_ast_boundaries_and_fallbacks_keep_complete_lines() -> None:
    _, valid_document = _parse("python_valid.md")
    valid = [
        chunk.display_content
        for chunk in StructuralChunker(_config(hard_limit_chars=500, target_chars=400)).build(valid_document)
        if chunk.content_type == RetrievalContentType.CODE
    ]
    assert len(valid) == 5
    assert valid[0].startswith('"""Synthetic module."""')
    assert valid[1].startswith("class Calculator:")
    assert valid[2].startswith("def helper")
    assert valid[3].startswith("async def async_helper")
    assert valid[4] == "RESULT = helper(CONSTANT)"

    _, fallback_document = _parse("code_fallbacks.md")
    fallback = [
        chunk.display_content
        for chunk in StructuralChunker(_config(code_window_lines=2)).build(fallback_document)
        if chunk.content_type == RetrievalContentType.CODE
    ]
    assert len(fallback) >= 4
    assert "\n".join(fallback).splitlines() == [
        line
        for block in fallback_document.blocks
        if block.block_type == BlockType.CODE
        for line in (block.plain_text or "").splitlines()
    ]


@pytest.mark.parametrize(
    ("newline", "source"),
    (
        ("LF", "Before ![Alt](images/a.png) after.\n![[b.png|Alias]]"),
        ("CRLF", 'Before\r\n<img src="images/a.png" alt="HTML Alt">\r\nafter.'),
    ),
)
def test_image_syntax_projects_as_text_chunks_with_lossless_stable_provenance(
    newline: str,
    source: str,
    tmp_path: Path,
) -> None:
    parser = MarkdownItParser()
    first_document = parser.parse(
        tmp_path / "projection.md",
        content=source,
        document_id="doc_projection",
        relative_path="projection.md",
    )
    second_document = parser.parse(
        tmp_path / "projection.md",
        content=source,
        document_id="doc_projection",
        relative_path="projection.md",
    )
    chunker = StructuralChunker(_config(min_chars=1))
    first_chunks = chunker.build(first_document)
    second_chunks = chunker.build(second_document)
    assert newline in {"LF", "CRLF"}
    assert first_document.source_hash == hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert [block.raw_markdown for block in first_document.blocks] == [
        block.raw_markdown for block in second_document.blocks
    ]
    assert [block.block_id for block in first_document.blocks] == [
        block.block_id for block in second_document.blocks
    ]
    assert _snapshot(first_chunks) == _snapshot(second_chunks)
    assert all(block.block_type.value != "image_reference" for block in first_document.blocks)
    assert all(chunk.content_type.value != "image_reference" for chunk in first_chunks)
    projected = "\n".join(chunk.display_content for chunk in first_chunks)
    assert "images/" not in projected
    assert "src=" not in projected
    expected_alt = "HTML Alt" if newline == "CRLF" else "Alt"
    assert projected.count(expected_alt) == 1
    if "Alias" in source:
        assert projected.count("Alias") == 1


def test_oversized_code_and_mermaid_lines_remain_lossless_and_non_overlapping() -> None:
    source, document = _parse("oversized_lines.md")
    chunks = StructuralChunker(
        _config(min_chars=1, target_chars=60, hard_limit_chars=80, max_tokens=100)
    ).build(document)

    for block in document.blocks:
        if block.block_type not in {BlockType.CODE, BlockType.MERMAID}:
            continue
        owned = [chunk for chunk in chunks if chunk.parent_block_ids == (block.block_id,)]
        assert len(owned) > 1
        assert all("omitted" not in chunk.display_content for chunk in owned)
        assert all(len(chunk.display_content) <= 80 for chunk in owned)
        intervals = []
        for chunk in owned:
            start = chunk.source_span.start_offset
            end = chunk.source_span.end_offset
            assert start is not None and end is not None
            intervals.append((start, end))
        expected_interval = _owned_interval(block)
        assert expected_interval is not None
        expected_start, expected_end = expected_interval
        assert intervals[0][0] == expected_start
        assert intervals[-1][1] == expected_end
        assert all(
            left[1] == right[0]
            for left, right in zip(intervals, intervals[1:], strict=False)
        )
        assert "".join(source[start:end] for start, end in intervals) == source[
            expected_start:expected_end
        ]


def test_long_prose_uses_sentences_then_word_and_character_fallback() -> None:
    source, document = _parse("long_prose.md")
    chunks = StructuralChunker(
        _config(min_chars=1, target_chars=74, hard_limit_chars=90, max_tokens=100)
    ).build(document)
    prose_chunks = [chunk for chunk in chunks if chunk.content_type == RetrievalContentType.TEXT]
    text = [chunk.display_content for chunk in prose_chunks]

    assert len(text) > 4
    assert all(len(part) <= 90 for part in text)
    for block in document.blocks:
        if block.block_type != BlockType.PARAGRAPH:
            continue
        owned = [chunk for chunk in prose_chunks if chunk.parent_block_ids == (block.block_id,)]
        intervals = [
            (chunk.source_span.start_offset, chunk.source_span.end_offset) for chunk in owned
        ]
        assert all(start is not None and end is not None for start, end in intervals)
        exact_intervals = [
            (start, end) for start, end in intervals if start is not None and end is not None
        ]
        assert exact_intervals[0][0] == block.source_span.start_offset
        assert exact_intervals[-1][1] == block.source_span.end_offset
        assert all(
            left_end == right_start
            for (_, left_end), (right_start, _) in zip(
                exact_intervals,
                exact_intervals[1:],
                strict=False,
            )
        )
        assert "".join(source[start:end] for start, end in exact_intervals) == block.raw_markdown
    assert any(part.startswith("Supercalifragilistic") for part in text)


def test_chunking_limits_reject_incoherent_min_target_hard_order() -> None:
    with pytest.raises(ValueError, match="min_chars"):
        StructuralChunkingConfig(min_chars=200, target_chars=100, hard_limit_chars=300)
    with pytest.raises(ValidationError):
        ChunkingConfig(min_chunk_chars=200, target_chunk_chars=100, hard_limit_chars=300)
