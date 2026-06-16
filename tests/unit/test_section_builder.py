"""Tests for src/mdrack/markdown/section_builder.py."""

from __future__ import annotations

from mdrack.markdown.ir import BlockType, MarkdownBlock
from mdrack.markdown.section_builder import build_sections

# ── helpers ──────────────────────────────────────────────────────────────────


def _heading(content: str, start: int) -> MarkdownBlock:
    """Shortcut for a heading block."""
    return MarkdownBlock(type=BlockType.HEADING, content=content, start_line=start, end_line=start)


def _para(content: str, start: int, end: int) -> MarkdownBlock:
    """Shortcut for a paragraph block."""
    return MarkdownBlock(type=BlockType.PARAGRAPH, content=content, start_line=start, end_line=end)


# ── tests ────────────────────────────────────────────────────────────────────


class TestSimpleH2:
    """Simple H2 sections."""

    def test_two_h2_sections(self) -> None:
        blocks = [
            _heading("## Intro", 2),
            _para("hello", 3, 3),
            _heading("## Conclusion", 6),
            _para("bye", 7, 7),
        ]
        secs = build_sections(blocks, "/docs/guide.md")
        assert len(secs) == 2
        assert secs[0].title == "Intro"
        assert secs[0].level == 2
        assert secs[0].start_line == 2
        assert secs[0].end_line == 5
        assert secs[0].heading_path == ["Intro"]
        assert secs[1].title == "Conclusion"
        assert secs[1].level == 2
        assert secs[1].start_line == 6
        assert secs[1].end_line == 7
        assert secs[1].heading_path == ["Conclusion"]

    def test_single_h2(self) -> None:
        blocks = [_heading("## Only", 1), _para("text", 2, 3)]
        secs = build_sections(blocks, "doc.md")
        assert len(secs) == 1
        assert secs[0].title == "Only"
        assert secs[0].end_line == 3


class TestNestedH2H3H4:
    """Nested section hierarchy."""

    def test_full_nesting(self) -> None:
        blocks = [
            _heading("## Getting Started", 1),
            _heading("### Installation", 4),
            _heading("#### Windows", 6),
            _heading("#### Linux", 9),
            _heading("### Usage", 12),
        ]
        secs = build_sections(blocks, "doc.md")
        assert len(secs) == 5

        # Getting Started
        assert secs[0].title == "Getting Started"
        assert secs[0].level == 2
        assert secs[0].parent_id is None
        assert secs[0].heading_path == ["Getting Started"]

        # Installation
        assert secs[1].title == "Installation"
        assert secs[1].level == 3
        assert secs[1].parent_id == secs[0].id
        assert secs[1].heading_path == ["Getting Started", "Installation"]

        # Windows
        assert secs[2].title == "Windows"
        assert secs[2].level == 4
        assert secs[2].parent_id == secs[1].id
        assert secs[2].heading_path == ["Getting Started", "Installation", "Windows"]

        # Linux
        assert secs[3].title == "Linux"
        assert secs[3].level == 4
        assert secs[3].parent_id == secs[1].id
        assert secs[3].heading_path == ["Getting Started", "Installation", "Linux"]

        # Usage
        assert secs[4].title == "Usage"
        assert secs[4].level == 3
        assert secs[4].parent_id == secs[0].id
        assert secs[4].heading_path == ["Getting Started", "Usage"]


class TestNoHeadings:
    """Files without any headings get a synthetic section."""

    def test_no_headings_uses_filename(self) -> None:
        blocks = [_para("Just text", 1, 2)]
        secs = build_sections(blocks, "/path/to/readme.md")
        assert len(secs) == 1
        assert secs[0].title == "readme"
        assert secs[0].level == 2
        assert secs[0].heading_path == ["readme"]
        assert secs[0].document_id == "/path/to/readme.md"
        assert secs[0].start_line == 1
        assert secs[0].end_line == 2

    def test_no_headings_no_blocks(self) -> None:
        secs = build_sections([], "empty.md")
        assert len(secs) == 1
        assert secs[0].title == "empty"
        assert secs[0].start_line == 1
        assert secs[0].end_line == 1

    def test_no_headings_non_path_id(self) -> None:
        secs = build_sections([], "abc-123")
        assert secs[0].title == "abc-123"


class TestHeadingPath:
    """heading_path is computed from the parent chain."""

    def test_three_level_path(self) -> None:
        blocks = [
            _heading("## Ch", 1),
            _heading("### L2", 3),
            _heading("#### L3", 5),
        ]
        secs = build_sections(blocks, "doc.md")
        assert secs[0].heading_path == ["Ch"]
        assert secs[1].heading_path == ["Ch", "L2"]
        assert secs[2].heading_path == ["Ch", "L2", "L3"]

    def test_two_independent_paths(self) -> None:
        blocks = [
            _heading("## A", 1),
            _heading("## B", 3),
        ]
        secs = build_sections(blocks, "doc.md")
        assert secs[0].heading_path == ["A"]
        assert secs[1].heading_path == ["B"]


class TestParentIdLinkage:
    """parent_id correctly links child → parent."""

    def test_siblings_share_parent(self) -> None:
        blocks = [
            _heading("## Parent", 1),
            _heading("### Child1", 3),
            _heading("### Child2", 5),
            _heading("## Other", 7),
        ]
        secs = build_sections(blocks, "doc.md")
        assert secs[0].parent_id is None          # Parent
        assert secs[1].parent_id == secs[0].id    # Child1
        assert secs[2].parent_id == secs[0].id    # Child2
        assert secs[3].parent_id is None           # Other

    def test_deep_chain(self) -> None:
        blocks = [
            _heading("## A", 1),
            _heading("### B", 3),
            _heading("#### C", 5),
        ]
        secs = build_sections(blocks, "doc.md")
        assert secs[0].parent_id is None
        assert secs[1].parent_id == secs[0].id
        assert secs[2].parent_id == secs[1].id


class TestStableOrdering:
    """Sections are returned in start_line order regardless of heading level."""

    def test_out_of_order_levels(self) -> None:
        blocks = [
            _heading("### Zebra", 10),
            _heading("## Alpha", 1),
            _heading("### Beta", 5),
        ]
        secs = build_sections(blocks, "doc.md")
        assert [s.title for s in secs] == ["Alpha", "Beta", "Zebra"]
        assert [s.start_line for s in secs] == [1, 5, 10]


class TestH1IsTitle:
    """H1 headings are never turned into sections."""

    def test_h1_ignored(self) -> None:
        blocks = [
            _heading("# Document Title", 1),
            _heading("## Section", 3),
        ]
        secs = build_sections(blocks, "doc.md")
        assert len(secs) == 1
        assert secs[0].title == "Section"
        assert secs[0].level == 2

    def test_only_h1_gets_synthetic(self) -> None:
        blocks = [_heading("# Title", 1)]
        secs = build_sections(blocks, "doc.md")
        assert len(secs) == 1
        assert secs[0].title == "Title"
        assert secs[0].level == 2
        assert secs[0].heading_path == ["Title"]

    def test_h1_with_h2_sections(self) -> None:
        blocks = [
            _heading("# Doc", 1),
            _para("intro", 2, 2),
            _heading("## Sec A", 4),
            _heading("## Sec B", 6),
        ]
        secs = build_sections(blocks, "doc.md")
        assert len(secs) == 2
        assert secs[0].title == "Sec A"
        assert secs[1].title == "Sec B"


class TestDeepNesting:
    """Deep nesting up to H4."""

    def test_h2_h3_h4_chain(self) -> None:
        blocks = [
            _heading("## L2", 1),
            _heading("### L3", 3),
            _heading("#### L4", 5),
            _heading("### L3b", 7),
        ]
        secs = build_sections(blocks, "doc.md")
        assert len(secs) == 4
        assert secs[0].level == 2
        assert secs[1].level == 3
        assert secs[2].level == 4
        assert secs[3].level == 3
        assert secs[2].parent_id == secs[1].id
        assert secs[3].parent_id == secs[0].id

    def test_level_jump_skips_intermediate(self) -> None:
        """H2 followed directly by H4: H4 becomes child of H2."""
        blocks = [
            _heading("## A", 1),
            _heading("#### B", 3),
        ]
        secs = build_sections(blocks, "doc.md")
        assert len(secs) == 2
        assert secs[0].level == 2
        assert secs[1].level == 4
        assert secs[1].parent_id == secs[0].id


class TestEndLineCalculation:
    """end_line is set to one line before the next same-or-higher section."""

    def test_end_line_before_next_section(self) -> None:
        blocks = [
            _heading("## A", 2),
            _para("content", 3, 4),
            _heading("## B", 6),
            _para("content", 7, 8),
        ]
        secs = build_sections(blocks, "doc.md")
        assert secs[0].end_line == 5
        assert secs[1].end_line == 8

    def test_end_line_at_eof(self) -> None:
        blocks = [
            _heading("## Only", 1),
            _para("content", 2, 5),
        ]
        secs = build_sections(blocks, "doc.md")
        assert secs[0].end_line == 5

    def test_end_line_nesting(self) -> None:
        blocks = [
            _heading("## Parent", 1),
            _heading("### Child", 3),
            _para("content", 4, 5),
            _heading("## Sibling", 7),
        ]
        secs = build_sections(blocks, "doc.md")
        # Parent ends at 6 (line before Sibling)
        assert secs[0].end_line == 6
        # Child ends at 6 (line before Sibling, which is ≤ level)
        assert secs[1].end_line == 6
        # Sibling ends at EOF (last block is its heading at line 7)
        assert secs[2].end_line == 7


class TestEdgeCases:
    """Various edge cases."""

    def test_consecutive_h2_no_content(self) -> None:
        blocks = [
            _heading("## A", 1),
            _heading("## B", 2),
            _heading("## C", 3),
        ]
        secs = build_sections(blocks, "doc.md")
        assert len(secs) == 3
        assert secs[0].end_line == 1
        assert secs[1].end_line == 2
        assert secs[2].end_line == 3

    def test_mixed_heading_and_other_blocks(self) -> None:
        blocks = [
            _para("preamble", 1, 1),
            _heading("## Sec", 3),
            _para("body", 4, 4),
            _para("more", 5, 6),
        ]
        secs = build_sections(blocks, "doc.md")
        assert len(secs) == 1
        assert secs[0].title == "Sec"
        assert secs[0].start_line == 3
        assert secs[0].end_line == 6
