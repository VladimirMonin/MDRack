"""Tests for MDRack Markdown IR models."""

from __future__ import annotations

import uuid

import pytest

from mdrack.markdown.ir import (
    BlockType,
    ContentType,
    FinalChunk,
    MarkdownBlock,
    ParsedDocument,
    SectionNode,
)

# ── MarkdownBlock ────────────────────────────────────────────────────────────


class TestMarkdownBlock:
    """Tests for the MarkdownBlock dataclass."""

    def test_create_heading_block(self) -> None:
        block = MarkdownBlock(
            type=BlockType.HEADING,
            content="# Hello",
            start_line=1,
            end_line=1,
        )
        assert block.type == BlockType.HEADING
        assert block.content == "# Hello"
        assert block.language is None

    def test_create_code_block_with_language(self) -> None:
        block = MarkdownBlock(
            type=BlockType.CODE,
            content="print('hello')",
            start_line=5,
            end_line=7,
            language="python",
        )
        assert block.language == "python"
        assert block.start_line == 5
        assert block.end_line == 7

    def test_all_block_types(self) -> None:
        for bt in BlockType:
            kwargs: dict[str, object] = {
                "type": bt,
                "content": "test",
                "start_line": 1,
                "end_line": 1,
            }
            if bt == BlockType.CODE:
                kwargs["language"] = "python"
            block = MarkdownBlock(**kwargs)
            assert block.type == bt

    def test_start_line_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="start_line must be >= 1"):
            MarkdownBlock(
                type=BlockType.PARAGRAPH,
                content="x",
                start_line=0,
                end_line=1,
            )

    def test_end_line_must_be_gte_start_line(self) -> None:
        with pytest.raises(ValueError, match="end_line.*must be >= start_line"):
            MarkdownBlock(
                type=BlockType.PARAGRAPH,
                content="x",
                start_line=5,
                end_line=3,
            )

    def test_code_block_requires_language(self) -> None:
        with pytest.raises(ValueError, match="language is required for code blocks"):
            MarkdownBlock(
                type=BlockType.CODE,
                content="x = 1",
                start_line=1,
                end_line=1,
            )

    def test_non_code_block_allows_no_language(self) -> None:
        block = MarkdownBlock(
            type=BlockType.PARAGRAPH,
            content="text",
            start_line=1,
            end_line=1,
            language=None,
        )
        assert block.language is None


# ── SectionNode ──────────────────────────────────────────────────────────────


class TestSectionNode:
    """Tests for the SectionNode dataclass."""

    def test_create_with_defaults(self) -> None:
        node = SectionNode()
        assert isinstance(node.id, str)
        uuid.UUID(node.id)  # validate it's a valid UUID
        assert node.level == 1
        assert node.heading_path == []
        assert node.parent_id is None

    def test_create_with_explicit_values(self) -> None:
        node = SectionNode(
            id="custom-id",
            document_id="doc-1",
            title="Intro",
            heading_path=["Root", "Intro"],
            level=2,
            start_line=10,
            end_line=20,
            parent_id="parent-id",
        )
        assert node.id == "custom-id"
        assert node.document_id == "doc-1"
        assert node.level == 2
        assert node.heading_path == ["Root", "Intro"]
        assert node.parent_id == "parent-id"

    def test_id_generated_as_uuid(self) -> None:
        node = SectionNode()
        parsed = uuid.UUID(node.id)
        assert parsed.version == 4

    def test_level_1_is_valid(self) -> None:
        node = SectionNode(level=1)
        assert node.level == 1

    def test_level_4_is_valid(self) -> None:
        node = SectionNode(level=4)
        assert node.level == 4

    def test_level_0_is_invalid(self) -> None:
        with pytest.raises(ValueError, match="level must be in \\[1, 4\\]"):
            SectionNode(level=0)

    def test_level_5_is_invalid(self) -> None:
        with pytest.raises(ValueError, match="level must be in \\[1, 4\\]"):
            SectionNode(level=5)

    def test_level_negative_is_invalid(self) -> None:
        with pytest.raises(ValueError, match="level must be in \\[1, 4\\]"):
            SectionNode(level=-1)


# ── FinalChunk ───────────────────────────────────────────────────────────────


class TestFinalChunk:
    """Tests for the FinalChunk dataclass."""

    def test_create_with_defaults(self) -> None:
        chunk = FinalChunk()
        uuid.UUID(chunk.id)  # valid UUID
        assert chunk.content_type == ContentType.TEXT
        assert chunk.chunk_index == 0
        assert chunk.previous_chunk_id is None
        assert chunk.next_chunk_id is None

    def test_create_with_explicit_values(self) -> None:
        chunk = FinalChunk(
            id="chunk-1",
            document_id="doc-1",
            section_id="sec-1",
            content="some text",
            content_type=ContentType.CODE,
            chunk_index=3,
            heading_path=["Root", "Section"],
            previous_chunk_id="prev",
            next_chunk_id="next",
        )
        assert chunk.content_type == ContentType.CODE
        assert chunk.previous_chunk_id == "prev"
        assert chunk.next_chunk_id == "next"

    def test_id_generated_as_uuid(self) -> None:
        chunk = FinalChunk()
        parsed = uuid.UUID(chunk.id)
        assert parsed.version == 4

    def test_all_content_types(self) -> None:
        for ct in ContentType:
            chunk = FinalChunk(content_type=ct)
            assert chunk.content_type == ct

    def test_content_type_rejects_non_enum(self) -> None:
        with pytest.raises(TypeError, match="content_type must be a ContentType enum"):
            FinalChunk(content_type="text")  # type: ignore[arg-type]

    def test_content_type_rejects_int(self) -> None:
        with pytest.raises(TypeError, match="content_type must be a ContentType enum"):
            FinalChunk(content_type=1)  # type: ignore[arg-type]

    def test_doubly_linked_list_pattern(self) -> None:
        c1 = FinalChunk(id="c1", chunk_index=0)
        c2 = FinalChunk(id="c2", chunk_index=1, previous_chunk_id="c1")
        c3 = FinalChunk(id="c3", chunk_index=2, previous_chunk_id="c2")

        c1.next_chunk_id = "c2"
        c2.next_chunk_id = "c3"

        assert c1.next_chunk_id == c2.id
        assert c2.previous_chunk_id == c1.id
        assert c2.next_chunk_id == c3.id
        assert c3.previous_chunk_id == c2.id


# ── ParsedDocument ───────────────────────────────────────────────────────────


class TestParsedDocument:
    """Tests for the ParsedDocument dataclass."""

    def test_create_with_absolute_path(self) -> None:
        doc = ParsedDocument(file_path="/docs/test.md")
        assert doc.file_path == "/docs/test.md"
        assert doc.frontmatter == {}
        assert doc.blocks == []

    def test_create_with_all_fields(self) -> None:
        block = MarkdownBlock(
            type=BlockType.PARAGRAPH,
            content="hello",
            start_line=1,
            end_line=1,
        )
        doc = ParsedDocument(
            file_path="C:/docs/test.md",
            relative_path="test.md",
            title="Test Doc",
            frontmatter={"author": "test"},
            blocks=[block],
            source_hash="abc123",
        )
        assert doc.title == "Test Doc"
        assert len(doc.blocks) == 1
        assert doc.source_hash == "abc123"

    def test_rejects_relative_path(self) -> None:
        with pytest.raises(ValueError, match="file_path must be absolute"):
            ParsedDocument(file_path="relative/path.md")

    def test_rejects_relative_path_windows_style(self) -> None:
        with pytest.raises(ValueError, match="file_path must be absolute"):
            ParsedDocument(file_path="docs/test.md")

    def test_windows_absolute_path_accepted(self) -> None:
        doc = ParsedDocument(file_path="C:/Users/test/doc.md")
        assert doc.file_path == "C:/Users/test/doc.md"

    def test_unix_absolute_path_accepted(self) -> None:
        doc = ParsedDocument(file_path="/home/user/doc.md")
        assert doc.file_path == "/home/user/doc.md"


# ── Enum completeness ────────────────────────────────────────────────────────


class TestEnums:
    """Verify enum definitions match spec."""

    def test_block_type_values(self) -> None:
        expected = {
            "heading", "paragraph", "code", "table",
            "list", "blockquote", "thematic_break",
        }
        actual = {bt.value for bt in BlockType}
        assert actual == expected

    def test_content_type_values(self) -> None:
        expected = {"text", "code", "mermaid", "table", "mixed"}
        actual = {ct.value for ct in ContentType}
        assert actual == expected
