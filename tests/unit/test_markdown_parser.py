"""Tests for src/mdrack/markdown/parser.py and frontmatter.py."""

from __future__ import annotations

import hashlib
from pathlib import Path

from mdrack.markdown.frontmatter import parse_frontmatter
from mdrack.markdown.ir import BlockType, ParsedDocument
from mdrack.markdown.parser import parse_markdown

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "markdown"


# ── Frontmatter ──────────────────────────────────────────────────────


class TestParseFrontmatter:
    def test_no_frontmatter(self) -> None:
        meta, rest = parse_frontmatter("# Hello\nBody")
        assert meta == {}
        assert rest == "# Hello\nBody"

    def test_simple_frontmatter(self) -> None:
        raw = "---\ntitle: Test\n---\nBody"
        meta, rest = parse_frontmatter(raw)
        assert meta["title"] == "Test"
        assert rest.strip() == "Body"

    def test_quoted_values(self) -> None:
        raw = '---\ntitle: "My Title"\n---\nBody'
        meta, _ = parse_frontmatter(raw)
        assert meta["title"] == "My Title"

    def test_multiple_keys(self) -> None:
        raw = "---\ntitle: Doc\nauthor: Alice\ndate: 2025-01-01\n---\nBody"
        meta, _ = parse_frontmatter(raw)
        assert len(meta) == 3
        assert meta["author"] == "Alice"

    def test_empty_frontmatter(self) -> None:
        raw = "---\n---\nBody"
        meta, rest = parse_frontmatter(raw)
        assert meta == {}
        assert rest.strip() == "Body"

    def test_unclosed_frontmatter_returns_empty(self) -> None:
        raw = "---\ntitle: Broken\nBody"
        meta, rest = parse_frontmatter(raw)
        assert meta == {}
        assert rest == raw


# ── Headings ─────────────────────────────────────────────────────────


class TestHeadings:
    def test_h1(self) -> None:
        doc = parse_markdown(Path("/tmp/x.md"), "# Title")
        assert len(doc.blocks) == 1
        b = doc.blocks[0]
        assert b.type == BlockType.HEADING
        assert b.content == "# Title"
        assert b.start_line == 1
        assert b.end_line == 1

    def test_h2(self) -> None:
        doc = parse_markdown(Path("/tmp/x.md"), "## Sub")
        assert doc.blocks[0].type == BlockType.HEADING
        assert doc.blocks[0].content == "## Sub"

    def test_h3(self) -> None:
        doc = parse_markdown(Path("/tmp/x.md"), "### Third")
        assert doc.blocks[0].type == BlockType.HEADING

    def test_h4(self) -> None:
        doc = parse_markdown(Path("/tmp/x.md"), "#### Fourth")
        assert doc.blocks[0].type == BlockType.HEADING

    def test_h5_not_heading(self) -> None:
        doc = parse_markdown(Path("/tmp/x.md"), "##### Five")
        assert doc.blocks[0].type == BlockType.PARAGRAPH

    def test_simple_headings_fixture(self) -> None:
        doc = parse_markdown(FIXTURES / "simple_headings.md")
        headings = [b for b in doc.blocks if b.type == BlockType.HEADING]
        assert len(headings) == 4
        assert headings[0].content == "# Title"
        assert headings[1].content == "## Subtitle"
        assert headings[2].content == "### Third level"
        assert headings[3].content == "#### Fourth level"


# ── Code fences ──────────────────────────────────────────────────────


class TestCodeFences:
    def test_python_code_block(self) -> None:
        content = "```python\nprint('hi')\n```"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        assert len(doc.blocks) == 1
        b = doc.blocks[0]
        assert b.type == BlockType.CODE
        assert b.language == "python"
        assert "print('hi')" in b.content
        assert b.start_line == 1
        assert b.end_line == 3

    def test_javascript_code_block(self) -> None:
        content = "```javascript\nconst x = 1;\n```"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        assert doc.blocks[0].language == "javascript"

    def test_language_detection(self) -> None:
        content = "```rust\nfn main() {}\n```"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        assert doc.blocks[0].language == "rust"

    def test_no_language(self) -> None:
        content = "```\nsome code\n```"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        assert doc.blocks[0].language == "text"

    def test_code_blocks_fixture(self) -> None:
        doc = parse_markdown(FIXTURES / "code_blocks.md")
        code_blocks = [b for b in doc.blocks if b.type == BlockType.CODE]
        assert len(code_blocks) == 4
        assert code_blocks[0].language == "python"
        assert code_blocks[1].language == "javascript"
        assert code_blocks[2].language == "mermaid"
        assert code_blocks[3].language == "text"


# ── Mermaid ──────────────────────────────────────────────────────────


class TestMermaid:
    def test_mermaid_detected(self) -> None:
        content = "```mermaid\ngraph LR\n    A-->B\n```"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        b = doc.blocks[0]
        assert b.type == BlockType.CODE
        assert b.language == "mermaid"
        assert "graph LR" in b.content


# ── Tables ───────────────────────────────────────────────────────────


class TestTable:
    def test_simple_table(self) -> None:
        content = "| A | B |\n|---|---|\n| 1 | 2 |"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        assert len(doc.blocks) == 1
        b = doc.blocks[0]
        assert b.type == BlockType.TABLE
        assert b.start_line == 1
        assert b.end_line == 3

    def test_table_with_surrounding_text(self) -> None:
        content = "Before\n| A |\n|---|\n| 1 |\nAfter"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        types = [b.type for b in doc.blocks]
        assert BlockType.PARAGRAPH in types
        assert BlockType.TABLE in types


# ── Lists ────────────────────────────────────────────────────────────


class TestList:
    def test_unordered_list(self) -> None:
        content = "- item 1\n- item 2\n- item 3"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        assert len(doc.blocks) == 1
        assert doc.blocks[0].type == BlockType.LIST
        assert "item 1" in doc.blocks[0].content

    def test_ordered_list(self) -> None:
        content = "1. first\n2. second"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        assert doc.blocks[0].type == BlockType.LIST

    def test_asterisk_list(self) -> None:
        content = "* alpha\n* beta"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        assert doc.blocks[0].type == BlockType.LIST

    def test_list_with_continuation(self) -> None:
        content = "- item one\n  continuation\n- item two"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        assert doc.blocks[0].type == BlockType.LIST
        assert "continuation" in doc.blocks[0].content


# ── Blockquotes ──────────────────────────────────────────────────────


class TestBlockquote:
    def test_single_blockquote(self) -> None:
        content = "> quoted text"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        assert doc.blocks[0].type == BlockType.BLOCKQUOTE

    def test_multi_line_blockquote(self) -> None:
        content = "> line 1\n> line 2\n> line 3"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        assert doc.blocks[0].type == BlockType.BLOCKQUOTE
        assert "line 1" in doc.blocks[0].content
        assert "line 3" in doc.blocks[0].content
        assert doc.blocks[0].end_line == 3


# ── Thematic breaks ──────────────────────────────────────────────────


class TestThematicBreak:
    def test_dashes(self) -> None:
        doc = parse_markdown(Path("/tmp/x.md"), "---")
        assert doc.blocks[0].type == BlockType.THEMATIC_BREAK

    def test_asterisks(self) -> None:
        doc = parse_markdown(Path("/tmp/x.md"), "***")
        assert doc.blocks[0].type == BlockType.THEMATIC_BREAK

    def test_underscores(self) -> None:
        doc = parse_markdown(Path("/tmp/x.md"), "___")
        assert doc.blocks[0].type == BlockType.THEMATIC_BREAK


# ── Line numbers ─────────────────────────────────────────────────────


class TestLineNumbers:
    def test_single_block_lines(self) -> None:
        doc = parse_markdown(Path("/tmp/x.md"), "# Title")
        assert doc.blocks[0].start_line == 1
        assert doc.blocks[0].end_line == 1

    def test_multi_line_code_block(self) -> None:
        content = "text\n```python\na\nb\n```"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        code = [b for b in doc.blocks if b.type == BlockType.CODE][0]
        assert code.start_line == 2
        assert code.end_line == 5

    def test_table_line_numbers(self) -> None:
        content = "A\n| x |\n|---|\n| 1 |\nB"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        table = [b for b in doc.blocks if b.type == BlockType.TABLE][0]
        assert table.start_line == 2
        assert table.end_line == 4

    def test_heading_after_paragraph(self) -> None:
        content = "Paragraph\n# Heading"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        assert doc.blocks[0].start_line == 1
        assert doc.blocks[0].end_line == 1
        assert doc.blocks[1].start_line == 2
        assert doc.blocks[1].end_line == 2


# ── Source hash ──────────────────────────────────────────────────────


class TestSourceHash:
    def test_hash_computed(self) -> None:
        doc = parse_markdown(Path("/tmp/x.md"), "# Hi")
        expected = hashlib.sha256("# Hi".encode("utf-8")).hexdigest()
        assert doc.source_hash == expected

    def test_hash_differs_for_different_content(self) -> None:
        d1 = parse_markdown(Path("/tmp/x.md"), "aaa")
        d2 = parse_markdown(Path("/tmp/x.md"), "bbb")
        assert d1.source_hash != d2.source_hash


# ── Frontmatter in ParsedDocument ────────────────────────────────────


class TestFrontmatterInDocument:
    def test_frontmatter_parsed(self) -> None:
        content = "---\ntitle: My Doc\n---\nBody"
        doc = parse_markdown(Path("/tmp/x.md"), content)
        assert doc.frontmatter["title"] == "My Doc"
        assert doc.title == "My Doc"

    def test_no_frontmatter(self) -> None:
        doc = parse_markdown(Path("/tmp/x.md"), "# No FM")
        assert doc.frontmatter == {}
        assert doc.title == "No FM"

    def test_frontmatter_fixture(self) -> None:
        doc = parse_markdown(FIXTURES / "frontmatter.md")
        assert doc.frontmatter["title"] == "My Document"
        assert doc.frontmatter["author"] == "Test"
        assert doc.frontmatter["tags"] == "python, markdown"
        assert doc.title == "My Document"


# ── Mixed content fixture ────────────────────────────────────────────


class TestMixedContentFixture:
    def test_all_block_types_present(self) -> None:
        doc = parse_markdown(FIXTURES / "mixed_content.md")
        types = {b.type for b in doc.blocks}
        assert BlockType.HEADING in types
        assert BlockType.PARAGRAPH in types
        assert BlockType.TABLE in types
        assert BlockType.LIST in types
        assert BlockType.BLOCKQUOTE in types
        assert BlockType.THEMATIC_BREAK in types
        assert BlockType.CODE in types

    def test_frontmatter_in_mixed(self) -> None:
        doc = parse_markdown(FIXTURES / "mixed_content.md")
        assert doc.title == "Mixed Content"


# ── Read from disk ───────────────────────────────────────────────────


class TestReadFromDisk:
    def test_read_file(self) -> None:
        doc = parse_markdown(FIXTURES / "simple_headings.md")
        assert isinstance(doc, ParsedDocument)
        assert len(doc.blocks) > 0

    def test_explicit_content_overrides_file(self) -> None:
        doc = parse_markdown(FIXTURES / "simple_headings.md", content="# Override")
        assert len(doc.blocks) == 1
        assert doc.blocks[0].content == "# Override"


# ── Edge cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_content(self) -> None:
        doc = parse_markdown(Path("/tmp/x.md"), "")
        assert doc.blocks == []

    def test_only_blank_lines(self) -> None:
        doc = parse_markdown(Path("/tmp/x.md"), "\n\n\n")
        assert doc.blocks == []

    def test_file_path_is_absolute(self) -> None:
        doc = parse_markdown(Path("/tmp/x.md"), "# Hi")
        assert Path(doc.file_path).is_absolute()
