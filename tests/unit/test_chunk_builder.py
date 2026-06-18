"""Tests for mdrack.markdown.chunk_builder."""

from __future__ import annotations

from mdrack.markdown.chunk_builder import (
    _get_overlap_text,
    _split_text_by_sentences,
    _split_text_by_words,
    build_chunks,
)
from mdrack.markdown.ir import BlockType, ContentType, MarkdownBlock, SectionNode
from mdrack.markdown.section_builder import build_sections

FILE_ID = "/docs/test.md"


def _sec(title: str = "Intro", start: int = 1, end: int = 100) -> SectionNode:
    return SectionNode(
        document_id=FILE_ID,
        title=title,
        heading_path=[title],
        level=2,
        start_line=start,
        end_line=end,
    )


def _heading(content: str, start: int) -> MarkdownBlock:
    return MarkdownBlock(type=BlockType.HEADING, content=content, start_line=start, end_line=start)


def _para(content: str, start: int = 1, end: int = 1) -> MarkdownBlock:
    return MarkdownBlock(type=BlockType.PARAGRAPH, content=content, start_line=start, end_line=end)


def _list(content: str, start: int = 1, end: int = 1) -> MarkdownBlock:
    return MarkdownBlock(type=BlockType.LIST, content=content, start_line=start, end_line=end)


def _blockquote(content: str, start: int = 1, end: int = 1) -> MarkdownBlock:
    return MarkdownBlock(type=BlockType.BLOCKQUOTE, content=content, start_line=start, end_line=end)


def _code(content: str, lang: str = "python", start: int = 1, end: int = 1) -> MarkdownBlock:
    return MarkdownBlock(type=BlockType.CODE, content=content, start_line=start, end_line=end, language=lang)


def _mermaid(content: str, start: int = 1, end: int = 1) -> MarkdownBlock:
    return _code(content, lang="mermaid", start=start, end=end)


def _table(content: str, start: int = 1, end: int = 1) -> MarkdownBlock:
    return MarkdownBlock(type=BlockType.TABLE, content=content, start_line=start, end_line=end)


def _break(start: int = 1) -> MarkdownBlock:
    return MarkdownBlock(type=BlockType.THEMATIC_BREAK, content="---", start_line=start, end_line=start)


class TestChunkContentSelection:
    def test_h1_body_is_not_lost(self) -> None:
        blocks = [
            _heading("# Title", 1),
            _para("Important body text stays here.", 2, 3),
        ]
        sections = build_sections(blocks, FILE_ID)
        chunks = build_chunks(blocks, sections, FILE_ID)
        assert len(chunks) == 1
        assert "Important body text stays here." in chunks[0].content

    def test_preamble_before_first_h2_is_chunked(self) -> None:
        blocks = [
            _heading("# Guide", 1),
            _para("Intro paragraph before sections.", 2, 3),
            _heading("## Main", 5),
            _para("Main body paragraph.", 6, 7),
        ]
        sections = build_sections(blocks, FILE_ID)
        chunks = build_chunks(blocks, sections, FILE_ID)
        assert any("Intro paragraph before sections." in chunk.content for chunk in chunks)
        assert any("Main body paragraph." in chunk.content for chunk in chunks)

    def test_thematic_break_does_not_create_chunk(self) -> None:
        blocks = [_heading("## Intro", 1), _break(2), _para("Body text.", 3, 3)]
        sections = [_sec(start=1, end=3)]
        chunks = build_chunks(blocks, sections, FILE_ID)
        assert len(chunks) == 1
        assert all(chunk.content.strip() != "---" for chunk in chunks)

    def test_heading_only_chunk_is_not_emitted(self) -> None:
        blocks = [_heading("## Lonely", 1)]
        sections = [_sec(start=1, end=1)]
        assert build_chunks(blocks, sections, FILE_ID) == []

    def test_nested_child_content_is_not_duplicated(self) -> None:
        blocks = [
            _heading("## Parent", 1),
            _para("Parent intro.", 2, 2),
            _heading("### Child", 4),
            _para("Child body appears once.", 5, 5),
        ]
        sections = build_sections(blocks, FILE_ID)
        chunks = build_chunks(blocks, sections, FILE_ID)
        combined = "\n".join(chunk.content for chunk in chunks)
        assert combined.count("Child body appears once.") == 1


class TestBufferedAssembly:
    def test_small_text_blocks_are_merged(self) -> None:
        blocks = [
            _para("A" * 70, 1, 1),
            _list("- bullet\n- bullet", 2, 3),
            _blockquote("> quoted text", 4, 4),
        ]
        sections = [_sec(start=1, end=4)]
        chunks = build_chunks(
            blocks,
            sections,
            FILE_ID,
            {
                "min_chunk_chars": 120,
                "target_chunk_chars": 500,
                "hard_limit_chars": 1000,
                "overlap_chars": 0,
            },
        )
        assert len(chunks) == 1
        assert chunks[0].content_type == ContentType.TEXT

    def test_small_table_stays_with_neighboring_text(self) -> None:
        blocks = [
            _para("Intro text before table.", 1, 1),
            _table("| X | Y |\n|---|---|\n| 1 | 2 |", 2, 4),
            _para("Conclusion text after table.", 5, 5),
        ]
        sections = [_sec(start=1, end=5)]
        chunks = build_chunks(
            blocks,
            sections,
            FILE_ID,
            {
                "min_chunk_chars": 10,
                "target_chunk_chars": 500,
                "hard_limit_chars": 1000,
                "overlap_chars": 0,
            },
        )
        assert len(chunks) == 1
        assert "| X | Y |" in chunks[0].content
        assert "Conclusion text after table." in chunks[0].content

    def test_small_mermaid_stays_with_neighboring_text(self) -> None:
        blocks = [
            _para("Explain the flow.", 1, 1),
            _mermaid("graph TD\n  A-->B", 2, 4),
            _para("Flow complete.", 5, 5),
        ]
        sections = [_sec(start=1, end=5)]
        chunks = build_chunks(
            blocks,
            sections,
            FILE_ID,
            {
                "min_chunk_chars": 10,
                "target_chunk_chars": 500,
                "hard_limit_chars": 1000,
                "overlap_chars": 0,
            },
        )
        assert len(chunks) == 1
        assert "graph TD" in chunks[0].content
        assert "Flow complete." in chunks[0].content

    def test_large_code_block_is_not_split_inside(self) -> None:
        content = "print('hello')\n" * 700
        blocks = [_code(content, start=1, end=700)]
        sections = [_sec(start=1, end=700)]
        chunks = build_chunks(
            blocks,
            sections,
            FILE_ID,
            {
                "min_chunk_chars": 10,
                "target_chunk_chars": 200,
                "hard_limit_chars": 400,
                "overlap_chars": 0,
            },
        )
        assert len(chunks) == 1
        assert chunks[0].content == content
        assert chunks[0].content_type == ContentType.CODE

    def test_large_table_block_is_not_split_inside(self) -> None:
        row = "| long | row | value |\n"
        content = "| a | b | c |\n|---|---|---|\n" + row * 300
        blocks = [_table(content, start=1, end=302)]
        sections = [_sec(start=1, end=302)]
        chunks = build_chunks(
            blocks,
            sections,
            FILE_ID,
            {
                "min_chunk_chars": 10,
                "target_chunk_chars": 200,
                "hard_limit_chars": 400,
                "overlap_chars": 0,
            },
        )
        assert len(chunks) == 1
        assert chunks[0].content == content
        assert chunks[0].content_type == ContentType.TABLE

    def test_large_mermaid_block_is_not_split_inside(self) -> None:
        content = "graph TD\n" + "\n".join(f"  A{i}-->B{i}" for i in range(500))
        blocks = [_mermaid(content, start=1, end=501)]
        sections = [_sec(start=1, end=501)]
        chunks = build_chunks(
            blocks,
            sections,
            FILE_ID,
            {
                "min_chunk_chars": 10,
                "target_chunk_chars": 200,
                "hard_limit_chars": 400,
                "overlap_chars": 0,
            },
        )
        assert len(chunks) == 1
        assert chunks[0].content == content
        assert chunks[0].content_type == ContentType.MERMAID


class TestChunkMergingAndLinks:
    def test_first_small_chunk_merges_forward(self) -> None:
        blocks = [
            _para("short intro", 1, 1),
            _para("B" * 180, 2, 2),
        ]
        sections = [_sec(start=1, end=2)]
        chunks = build_chunks(
            blocks,
            sections,
            FILE_ID,
            {
                "min_chunk_chars": 100,
                "target_chunk_chars": 10,
                "hard_limit_chars": 400,
                "overlap_chars": 0,
            },
        )
        assert len(chunks) == 1
        assert "short intro" in chunks[0].content
        assert "B" * 180 in chunks[0].content

    def test_links_are_correct_after_small_chunk_merge(self) -> None:
        blocks = [
            _para("A" * 130, 1, 1),
            _table("|x|y|\n|---|---|\n|1|2|", 2, 4),
            _para("B" * 130, 5, 5),
            _para("C" * 130, 6, 6),
        ]
        sections = [_sec(start=1, end=6)]
        chunks = build_chunks(
            blocks,
            sections,
            FILE_ID,
            {
                "min_chunk_chars": 100,
                "target_chunk_chars": 100,
                "hard_limit_chars": 160,
                "overlap_chars": 0,
            },
        )
        assert len(chunks) == 3
        assert chunks[0].previous_chunk_id is None
        assert chunks[-1].next_chunk_id is None
        assert chunks[1].previous_chunk_id == chunks[0].id
        assert chunks[1].next_chunk_id == chunks[2].id

    def test_min_chunk_chars_reduces_tiny_chunk_count(self) -> None:
        blocks = [
            _para("A" * 130, 1, 1),
            _table("|x|y|\n|---|---|\n|1|2|", 2, 4),
            _para("B" * 140, 5, 5),
        ]
        sections = [_sec(start=1, end=5)]
        low_min = build_chunks(
            blocks,
            sections,
            FILE_ID,
            {
                "min_chunk_chars": 10,
                "target_chunk_chars": 100,
                "hard_limit_chars": 160,
                "overlap_chars": 0,
            },
        )
        high_min = build_chunks(
            blocks,
            sections,
            FILE_ID,
            {
                "min_chunk_chars": 100,
                "target_chunk_chars": 100,
                "hard_limit_chars": 160,
                "overlap_chars": 0,
            },
        )
        assert len(high_min) < len(low_min)


class TestOverlap:
    def test_overlap_present_between_text_chunks(self) -> None:
        blocks = [
            _para("Alpha beta gamma delta epsilon. " * 40, 1, 5),
            _para("Zeta eta theta iota kappa lambda. " * 40, 6, 10),
        ]
        sections = [_sec(start=1, end=10)]
        chunks = build_chunks(
            blocks,
            sections,
            FILE_ID,
            {
                "min_chunk_chars": 100,
                "target_chunk_chars": 500,
                "hard_limit_chars": 700,
                "overlap_chars": 50,
            },
        )
        assert len(chunks) >= 2
        overlap_words = set(_get_overlap_text(chunks[0].content, 50).split())
        assert overlap_words.intersection(set(chunks[1].content.split()))

    def test_overlap_does_not_cross_section_boundaries(self) -> None:
        sections = [
            _sec(title="A", start=1, end=1),
            _sec(title="B", start=2, end=2),
        ]
        blocks = [
            _para("Alpha beta gamma delta epsilon. " * 6, 1, 1),
            _para("Section two starts cleanly.", 2, 2),
        ]
        chunks = build_chunks(
            blocks,
            sections,
            FILE_ID,
            {
                "min_chunk_chars": 1,
                "target_chunk_chars": 200,
                "hard_limit_chars": 400,
                "overlap_chars": 50,
            },
        )
        assert len(chunks) == 2
        assert chunks[1].content == "Section two starts cleanly."

    def test_overlap_keeps_final_chunk_under_hard_limit(self) -> None:
        blocks = [
            _para("Alpha beta gamma delta epsilon. " * 9, 1, 1),
            _para("B" * 390, 2, 2),
        ]
        sections = [_sec(start=1, end=2)]
        chunks = build_chunks(
            blocks,
            sections,
            FILE_ID,
            {
                "min_chunk_chars": 1,
                "target_chunk_chars": 300,
                "hard_limit_chars": 400,
                "overlap_chars": 50,
            },
        )
        assert len(chunks) == 2
        assert len(chunks[1].content) <= 400


class TestHelpers:
    def test_get_overlap_text_short(self) -> None:
        assert _get_overlap_text("hello", 10) == "hello"

    def test_get_overlap_text_long(self) -> None:
        text = "first second third fourth fifth"
        result = _get_overlap_text(text, 15)
        assert len(result) <= 15
        assert result in text

    def test_split_sentences(self) -> None:
        text = "First sentence. Second sentence. Third sentence."
        pieces = _split_text_by_sentences(text, 25)
        assert len(pieces) >= 2
        assert "First sentence" in " ".join(pieces)

    def test_split_words(self) -> None:
        text = "one two three four five six seven eight"
        pieces = _split_text_by_words(text, 15)
        assert len(pieces) >= 2
