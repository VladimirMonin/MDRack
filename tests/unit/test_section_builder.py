"""Tests for src/mdrack/markdown/section_builder.py."""

from __future__ import annotations

from mdrack.markdown.ir import BlockType, MarkdownBlock
from mdrack.markdown.section_builder import build_sections


def _heading(content: str, start: int) -> MarkdownBlock:
    return MarkdownBlock(type=BlockType.HEADING, content=content, start_line=start, end_line=start)


def _para(content: str, start: int, end: int) -> MarkdownBlock:
    return MarkdownBlock(type=BlockType.PARAGRAPH, content=content, start_line=start, end_line=end)


class TestSyntheticSections:
    def test_no_headings_uses_filename(self) -> None:
        sections = build_sections([_para("Just text", 1, 2)], "/path/to/readme.md")
        assert len(sections) == 1
        assert sections[0].title == "readme"
        assert sections[0].start_line == 1
        assert sections[0].end_line == 2

    def test_h1_only_section_keeps_body_to_eof(self) -> None:
        blocks = [
            _heading("# Title", 1),
            _para("Body line one.", 2, 3),
            _para("Body line two.", 5, 6),
        ]
        sections = build_sections(blocks, "doc.md")
        assert len(sections) == 1
        assert sections[0].title == "Title"
        assert sections[0].start_line == 1
        assert sections[0].end_line == 6

    def test_preamble_before_first_h2_gets_synthetic_section(self) -> None:
        blocks = [
            _heading("# Guide", 1),
            _para("Intro paragraph.", 2, 3),
            _heading("## Main", 5),
            _para("Main body.", 6, 7),
        ]
        sections = build_sections(blocks, "guide.md")
        assert len(sections) == 2
        assert sections[0].title == "Guide"
        assert sections[0].start_line == 1
        assert sections[0].end_line == 4
        assert sections[1].title == "Main"
        assert sections[1].start_line == 5
        assert sections[1].end_line == 7


class TestHeadingTree:
    def test_nested_heading_paths_are_preserved(self) -> None:
        blocks = [
            _heading("## Parent", 1),
            _heading("### Child", 3),
            _heading("#### Leaf", 5),
            _heading("### Sibling", 7),
        ]
        sections = build_sections(blocks, "doc.md")
        assert [section.heading_path for section in sections] == [
            ["Parent"],
            ["Parent", "Child"],
            ["Parent", "Child", "Leaf"],
            ["Parent", "Sibling"],
        ]
        assert sections[1].parent_id == sections[0].id
        assert sections[2].parent_id == sections[1].id
        assert sections[3].parent_id == sections[0].id

    def test_section_ranges_remain_stable_for_nested_content(self) -> None:
        blocks = [
            _heading("## Parent", 1),
            _para("Parent intro.", 2, 2),
            _heading("### Child", 4),
            _para("Child body.", 5, 6),
            _heading("## Sibling", 8),
        ]
        sections = build_sections(blocks, "doc.md")
        assert [section.title for section in sections] == ["Parent", "Child", "Sibling"]
        assert sections[0].end_line == 7
        assert sections[1].end_line == 7
        assert sections[2].end_line == 8


class TestOrdering:
    def test_sections_are_returned_in_start_line_order(self) -> None:
        blocks = [
            _heading("### Zebra", 10),
            _heading("## Alpha", 1),
            _heading("### Beta", 5),
        ]
        sections = build_sections(blocks, "doc.md")
        assert [section.title for section in sections] == ["Alpha", "Beta", "Zebra"]
        assert [section.start_line for section in sections] == [1, 5, 10]
