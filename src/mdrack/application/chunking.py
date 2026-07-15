"""Structure-aware conversion from source blocks to retrieval chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass

from mdrack.domain.blocks import BlockType, SourceBlock, SourceSpan
from mdrack.domain.chunks import RetrievalChunk, RetrievalContentType
from mdrack.domain.documents import Document
from mdrack.domain.identifiers import content_fingerprint, logical_id

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class StructuralChunkingConfig:
    target_chars: int = 3200
    hard_limit_chars: int = 8000
    max_tokens: int = 2000
    overlap_chars: int = 300
    code_window_lines: int = 80
    table_rows_per_chunk: int = 40
    mermaid_window_lines: int = 80

    def __post_init__(self) -> None:
        positive = (
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


def estimate_tokens(text: str) -> int:
    """Conservative dependency-free token estimate based on UTF-8 bytes."""
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


class StructuralChunker:
    """Build bounded retrieval representations without mutating source blocks."""

    name = "structural_blocks"
    version = "1"

    def __init__(self, config: StructuralChunkingConfig | None = None) -> None:
        self.config = config or StructuralChunkingConfig()

    def build(self, document: Document) -> tuple[RetrievalChunk, ...]:
        drafts: list[tuple[SourceBlock, str, RetrievalContentType, SourceSpan]] = []
        for block in document.blocks:
            if block.block_type in {BlockType.FRONTMATTER, BlockType.HEADING, BlockType.THEMATIC_BREAK}:
                continue
            drafts.extend(self._block_drafts(block))

        chunks: list[RetrievalChunk] = []
        for index, (block, display, content_type, span) in enumerate(drafts):
            embedding = self._embedding_text(block, display, content_type)
            estimated_tokens = estimate_tokens(embedding)
            if len(display) > self.config.hard_limit_chars:
                raise ValueError("chunk display content exceeds hard character limit")
            if estimated_tokens > self.config.max_tokens:
                raise ValueError("chunk embedding text exceeds estimated token limit")
            chunk_id = logical_id(
                "chunk",
                document.document_id,
                block.block_id,
                index,
                content_fingerprint(display),
                self.version,
            )
            chunks.append(
                RetrievalChunk(
                    chunk_id=chunk_id,
                    document_id=document.document_id,
                    parent_block_ids=(block.block_id,),
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
    ) -> list[tuple[SourceBlock, str, RetrievalContentType, SourceSpan]]:
        if block.block_type == BlockType.CODE:
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
            pieces = self._split_prose(searchable, max_chars, max_bytes)
            return [
                (block, piece, RetrievalContentType.IMAGE_REFERENCE, block.source_span)
                for piece in pieces
                if piece.strip()
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
        pieces = self._split_prose(display, max_chars, max_bytes)
        return [(block, piece, content_type, block.source_span) for piece in pieces if piece.strip()]

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
        text = text.strip()
        if not text:
            return []
        if self._within_budget(text, max_chars, max_bytes):
            return [text]

        sentences = _SENTENCE_BOUNDARY.split(text)
        units = sentences if len(sentences) > 1 else text.split()
        separator = " "
        pieces: list[str] = []
        current = ""
        for unit in units:
            candidate = f"{current}{separator}{unit}".strip() if current else unit
            if current and not self._within_budget(candidate, max_chars, max_bytes):
                pieces.append(current)
                overlap = self._overlap(current, max_chars)
                current = f"{overlap} {unit}".strip() if overlap else unit
            else:
                current = candidate
            while current and not self._within_budget(current, max_chars, max_bytes):
                head, current = self._hard_split(current, max_chars, max_bytes)
                pieces.append(head)
        if current:
            pieces.append(current)
        return pieces

    def _overlap(self, text: str, max_chars: int) -> str:
        if self.config.overlap_chars <= 0:
            return ""
        overlap = text[-min(self.config.overlap_chars, max_chars // 3) :]
        first_space = overlap.find(" ")
        return overlap[first_space + 1 :] if first_space >= 0 else overlap

    @staticmethod
    def _within_budget(text: str, max_chars: int, max_bytes: int) -> bool:
        return len(text) <= max_chars and len(text.encode("utf-8")) <= max_bytes

    @staticmethod
    def _hard_split(text: str, max_chars: int, max_bytes: int) -> tuple[str, str]:
        end = min(len(text), max_chars)
        while end > 0 and len(text[:end].encode("utf-8")) > max_bytes:
            end -= 1
        if end == 0:
            raise ValueError("chunk limits cannot represent one Unicode character")
        boundary = text.rfind(" ", 0, end + 1)
        if boundary > 0:
            end = boundary
        return text[:end].strip(), text[end:].strip()

    def _line_drafts(
        self,
        block: SourceBlock,
        content_type: RetrievalContentType,
        window_lines: int,
    ) -> list[tuple[SourceBlock, str, RetrievalContentType, SourceSpan]]:
        content = (block.plain_text or "").strip("\n")
        lines = content.splitlines()
        if not lines:
            return []
        max_chars, max_bytes = self._body_limits(block, content_type)
        groups: list[tuple[int, list[str]]] = []
        current: list[str] = []
        group_start = 0
        for line_index, line in enumerate(lines):
            candidate = "\n".join([*current, line])
            if current and (
                len(current) >= window_lines
                or not self._within_budget(candidate, max_chars, max_bytes)
            ):
                groups.append((group_start, current))
                current = []
                group_start = line_index
            if not self._within_budget(line, max_chars, max_bytes):
                if current:
                    groups.append((group_start, current))
                    current = []
                if content_type == RetrievalContentType.MERMAID:
                    marker = self._bounded_marker("mermaid line", line, max_chars, max_bytes)
                    groups.append((line_index, [marker]))
                else:
                    for piece in self._split_prose(line, max_chars, max_bytes):
                        groups.append((line_index, [piece]))
                group_start = line_index + 1
            else:
                current.append(line)
        if current:
            groups.append((group_start, current))

        content_start_line = block.source_span.start_line + 1
        if not block.raw_markdown.lstrip().startswith(("```", "~~~")):
            content_start_line = block.source_span.start_line
        return [
            (
                block,
                "\n".join(group),
                content_type,
                SourceSpan(
                    start_line=content_start_line + start,
                    end_line=content_start_line + start + len(group) - 1,
                ),
            )
            for start, group in groups
        ]

    def _table_drafts(self, block: SourceBlock) -> list[tuple[SourceBlock, str, RetrievalContentType, SourceSpan]]:
        lines = block.raw_markdown.splitlines()
        max_chars, max_bytes = self._body_limits(block, RetrievalContentType.TABLE)
        if len(lines) <= 2:
            if self._within_budget(block.raw_markdown, max_chars, max_bytes):
                return [(block, block.raw_markdown, RetrievalContentType.TABLE, block.source_span)]
            marker = self._bounded_marker("table", block.raw_markdown, max_chars, max_bytes)
            return [(block, marker, RetrievalContentType.TABLE, block.source_span)]

        header = lines[:2]
        rows = lines[2:]
        chunks: list[tuple[SourceBlock, str, RetrievalContentType, SourceSpan]] = []
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
                    (
                        block,
                        self._bounded_marker("table row", row, max_chars, max_bytes),
                        RetrievalContentType.TABLE,
                        SourceSpan(
                            start_line=block.source_span.start_line + 2 + start,
                            end_line=block.source_span.start_line + 2 + start,
                        ),
                    )
                )
                start += 1
                continue
            display = "\n".join([*header, *selected])
            chunks.append(
                (
                    block,
                    display,
                    RetrievalContentType.TABLE,
                    SourceSpan(
                        start_line=block.source_span.start_line,
                        end_line=min(
                            block.source_span.end_line,
                            block.source_span.start_line + 1 + start + len(selected),
                        ),
                    ),
                )
            )
            start += len(selected)
        return chunks

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
