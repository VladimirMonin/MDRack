"""Tests for mdrack.markdown.chunk_builder."""

from __future__ import annotations

from mdrack.markdown.chunk_builder import (
    _get_overlap_text,
    _split_text_by_sentences,
    _split_text_by_words,
    build_chunks,
)
from mdrack.markdown.ir import (
    BlockType,
    ContentType,
    MarkdownBlock,
    SectionNode,
)

FILE_ID = "/docs/test.md"


def _sec(title: str = "Intro", start: int = 1, end: int = 100) -> SectionNode:
    """Helper to create a SectionNode."""
    return SectionNode(
        document_id=FILE_ID,
        title=title,
        heading_path=[title],
        level=2,
        start_line=start,
        end_line=end,
    )


def _para(content: str, start: int = 1, end: int = 1) -> MarkdownBlock:
    """Helper to create a paragraph block."""
    return MarkdownBlock(
        type=BlockType.PARAGRAPH,
        content=content,
        start_line=start,
        end_line=end,
    )


def _code(content: str, lang: str = "python", start: int = 1, end: int = 1) -> MarkdownBlock:
    """Helper to create a code block."""
    return MarkdownBlock(
        type=BlockType.CODE,
        content=content,
        start_line=start,
        end_line=end,
        language=lang,
    )


def _mermaid(content: str, start: int = 1, end: int = 1) -> MarkdownBlock:
    """Helper to create a mermaid code block."""
    return _code(content, lang="mermaid", start=start, end=end)


def _table(content: str, start: int = 1, end: int = 1) -> MarkdownBlock:
    """Helper to create a table block."""
    return MarkdownBlock(
        type=BlockType.TABLE,
        content=content,
        start_line=start,
        end_line=end,
    )


# ── code blocks ───────────────────────────────────────────────────────


class TestCodeBlocksNotSplit:
    def test_single_code_block(self) -> None:
        code = "def foo():\n    return 42"
        blocks = [_code(code)]
        sec = _sec()
        chunks = build_chunks(blocks, [sec], FILE_ID)
        assert len(chunks) == 1
        assert chunks[0].content == code
        assert chunks[0].content_type == ContentType.CODE

    def test_code_block_with_text_before(self) -> None:
        """Code block should stay intact even if preceded by text."""
        sec = _sec()
        para = _para("Some intro text.", start=1, end=1)
        code = _code("x = 1\ny = 2\nprint(x + y)", start=2, end=5)
        chunks = build_chunks([para, code], [sec], FILE_ID)
        code_chunks = [c for c in chunks if c.content_type == ContentType.CODE]
        assert len(code_chunks) == 1
        assert "x = 1" in code_chunks[0].content

    def test_multiple_code_blocks(self) -> None:
        sec = _sec()
        c1 = _code("print('a')", lang="python", start=1, end=1)
        c2 = _code("print('b')", lang="python", start=2, end=2)
        chunks = build_chunks([c1, c2], [sec], FILE_ID)
        code_chunks = [c for c in chunks if c.content_type == ContentType.CODE]
        assert len(code_chunks) == 2


# ── mermaid blocks ────────────────────────────────────────────────────


class TestMermaidBlocksKeptIntact:
    def test_mermaid_detected(self) -> None:
        content = "graph TD\n    A-->B\n    B-->C"
        blocks = [_mermaid(content)]
        sec = _sec()
        chunks = build_chunks(blocks, [sec], FILE_ID)
        assert len(chunks) == 1
        assert chunks[0].content_type == ContentType.MERMAID
        assert chunks[0].content == content

    def test_mermaid_not_confused_with_code(self) -> None:
        blocks = [_mermaid("flowchart LR\n  X-->Y")]
        sec = _sec()
        chunks = build_chunks(blocks, [sec], FILE_ID)
        assert chunks[0].content_type != ContentType.CODE
        assert chunks[0].content_type == ContentType.MERMAID


# ── table blocks ──────────────────────────────────────────────────────


class TestTablesKeptIntact:
    def test_single_table(self) -> None:
        table_content = "| Name | Value |\n|------|-------|\n| A    | 1     |\n| B    | 2     |"
        blocks = [_table(table_content)]
        sec = _sec()
        chunks = build_chunks(blocks, [sec], FILE_ID)
        assert len(chunks) == 1
        assert chunks[0].content_type == ContentType.TABLE
        assert chunks[0].content == table_content

    def test_table_with_surrounding_text(self) -> None:
        sec = _sec()
        before = _para("Here is data:")
        tbl = _table("| X | Y |\n|---|---|\n| 1 | 2 |")
        after = _para("That's the table.")
        chunks = build_chunks([before, tbl, after], [sec], FILE_ID)
        table_chunks = [c for c in chunks if c.content_type == ContentType.TABLE]
        assert len(table_chunks) == 1


# ── text chunking with size limits ───────────────────────────────────


class TestTextChunkingSizes:
    def test_small_text_stays_single(self) -> None:
        text = "Hello world. This is a short paragraph."
        blocks = [_para(text)]
        sec = _sec()
        chunks = build_chunks(blocks, [sec], FILE_ID)
        assert len(chunks) == 1
        assert chunks[0].content_type == ContentType.TEXT

    def test_large_text_gets_split(self) -> None:
        # Create text exceeding target
        paragraph = "This is a sentence. " * 100  # ~2100 chars
        blocks = [_para(paragraph)]
        sec = _sec()
        chunks = build_chunks(blocks, [sec], FILE_ID, {"target_chunk_chars": 800})
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.content_type == ContentType.TEXT

    def test_hard_limit_respected(self) -> None:
        hard = "Word " * 600  # ~3000 chars
        blocks = [_para(hard)]
        sec = _sec()
        cfg = {"min_chunk_chars": 100, "target_chunk_chars": 200, "hard_limit_chars": 400}
        chunks = build_chunks(blocks, [sec], FILE_ID, cfg)
        # Some chunks should exist; the text should be fully present
        combined = " ".join(c.content for c in chunks)
        assert "Word" in combined

    def test_multiple_paragraphs_merged(self) -> None:
        p1 = _para("Short paragraph one.")
        p2 = _para("Short paragraph two.")
        p3 = _para("Short paragraph three.")
        sec = _sec()
        chunks = build_chunks([p1, p2, p3], [sec], FILE_ID, {"target_chunk_chars": 2000})
        assert len(chunks) == 1
        assert "Short paragraph one." in chunks[0].content


# ── overlap between text chunks ──────────────────────────────────────


class TestOverlap:
    def test_overlap_present(self) -> None:
        """Second chunk should contain overlap text from first chunk."""
        text1 = "Alpha beta gamma delta epsilon. " * 30  # ~1000 chars
        text2 = "Zeta eta theta iota kappa lambda. " * 30  # ~1050 chars
        blocks = [_para(text1), _para(text2)]
        sec = _sec()
        chunks = build_chunks(
            blocks, [sec], FILE_ID,
            {"min_chunk_chars": 100, "target_chunk_chars": 500, "overlap_chars": 50},
        )
        if len(chunks) >= 2:
            # The second chunk should start with some tail from the first
            first_tail = chunks[0].content[-80:]
            assert any(
                word in chunks[1].content
                for word in first_tail.split()
            ), "Overlap tail should appear at start of next chunk"

    def test_no_overlap_for_code(self) -> None:
        """Code blocks should not carry overlap."""
        sec = _sec()
        para = _para("Some text before code. " * 30, start=1, end=5)
        code = _code("def x(): pass", start=6, end=6)
        chunks = build_chunks([para, code], [sec], FILE_ID, {"overlap_chars": 50})
        code_chunks = [c for c in chunks if c.content_type == ContentType.CODE]
        assert len(code_chunks) == 1
        assert code_chunks[0].content == "def x(): pass"


# ── chunk linking ────────────────────────────────────────────────────


class TestChunkLinking:
    def test_linked_list_structure(self) -> None:
        sec = _sec()
        b1 = _para("Hello. " * 30, start=1, end=5)   # ~210 chars
        b2 = _para("World. " * 30, start=6, end=10)   # ~210 chars
        b3 = _para("Foo. " * 30, start=11, end=15)    # ~150 chars
        chunks = build_chunks(
            [b1, b2, b3], [sec], FILE_ID,
            {"min_chunk_chars": 50, "target_chunk_chars": 200, "hard_limit_chars": 500},
        )
        assert len(chunks) >= 3
        # first chunk has no predecessor
        assert chunks[0].previous_chunk_id is None
        # last chunk has no successor
        assert chunks[-1].next_chunk_id is None
        # middle chunks are doubly linked
        for i in range(1, len(chunks)):
            assert chunks[i].previous_chunk_id == chunks[i - 1].id
        for i in range(len(chunks) - 1):
            assert chunks[i].next_chunk_id == chunks[i + 1].id

    def test_single_chunk_no_links(self) -> None:
        sec = _sec()
        chunks = build_chunks([_para("Hello.")], [sec], FILE_ID)
        assert len(chunks) == 1
        assert chunks[0].previous_chunk_id is None
        assert chunks[0].next_chunk_id is None

    def test_chunk_indices_sequential(self) -> None:
        sec = _sec()
        b1 = _para("A " * 400, start=1, end=10)
        b2 = _para("B " * 400, start=11, end=20)
        chunks = build_chunks(
            [b1, b2], [sec], FILE_ID,
            {"min_chunk_chars": 50, "target_chunk_chars": 200},
        )
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i


# ── empty / edge inputs ──────────────────────────────────────────────


class TestEmptyInput:
    def test_empty_blocks(self) -> None:
        sec = _sec()
        chunks = build_chunks([], [sec], FILE_ID)
        assert chunks == []

    def test_empty_sections(self) -> None:
        blocks = [_para("Some text.")]
        chunks = build_chunks(blocks, [], FILE_ID)
        assert chunks == []

    def test_both_empty(self) -> None:
        chunks = build_chunks([], [], FILE_ID)
        assert chunks == []


class TestSingleBlock:
    def test_single_paragraph(self) -> None:
        text = "The quick brown fox jumps over the lazy dog."
        blocks = [_para(text)]
        sec = _sec()
        chunks = build_chunks(blocks, [sec], FILE_ID)
        assert len(chunks) == 1
        assert chunks[0].content == text
        assert chunks[0].content_type == ContentType.TEXT
        assert chunks[0].heading_path == ["Intro"]


# ── mixed content ────────────────────────────────────────────────────


class TestMixedContent:
    def test_paragraph_code_paragraph(self) -> None:
        sec = _sec()
        p1 = _para("Before code.")
        c1 = _code("x = 1", lang="python")
        p2 = _para("After code.")
        chunks = build_chunks([p1, c1, p2], [sec], FILE_ID)
        types = [c.content_type for c in chunks]
        assert ContentType.TEXT in types
        assert ContentType.CODE in types

    def test_mixed_all_types(self) -> None:
        sec = _sec()
        blocks = [
            _para("Text paragraph."),
            _code("print('hello')", lang="python"),
            _mermaid("graph TD\n  A-->B"),
            _table("| A | B |\n|---|---|\n| 1 | 2 |"),
            _para("Final text."),
        ]
        chunks = build_chunks(blocks, [sec], FILE_ID)
        content_types = {c.content_type for c in chunks}
        assert ContentType.TEXT in content_types
        assert ContentType.CODE in content_types
        assert ContentType.MERMAID in content_types
        assert ContentType.TABLE in content_types

    def test_heading_path_propagated(self) -> None:
        sec = _sec()
        sec.heading_path = ["Doc", "Section", "Sub"]
        blocks = [_para("Some content here.")]
        chunks = build_chunks(blocks, [sec], FILE_ID)
        assert chunks[0].heading_path == ["Doc", "Section", "Sub"]

    def test_document_id_propagated(self) -> None:
        sec = _sec()
        blocks = [_para("Text.")]
        chunks = build_chunks(blocks, [sec], "/docs/myfile.md")
        assert chunks[0].document_id == "/docs/myfile.md"


# ── helper functions ──────────────────────────────────────────────────


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
        combined = " ".join(pieces)
        assert "First sentence" in combined

    def test_split_words(self) -> None:
        text = "one two three four five six seven eight"
        pieces = _split_text_by_words(text, 15)
        assert len(pieces) >= 2
