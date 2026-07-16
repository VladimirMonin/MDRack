"""Structure-aware conversion from source blocks to retrieval chunks."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from mdrack.domain.blocks import BlockType, SourceBlock, SourceSpan
from mdrack.domain.chunks import RetrievalChunk, RetrievalContentType
from mdrack.domain.documents import Document
from mdrack.domain.identifiers import content_fingerprint, logical_id

_PARAGRAPH_BOUNDARY = re.compile(r"\n[ \t]*\n+")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])\s+")
_WORD_UNIT = re.compile(r"\s*\S+")


@dataclass(frozen=True)
class StructuralChunkingConfig:
    min_chars: int = 1
    target_chars: int = 3200
    hard_limit_chars: int = 8000
    max_tokens: int = 2000
    overlap_chars: int = 300
    code_window_lines: int = 80
    table_rows_per_chunk: int = 40
    mermaid_window_lines: int = 80

    def __post_init__(self) -> None:
        positive = (
            self.min_chars,
            self.target_chars,
            self.hard_limit_chars,
            self.max_tokens,
            self.code_window_lines,
            self.table_rows_per_chunk,
            self.mermaid_window_lines,
        )
        if any(value < 1 for value in positive) or self.overlap_chars < 0:
            raise ValueError("chunking limits must be positive and overlap non-negative")
        if self.target_chars > self.hard_limit_chars:
            raise ValueError("target_chars cannot exceed hard_limit_chars")
        if self.min_chars > self.target_chars:
            raise ValueError("min_chars cannot exceed target_chars")


@dataclass(frozen=True)
class _Draft:
    blocks: tuple[SourceBlock, ...]
    display: str
    content_type: RetrievalContentType
    span: SourceSpan
    mergeable: bool = False


def estimate_tokens(text: str) -> int:
    """Conservative dependency-free token estimate based on UTF-8 bytes."""
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


class StructuralChunker:
    """Build bounded retrieval representations without mutating source blocks."""

    name = "structural_blocks"
    version = "2"

    def __init__(self, config: StructuralChunkingConfig | None = None) -> None:
        self.config = config or StructuralChunkingConfig()

    def build(self, document: Document) -> tuple[RetrievalChunk, ...]:
        stream: list[_Draft | None] = []
        for block in document.blocks:
            if block.block_type in {BlockType.FRONTMATTER, BlockType.HEADING, BlockType.THEMATIC_BREAK}:
                stream.append(None)
                continue
            stream.extend(self._block_drafts(block))
        drafts = self._merge_small_drafts(stream)

        chunks: list[RetrievalChunk] = []
        for index, draft in enumerate(drafts):
            block = draft.blocks[0]
            display = draft.display
            content_type = draft.content_type
            span = draft.span
            embedding = self._embedding_text(block, display, content_type)
            estimated_tokens = estimate_tokens(embedding)
            if len(display) > self.config.hard_limit_chars:
                raise ValueError("chunk display content exceeds hard character limit")
            if estimated_tokens > self.config.max_tokens:
                raise ValueError("chunk embedding text exceeds estimated token limit")
            chunk_id = logical_id(
                "chunk",
                document.document_id,
                *(parent.block_id for parent in draft.blocks),
                index,
                content_fingerprint(display),
                self.version,
            )
            chunks.append(
                RetrievalChunk(
                    chunk_id=chunk_id,
                    document_id=document.document_id,
                    parent_block_ids=tuple(parent.block_id for parent in draft.blocks),
                    display_content=display,
                    embedding_text=embedding,
                    content_type=content_type,
                    chunk_index=index,
                    heading_path=block.heading_path,
                    source_span=span,
                    estimated_tokens=estimated_tokens,
                )
            )
        return tuple(chunks)

    def _block_drafts(
        self,
        block: SourceBlock,
    ) -> list[_Draft]:
        if block.block_type == BlockType.CODE:
            if (block.language or "").casefold() in {"py", "python"}:
                python_drafts = self._python_drafts(block)
                if python_drafts is not None:
                    return python_drafts
            return self._line_drafts(block, RetrievalContentType.CODE, self.config.code_window_lines)
        if block.block_type == BlockType.MERMAID:
            return self._line_drafts(block, RetrievalContentType.MERMAID, self.config.mermaid_window_lines)
        if block.block_type == BlockType.TABLE:
            return self._table_drafts(block)

        if block.block_type == BlockType.IMAGE_REFERENCE:
            alt_text = block.attributes.get("alt_text")
            surrounding_text = block.attributes.get("surrounding_text")
            searchable = "\n".join(
                dict.fromkeys(
                    value.strip()
                    for value in (alt_text, surrounding_text)
                    if isinstance(value, str) and value.strip()
                )
            )
            if not searchable:
                return []
            max_chars, max_bytes = self._body_limits(block, RetrievalContentType.IMAGE_REFERENCE)
            display = self._bounded_searchable_parts(
                [
                    value.strip()
                    for value in (alt_text, surrounding_text)
                    if isinstance(value, str) and value.strip()
                ],
                max_chars,
                max_bytes,
            )
            return [
                _Draft((block,), display, RetrievalContentType.IMAGE_REFERENCE, block.source_span)
            ]

        content_type = {
            BlockType.PARAGRAPH: RetrievalContentType.TEXT,
            BlockType.LIST: RetrievalContentType.LIST,
            BlockType.BLOCKQUOTE: RetrievalContentType.BLOCKQUOTE,
            BlockType.CALLOUT: RetrievalContentType.CALLOUT,
            BlockType.UNKNOWN: RetrievalContentType.UNKNOWN,
        }.get(block.block_type, RetrievalContentType.UNKNOWN)
        display = (block.plain_text if block.block_type == BlockType.PARAGRAPH else block.raw_markdown) or ""
        max_chars, max_bytes = self._body_limits(
            block,
            content_type,
            use_target=block.block_type == BlockType.PARAGRAPH,
        )
        ranged = self._split_prose_ranges(display, max_chars, max_bytes)
        mergeable = block.block_type in {BlockType.PARAGRAPH, BlockType.LIST, BlockType.BLOCKQUOTE}
        return [
            _Draft(
                (block,),
                piece,
                content_type,
                self._span_for_text_slice(block, display, start, end),
                mergeable=mergeable and len(ranged) == 1,
            )
            for piece, start, end in ranged
            if piece.strip()
        ]

    def _body_limits(
        self,
        block: SourceBlock,
        content_type: RetrievalContentType,
        *,
        use_target: bool = False,
    ) -> tuple[int, int]:
        prefix = self._embedding_prefix(block, content_type)
        separator_bytes = 2 if prefix else 0
        token_bytes = self.config.max_tokens * 4 - len(prefix.encode("utf-8")) - separator_bytes
        char_limit = self.config.target_chars if use_target else self.config.hard_limit_chars
        return max(1, min(char_limit, self.config.hard_limit_chars)), max(1, token_bytes)

    def _split_prose(self, text: str, max_chars: int, max_bytes: int) -> list[str]:
        return [piece for piece, _, _ in self._split_prose_ranges(text, max_chars, max_bytes)]

    def _split_prose_ranges(self, text: str, max_chars: int, max_bytes: int) -> list[tuple[str, int, int]]:
        if not text.strip():
            return []
        ranges = self._split_range(text, 0, len(text), max_chars, max_bytes, level=0)
        return [(text[start:end], start, end) for start, end in ranges]

    def _split_range(
        self,
        text: str,
        start: int,
        end: int,
        max_chars: int,
        max_bytes: int,
        *,
        level: int,
    ) -> list[tuple[int, int]]:
        if self._within_budget(text[start:end], max_chars, max_bytes):
            return [(start, end)]
        if level < 3:
            pattern = (_PARAGRAPH_BOUNDARY, _SENTENCE_BOUNDARY, _WORD_UNIT)[level]
            units = self._units(text, start, end, pattern, word_mode=level == 2)
            if len(units) > 1:
                return self._pack_units(text, units, max_chars, max_bytes, next_level=level + 1)
            return self._split_range(text, start, end, max_chars, max_bytes, level=level + 1)

        pieces: list[tuple[int, int]] = []
        cursor = start
        while cursor < end:
            piece_end = min(end, cursor + max_chars)
            while piece_end > cursor and len(text[cursor:piece_end].encode("utf-8")) > max_bytes:
                piece_end -= 1
            if piece_end == cursor:
                raise ValueError("chunk limits cannot represent one Unicode character")
            pieces.append((cursor, piece_end))
            cursor = piece_end
        return pieces

    @staticmethod
    def _units(
        text: str,
        start: int,
        end: int,
        pattern: re.Pattern[str],
        *,
        word_mode: bool,
    ) -> list[tuple[int, int]]:
        if word_mode:
            units = [(match.start(), match.end()) for match in pattern.finditer(text, start, end)]
            if units and units[0][0] > start:
                units[0] = (start, units[0][1])
            if units and units[-1][1] < end:
                units[-1] = (units[-1][0], end)
            return units
        units: list[tuple[int, int]] = []
        cursor = start
        for match in pattern.finditer(text, start, end):
            units.append((cursor, match.end()))
            cursor = match.end()
        units.append((cursor, end))
        return [unit for unit in units if unit[0] < unit[1]]

    def _pack_units(
        self,
        text: str,
        units: list[tuple[int, int]],
        max_chars: int,
        max_bytes: int,
        *,
        next_level: int,
    ) -> list[tuple[int, int]]:
        pieces: list[tuple[int, int]] = []
        current_start: int | None = None
        current_end: int | None = None
        for unit_start, unit_end in units:
            if not self._within_budget(text[unit_start:unit_end], max_chars, max_bytes):
                if current_start is not None and current_end is not None:
                    pieces.append((current_start, current_end))
                    current_start = current_end = None
                pieces.extend(
                    self._split_range(
                        text,
                        unit_start,
                        unit_end,
                        max_chars,
                        max_bytes,
                        level=next_level,
                    )
                )
                continue
            candidate_start = unit_start if current_start is None else current_start
            if current_end is not None and not self._within_budget(
                text[candidate_start:unit_end], max_chars, max_bytes
            ):
                pieces.append((candidate_start, current_end))
                current_start = unit_start
            elif current_start is None:
                current_start = unit_start
            current_end = unit_end
        if current_start is not None and current_end is not None:
            pieces.append((current_start, current_end))
        return pieces

    @staticmethod
    def _within_budget(text: str, max_chars: int, max_bytes: int) -> bool:
        return len(text) <= max_chars and len(text.encode("utf-8")) <= max_bytes

    def _bounded_searchable_parts(
        self,
        parts: list[str],
        max_chars: int,
        max_bytes: int,
    ) -> str:
        unique = list(dict.fromkeys(parts))
        searchable = "\n".join(unique)
        if self._within_budget(searchable, max_chars, max_bytes):
            return searchable

        selected = ["" for _ in unique]
        positions = [0 for _ in unique]
        while True:
            progressed = False
            for index, part in enumerate(unique):
                if positions[index] >= len(part):
                    continue
                candidate_parts = selected.copy()
                candidate_parts[index] += part[positions[index]]
                candidate = "\n".join(value for value in candidate_parts if value)
                if not self._within_budget(candidate, max_chars, max_bytes):
                    continue
                selected = candidate_parts
                positions[index] += 1
                progressed = True
            if not progressed:
                break
        bounded = "\n".join(value for value in selected if value)
        if not bounded:
            raise ValueError("chunk limits cannot represent searchable image text")
        return bounded


    def _line_drafts(
        self,
        block: SourceBlock,
        content_type: RetrievalContentType,
        window_lines: int,
    ) -> list[_Draft]:
        content = (block.plain_text or "").strip("\n")
        lines = content.splitlines()
        if not lines:
            return []
        max_chars, max_bytes = self._body_limits(block, content_type)
        line_starts = self._line_starts(content)
        groups: list[tuple[int, int, str]] = []
        current: list[str] = []
        group_start = 0
        for line_index, line in enumerate(lines):
            candidate = "\n".join([*current, line])
            if current and (
                len(current) >= window_lines
                or not self._within_budget(candidate, max_chars, max_bytes)
            ):
                groups.append(
                    (
                        line_starts[group_start],
                        self._line_content_end(content, line_starts, line_index),
                        "\n".join(current),
                    )
                )
                current = []
                group_start = line_index
            if not self._within_budget(line, max_chars, max_bytes):
                if current:
                    groups.append(
                        (
                            line_starts[group_start],
                            self._line_content_end(content, line_starts, line_index),
                            "\n".join(current),
                        )
                    )
                    current = []
                line_start = line_starts[line_index]
                line_end = self._line_content_end(content, line_starts, line_index + 1)
                groups.extend(
                    (start, end, content[start:end])
                    for start, end in self._split_range(
                        content,
                        line_start,
                        line_end,
                        max_chars,
                        max_bytes,
                        level=3,
                    )
                )
                group_start = line_index + 1
            else:
                current.append(line)
        if current:
            groups.append(
                (
                    line_starts[group_start],
                    self._line_content_end(content, line_starts, len(lines)),
                    "\n".join(current),
                )
            )

        return [
            _Draft(
                (block,),
                display,
                content_type,
                self._span_for_text_slice(block, content, start, end),
            )
            for start, end, display in groups
        ]

    def _python_drafts(self, block: SourceBlock) -> list[_Draft] | None:
        content = (block.plain_text or "").strip("\n")
        if not content:
            return []
        try:
            module = ast.parse(content)
        except SyntaxError:
            return None

        structural = [
            node
            for node in module.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if not structural:
            return self._line_drafts(block, RetrievalContentType.CODE, self.config.code_window_lines)

        lines = content.splitlines()
        structural_ranges: list[tuple[int, int]] = []
        for node in structural:
            decorator_lines = [decorator.lineno for decorator in node.decorator_list]
            start_line = min([node.lineno, *decorator_lines]) - 1
            end_line = node.end_lineno or node.lineno
            structural_ranges.append((start_line, end_line))
        ranges: list[tuple[int, int]] = []
        cursor = 0
        for start_line, end_line in structural_ranges:
            if cursor < start_line:
                ranges.append((cursor, start_line))
            ranges.append((start_line, end_line))
            cursor = end_line
        if cursor < len(lines):
            ranges.append((cursor, len(lines)))
        line_starts = self._line_starts(content)
        max_chars, max_bytes = self._body_limits(block, RetrievalContentType.CODE)
        drafts: list[_Draft] = []
        for start_line, end_line in ranges:
            start = line_starts[start_line]
            end = self._line_content_end(content, line_starts, end_line)
            segment = content[start:end]
            if not segment.strip():
                continue
            if (
                self._within_budget(segment, max_chars, max_bytes)
                and end_line - start_line <= self.config.code_window_lines
            ):
                drafts.append(
                    _Draft(
                        (block,),
                        segment.strip("\n"),
                        RetrievalContentType.CODE,
                        self._span_for_text_slice(block, content, start, end),
                    )
                )
                continue
            drafts.extend(
                self._line_drafts_for_range(
                    block,
                    content,
                    start_line,
                    end_line,
                    max_chars,
                    max_bytes,
                )
            )
        return drafts

    def _line_drafts_for_range(
        self,
        block: SourceBlock,
        content: str,
        start_line: int,
        end_line: int,
        max_chars: int,
        max_bytes: int,
    ) -> list[_Draft]:
        line_starts = self._line_starts(content)
        drafts: list[_Draft] = []
        cursor = start_line
        while cursor < end_line:
            selected_end = cursor
            while selected_end < end_line and selected_end - cursor < self.config.code_window_lines:
                candidate_end = selected_end + 1
                start = line_starts[cursor]
                end = self._line_content_end(content, line_starts, candidate_end)
                if selected_end > cursor and not self._within_budget(content[start:end], max_chars, max_bytes):
                    break
                selected_end = candidate_end
                if not self._within_budget(content[start:end], max_chars, max_bytes):
                    break
            start = line_starts[cursor]
            end = self._line_content_end(content, line_starts, selected_end)
            display = content[start:end]
            if not self._within_budget(display, max_chars, max_bytes):
                selected_end = cursor + 1
                end = self._line_content_end(content, line_starts, selected_end)
                drafts.extend(
                    _Draft(
                        (block,),
                        content[piece_start:piece_end],
                        RetrievalContentType.CODE,
                        self._span_for_text_slice(block, content, piece_start, piece_end),
                    )
                    for piece_start, piece_end in self._split_range(
                        content,
                        start,
                        end,
                        max_chars,
                        max_bytes,
                        level=3,
                    )
                )
                cursor = selected_end
                continue
            drafts.append(
                _Draft(
                    (block,),
                    display.strip("\n"),
                    RetrievalContentType.CODE,
                    self._span_for_text_slice(block, content, start, end),
                )
            )
            cursor = selected_end
        return drafts

    def _table_drafts(self, block: SourceBlock) -> list[_Draft]:
        lines = block.raw_markdown.splitlines()
        line_starts = self._line_starts(block.raw_markdown)
        max_chars, max_bytes = self._body_limits(block, RetrievalContentType.TABLE)
        if len(lines) <= 2:
            if self._within_budget(block.raw_markdown, max_chars, max_bytes):
                return [_Draft((block,), block.raw_markdown, RetrievalContentType.TABLE, block.source_span)]
            marker = self._bounded_marker("table", block.raw_markdown, max_chars, max_bytes)
            return [_Draft((block,), marker, RetrievalContentType.TABLE, block.source_span)]

        header = lines[:2]
        rows = lines[2:]
        chunks: list[_Draft] = []
        start = 0
        while start < len(rows):
            selected: list[str] = []
            while start + len(selected) < len(rows) and len(selected) < self.config.table_rows_per_chunk:
                row = rows[start + len(selected)]
                candidate = "\n".join([*header, *selected, row])
                if not self._within_budget(candidate, max_chars, max_bytes):
                    break
                selected.append(row)
            if not selected:
                row = rows[start]
                chunks.append(
                    _Draft(
                        (block,),
                        self._bounded_marker("table row", row, max_chars, max_bytes),
                        RetrievalContentType.TABLE,
                        self._span_for_text_slice(
                            block,
                            block.raw_markdown,
                            line_starts[2 + start],
                            self._line_content_end(block.raw_markdown, line_starts, 3 + start),
                        ),
                    )
                )
                start += 1
                continue
            display = "\n".join([*header, *selected])
            owned_start_line = 0 if start == 0 else 2 + start
            owned_end_line = 2 + start + len(selected)
            chunks.append(
                _Draft(
                    (block,),
                    display,
                    RetrievalContentType.TABLE,
                    self._span_for_text_slice(
                        block,
                        block.raw_markdown,
                        line_starts[owned_start_line],
                        self._line_content_end(block.raw_markdown, line_starts, owned_end_line),
                    ),
                )
            )
            start += len(selected)
        return chunks

    def _merge_small_drafts(self, stream: list[_Draft | None]) -> list[_Draft]:
        merged: list[_Draft] = []
        pending: _Draft | None = None
        for draft in stream:
            if draft is None or not draft.mergeable:
                if pending is not None:
                    merged.append(pending)
                    pending = None
                if draft is not None:
                    merged.append(draft)
                continue
            if pending is None:
                pending = draft
                continue
            candidate = self._merge_pair(pending, draft)
            if candidate is not None and (
                len(pending.display) < self.config.min_chars
                or len(draft.display) < self.config.min_chars
            ):
                pending = candidate
            else:
                merged.append(pending)
                pending = draft
        if pending is not None:
            merged.append(pending)
        return merged

    def _merge_pair(self, left: _Draft, right: _Draft) -> _Draft | None:
        if (
            left.content_type != right.content_type
            or left.blocks[0].heading_path != right.blocks[0].heading_path
            or left.content_type
            not in {RetrievalContentType.TEXT, RetrievalContentType.LIST, RetrievalContentType.BLOCKQUOTE}
        ):
            return None
        display = f"{left.display}\n\n{right.display}"
        max_chars, max_bytes = self._body_limits(left.blocks[0], left.content_type, use_target=True)
        if not self._within_budget(display, max_chars, max_bytes):
            return None
        return _Draft(
            blocks=(*left.blocks, *right.blocks),
            display=display,
            content_type=left.content_type,
            span=self._union_span(left.span, right.span),
            mergeable=True,
        )

    @staticmethod
    def _union_span(left: SourceSpan, right: SourceSpan) -> SourceSpan:
        if (
            left.start_offset is None
            or left.end_offset is None
            or right.start_offset is None
            or right.end_offset is None
        ):
            return SourceSpan(min(left.start_line, right.start_line), max(left.end_line, right.end_line))
        return SourceSpan(
            min(left.start_line, right.start_line),
            max(left.end_line, right.end_line),
            min(left.start_offset, right.start_offset),
            max(left.end_offset, right.end_offset),
        )

    @staticmethod
    def _line_starts(text: str) -> list[int]:
        starts = [0]
        for line in text.splitlines(keepends=True):
            starts.append(starts[-1] + len(line))
        if starts[-1] < len(text):
            starts.append(len(text))
        return starts

    @staticmethod
    def _line_content_end(text: str, line_starts: list[int], end_line: int) -> int:
        return len(text) if end_line >= len(line_starts) else line_starts[end_line]

    def _span_for_text_slice(
        self,
        block: SourceBlock,
        text: str,
        start: int,
        end: int,
    ) -> SourceSpan:
        raw_start, raw_end = self._text_raw_bounds(block.raw_markdown, text, start, end)
        start_line = block.source_span.start_line + block.raw_markdown.count("\n", 0, raw_start)
        end_probe = max(raw_start, raw_end - 1)
        end_line = block.source_span.start_line + block.raw_markdown.count("\n", 0, end_probe)
        if block.source_span.start_offset is None:
            return SourceSpan(start_line, end_line)
        return SourceSpan(
            start_line,
            end_line,
            block.source_span.start_offset + raw_start,
            block.source_span.start_offset + raw_end,
        )

    @staticmethod
    def _text_raw_bounds(raw: str, text: str, start: int, end: int) -> tuple[int, int]:
        direct = raw.find(text)
        if direct >= 0:
            return direct + start, direct + end
        normalized: list[str] = []
        normalized_to_raw: list[int] = []
        raw_index = 0
        while raw_index < len(raw):
            normalized_to_raw.append(raw_index)
            if raw.startswith("\r\n", raw_index):
                normalized.append("\n")
                raw_index += 2
            else:
                normalized.append(raw[raw_index])
                raw_index += 1
        normalized_to_raw.append(len(raw))
        normalized_raw = "".join(normalized)
        normalized_start = normalized_raw.find(text)
        if normalized_start < 0:
            raise ValueError("chunk text cannot be located in its source block")
        return (
            normalized_to_raw[normalized_start + start],
            normalized_to_raw[normalized_start + end],
        )

    @staticmethod
    def _bounded_marker(kind: str, source: str, max_chars: int, max_bytes: int) -> str:
        digest = content_fingerprint(source)[:12]
        candidates = (
            f"[{kind} omitted; chars={len(source)}; sha256={digest}]",
            f"[{kind} omitted; sha256={digest}]",
            f"[{kind} omitted]",
            "~",
        )
        for candidate in candidates:
            if StructuralChunker._within_budget(candidate, max_chars, max_bytes):
                return candidate
        raise ValueError(f"chunk limits cannot represent bounded {kind} marker")

    def _embedding_prefix(self, block: SourceBlock, content_type: RetrievalContentType) -> str:
        heading = " > ".join(block.heading_path)
        label = ""
        if content_type == RetrievalContentType.CODE:
            label = f"[code:{block.language or 'text'}]"
        elif content_type == RetrievalContentType.MERMAID:
            label = "[mermaid diagram]"
        elif content_type != RetrievalContentType.TEXT:
            label = f"[{content_type.value}]"
        prefix = "\n".join(part for part in (heading, label) if part)
        max_prefix_bytes = self.config.max_tokens * 4 - 6
        if max_prefix_bytes <= 0:
            return ""
        end = len(prefix)
        while end > 0 and len(prefix[:end].encode("utf-8")) > max_prefix_bytes:
            end -= 1
        return prefix[:end].rstrip()

    def _embedding_text(
        self,
        block: SourceBlock,
        display: str,
        content_type: RetrievalContentType,
    ) -> str:
        prefix = self._embedding_prefix(block, content_type)
        return f"{prefix}\n\n{display}" if prefix else display
