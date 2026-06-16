"""Build section hierarchy from parsed Markdown blocks.

Sections are derived from H2–H4 heading blocks.  H1 is treated as a
document title and never becomes a section.  Files with no headings
get a single synthetic section using the filename as title.
"""

from __future__ import annotations

from pathlib import Path

from mdrack.markdown.ir import MarkdownBlock, SectionNode


def _parse_heading_level(content: str) -> tuple[int, str]:
    """Extract heading level and stripped text from a heading block's content.

    ``"# Hello"`` → ``(1, "Hello")``
    ``"## Sub"`` → ``(2, "Sub")``
    """
    stripped = content.strip()
    level = 0
    for char in stripped:
        if char == "#":
            level += 1
        else:
            break
    text = stripped[level:].strip()
    return level, text


def _title_from_file_id(file_id: str) -> str:
    """Derive a human-readable title from a file identifier.

    If *file_id* looks like a file path the stem is returned, otherwise
    the raw identifier is used.
    """
    path = Path(file_id)
    if path.suffix:
        return path.stem
    return file_id


def build_sections(
    blocks: list[MarkdownBlock],
    file_id: str,
) -> list[SectionNode]:
    """Build a section hierarchy from parsed Markdown blocks.

    Parameters
    ----------
    blocks:
        Flat list of ``MarkdownBlock`` instances produced by the parser.
    file_id:
        Unique document identifier (typically the absolute file path).

    Returns
    -------
    list[SectionNode]
        Ordered list of sections.  Every section has its ``heading_path``
        and ``parent_id`` populated.
    """
    heading_blocks = [b for b in blocks if b.type.value == "heading"]

    headings: list[tuple[MarkdownBlock, int, str]] = []
    for b in heading_blocks:
        level, text = _parse_heading_level(b.content)
        headings.append((b, level, text))

    h1_headings = [(b, lv, t) for b, lv, t in headings if lv == 1]
    section_headings = [(b, lv, t) for b, lv, t in headings if 2 <= lv <= 4]

    # ── no headings at all → synthetic section ────────────────────────
    if not headings:
        title = _title_from_file_id(file_id)
        end = max((b.end_line for b in blocks), default=1)
        return [
            SectionNode(
                document_id=file_id,
                title=title,
                heading_path=[title],
                level=2,
                start_line=1,
                end_line=end,
            )
        ]

    # ── only H1, no H2-H4 → synthetic section with H1 title ─────────
    if not section_headings:
        h1_text = h1_headings[0][2] if h1_headings else file_id
        h1_block = h1_headings[0][0] if h1_headings else None
        return [
            SectionNode(
                document_id=file_id,
                title=h1_text,
                heading_path=[h1_text],
                level=2,
                start_line=h1_block.start_line if h1_block else 1,
                end_line=h1_block.end_line if h1_block else 1,
            )
        ]

    # ── normal H2-H4 section tree ────────────────────────────────────
    sections: list[SectionNode] = []
    stack: list[SectionNode] = []

    for block, level, text in section_headings:
        while stack and stack[-1].level >= level:
            stack.pop()

        parent_id = stack[-1].id if stack else None

        section = SectionNode(
            document_id=file_id,
            title=text,
            level=level,
            start_line=block.start_line,
            parent_id=parent_id,
        )
        sections.append(section)
        stack.append(section)

    # ── compute heading_path via parent chain ─────────────────────────
    section_map = {s.id: s for s in sections}
    for section in sections:
        path: list[str] = []
        current: SectionNode | None = section
        while current is not None:
            path.append(current.title)
            current = section_map.get(current.parent_id) if current.parent_id else None
        section.heading_path = list(reversed(path))

    # ── sort by start_line for stable ordering ────────────────────────
    sections.sort(key=lambda s: s.start_line)

    # ── compute end_line (one line before next same-or-higher section, or EOF) ─
    doc_end = max((b.end_line for b in blocks), default=1)
    for i, section in enumerate(sections):
        next_end: int | None = None
        for j in range(i + 1, len(sections)):
            if sections[j].level <= section.level:
                next_end = sections[j].start_line - 1
                break
        section.end_line = next_end if next_end is not None else doc_end

    return sections
