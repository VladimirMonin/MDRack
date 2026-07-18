"""Markdown parser that produces a list of MarkdownBlock instances.

Uses a line-by-line state machine — no regex-based whole-document splitting.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from mdrack.adapters.markdown_it import MarkdownItParser
from mdrack.markdown.frontmatter import parse_frontmatter
from mdrack.markdown.ir import BlockType, MarkdownBlock, ParsedDocument

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)")
_TABLE_RE = re.compile(r"^\|.*\|")
_LIST_RE = re.compile(r"^\s*[-*]\s|^\s*\d+\.\s")
_BLOCKQUOTE_RE = re.compile(r"^>\s?")
_THEMATIC_RE = re.compile(r"^(\s*[-*_]\s*){3,}$")


def _is_heading(line: str) -> tuple[int, str] | None:
    m = _HEADING_RE.match(line)
    if m:
        return len(m.group(1)), m.group(2).strip()
    return None


def _is_table_line(line: str) -> bool:
    return bool(_TABLE_RE.match(line))


def _is_list_start(line: str) -> bool:
    return bool(_LIST_RE.match(line))


def _is_blockquote(line: str) -> bool:
    return bool(_BLOCKQUOTE_RE.match(line))


def _is_thematic_break(line: str) -> bool:
    return bool(_THEMATIC_RE.match(line.strip()))


def _collect_paragraph(
    lines: list[str],
    start: int,
) -> tuple[str, int]:
    """Collect contiguous non-empty, non-special lines as a paragraph.

    Returns (paragraph_text, end_index) where end_index is the index of
    the last included line.
    """
    collected: list[str] = []
    idx = start
    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()
        if not stripped:
            break
        if _is_heading(line) is not None:
            break
        if stripped.startswith("```"):
            break
        if _is_thematic_break(stripped):
            break
        if _is_table_line(line):
            break
        if _is_blockquote(line):
            break
        if _is_list_start(line):
            break
        collected.append(line)
        idx += 1
    return "\n".join(collected), idx - 1


def _collect_code_fence(lines: list[str], start: int) -> tuple[str, str | None, int]:
    """Collect a fenced code block starting at ``start``.

    The opening line must already be confirmed to start with ```.
    Returns (block_content, language_or_None, end_index).
    """
    opening = lines[start].strip()
    fence_len = len(opening) - len(opening.lstrip("`"))
    language = opening[fence_len:].strip() or "text"

    collected: list[str] = [lines[start]]
    idx = start + 1
    while idx < len(lines):
        collected.append(lines[idx])
        stripped = lines[idx].strip()
        if stripped.startswith("`" * fence_len):
            rest = stripped[fence_len:]
            if not rest or rest.isspace():
                break
        idx += 1

    return "\n".join(collected), language, idx


def _collect_blockquote(lines: list[str], start: int) -> tuple[str, int]:
    """Collect contiguous blockquote lines."""
    collected: list[str] = []
    idx = start
    while idx < len(lines):
        if not _is_blockquote(lines[idx]):
            break
        collected.append(lines[idx])
        idx += 1
    return "\n".join(collected), idx - 1


def _collect_list(lines: list[str], start: int) -> tuple[str, int]:
    """Collect contiguous list item lines."""
    collected: list[str] = []
    idx = start
    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped:
            break
        if _is_heading(lines[idx]) is not None:
            break
        if stripped.startswith("```"):
            break
        if _is_thematic_break(stripped):
            break
        if _is_table_line(lines[idx]):
            break
        if _is_blockquote(lines[idx]):
            break
        if _is_list_start(lines[idx]):
            collected.append(lines[idx])
            idx += 1
            continue
        # continuation of previous list item
        if collected:
            collected.append(lines[idx])
            idx += 1
            continue
        break
    return "\n".join(collected), idx - 1


def _collect_table(lines: list[str], start: int) -> tuple[str, int]:
    """Collect contiguous table lines (|...|)."""
    collected: list[str] = []
    idx = start
    while idx < len(lines):
        if not _is_table_line(lines[idx]):
            break
        collected.append(lines[idx])
        idx += 1
    return "\n".join(collected), idx - 1


def parse_markdown(file_path: Path, content: str | None = None) -> ParsedDocument:
    """Parse a Markdown file into a ParsedDocument.

    Parameters
    ----------
    file_path:
        Absolute path to the Markdown file.
    content:
        Optional pre-loaded file content.  When *None* the file is read
        from disk.

    Returns
    -------
    ParsedDocument
    """
    file_path = file_path.resolve()

    if content is None:
        content = file_path.read_text(encoding="utf-8")

    source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    metadata, body = parse_frontmatter(content)

    title = metadata.get("title", "")
    relative_path = str(file_path)

    # ── fallback: extract title from first H1 if no frontmatter title ──
    if not title:
        for raw_line in body.split("\n"):
            candidate = raw_line.strip()
            h = _is_heading(candidate or raw_line)
            if h is not None and h[0] == 1:
                title = h[1]
                break

    lines = body.split("\n")
    blocks: list[MarkdownBlock] = []
    projection_parser = MarkdownItParser()
    projection_environment, reference_definition_lines = projection_parser.projection_context(body)
    idx = 0

    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()

        # --- blank line ---
        if not stripped:
            idx += 1
            continue

        # Reference definitions feed CommonMark image parsing but are not searchable prose.
        if idx in reference_definition_lines:
            idx += 1
            continue

        # --- heading ---
        heading = _is_heading(line)
        if heading is not None:
            level, text = heading
            blocks.append(
                MarkdownBlock(
                    type=BlockType.HEADING,
                    content=stripped,
                    start_line=idx + 1,
                    end_line=idx + 1,
                    language=None,
                )
            )
            idx += 1
            continue

        # --- code fence ---
        if stripped.startswith("```"):
            block_content, language, end = _collect_code_fence(lines, idx)
            blocks.append(
                MarkdownBlock(
                    type=BlockType.CODE,
                    content=block_content,
                    start_line=idx + 1,
                    end_line=end + 1,
                    language=language,
                )
            )
            idx = end + 1
            continue

        # --- thematic break ---
        if _is_thematic_break(stripped):
            blocks.append(
                MarkdownBlock(
                    type=BlockType.THEMATIC_BREAK,
                    content=stripped,
                    start_line=idx + 1,
                    end_line=idx + 1,
                    language=None,
                )
            )
            idx += 1
            continue

        # --- table ---
        if _is_table_line(line):
            table_content, end = _collect_table(lines, idx)
            blocks.append(
                MarkdownBlock(
                    type=BlockType.TABLE,
                    content=projection_parser.project_text(
                        table_content,
                        environment=projection_environment,
                    ),
                    start_line=idx + 1,
                    end_line=end + 1,
                    language=None,
                )
            )
            idx = end + 1
            continue

        # --- blockquote ---
        if _is_blockquote(line):
            bq_content, end = _collect_blockquote(lines, idx)
            blocks.append(
                MarkdownBlock(
                    type=BlockType.BLOCKQUOTE,
                    content=projection_parser.project_text(
                        bq_content,
                        environment=projection_environment,
                    ),
                    start_line=idx + 1,
                    end_line=end + 1,
                    language=None,
                )
            )
            idx = end + 1
            continue

        # --- list ---
        if _is_list_start(line):
            list_content, end = _collect_list(lines, idx)
            blocks.append(
                MarkdownBlock(
                    type=BlockType.LIST,
                    content=projection_parser.project_text(
                        list_content,
                        environment=projection_environment,
                    ),
                    start_line=idx + 1,
                    end_line=end + 1,
                    language=None,
                )
            )
            idx = end + 1
            continue

        # --- paragraph (fallback) ---
        para_content, end = _collect_paragraph(lines, idx)
        if para_content.strip():
            blocks.append(
                MarkdownBlock(
                    type=BlockType.PARAGRAPH,
                    content=projection_parser.project_text(
                        para_content,
                        environment=projection_environment,
                    ),
                    start_line=idx + 1,
                    end_line=end + 1,
                    language=None,
                )
            )
        idx = end + 1

    return ParsedDocument(
        file_path=str(file_path),
        relative_path=relative_path,
        title=title,
        frontmatter=metadata,
        blocks=blocks,
        source_hash=source_hash,
    )
