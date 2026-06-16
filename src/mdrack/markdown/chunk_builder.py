"""Build final chunks from parsed blocks and section hierarchy.

Chunks are the atomic units that get embedded and stored for retrieval.
This module splits sections into appropriately sized chunks while
keeping structural elements (code, mermaid, tables) intact.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from mdrack.markdown.ir import (
    BlockType,
    ContentType,
    FinalChunk,
    MarkdownBlock,
    SectionNode,
)

_DEFAULT_CONFIG: dict[str, int] = {
    "min_chunk_chars": 600,
    "target_chunk_chars": 1200,
    "hard_limit_chars": 2200,
    "overlap_chars": 180,
}


def _detect_content_type(blocks: list[MarkdownBlock]) -> ContentType:
    """Detect the dominant content type from a list of blocks."""
    if not blocks:
        return ContentType.TEXT
    types = {b.type for b in blocks}
    if types == {BlockType.CODE}:
        return ContentType.CODE
    if types == {BlockType.TABLE}:
        return ContentType.TABLE
    if types == {BlockType.CODE} and any(
        b.language and "mermaid" in b.language.lower() for b in blocks
    ):
        return ContentType.MERMAID
    return ContentType.TEXT


def _is_code_block(block: MarkdownBlock) -> bool:
    """Check if block is a code block (including mermaid)."""
    return block.type == BlockType.CODE


def _is_mermaid_block(block: MarkdownBlock) -> bool:
    """Check if block is a mermaid diagram block."""
    return (
        block.type == BlockType.CODE
        and block.language is not None
        and "mermaid" in block.language.lower()
    )


def _is_table_block(block: MarkdownBlock) -> bool:
    """Check if block is a table block."""
    return block.type == BlockType.TABLE


def _split_text_by_sentences(text: str, max_chars: int) -> list[str]:
    """Split text into pieces respecting sentence boundaries.

    Tries to keep chunks under *max_chars* by splitting on sentence
    boundaries (period+space, exclamation, question mark).
    """
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) <= 1:
        return _split_text_by_words(text, max_chars)

    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + 1 + len(sentence) > max_chars:
            pieces.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip() if current else sentence
    if current.strip():
        pieces.append(current.strip())
    return pieces


def _split_text_by_words(text: str, max_chars: int) -> list[str]:
    """Split text at word boundaries when sentence splitting fails."""
    words = text.split()
    pieces: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > max_chars:
            pieces.append(current.strip())
            current = word
        else:
            current = f"{current} {word}".strip() if current else word
    if current.strip():
        pieces.append(current.strip())
    return pieces


def _get_overlap_text(text: str, overlap_chars: int) -> str:
    """Extract trailing text of *text* up to *overlap_chars* characters.

    Tries to break at a word boundary; falls back to a hard cut.
    """
    if overlap_chars <= 0 or len(text) <= overlap_chars:
        return text
    tail = text[-overlap_chars:]
    space_idx = tail.find(" ")
    if space_idx != -1:
        tail = tail[space_idx + 1 :]
    return tail


@dataclass
class _ChunkAccumulator:
    """Accumulates text pieces for a single chunk under construction."""

    pieces: list[str] = field(default_factory=list)
    total_chars: int = 0

    def add(self, text: str) -> None:
        self.pieces.append(text)
        self.total_chars += len(text)

    def flush(self) -> str:
        result = "\n\n".join(self.pieces)
        self.pieces.clear()
        self.total_chars = 0
        return result

    @property
    def content(self) -> str:
        return "\n\n".join(self.pieces)


def build_chunks(
    blocks: list[MarkdownBlock],
    sections: list[SectionNode],
    file_id: str,
    config: dict | None = None,
) -> list[FinalChunk]:
    """Build final chunks from parsed blocks and section hierarchy.

    Parameters
    ----------
    blocks:
        Flat list of ``MarkdownBlock`` instances from the parser.
    sections:
        Section hierarchy produced by ``build_sections``.
    file_id:
        Document identifier (typically the absolute file path).
    config:
        Optional overrides for chunk sizing parameters.  Defaults:
        ``min_chunk_chars=600``, ``target_chunk_chars=1200``,
        ``hard_limit_chars=2200``, ``overlap_chars=180``.

    Returns
    -------
    list[FinalChunk]
        Ordered list of chunks forming a doubly-linked list via
        ``previous_chunk_id`` / ``next_chunk_id``.
    """
    cfg = {**_DEFAULT_CONFIG, **(config or {})}
    target_chars = cfg["target_chunk_chars"]
    hard_limit = cfg["hard_limit_chars"]
    overlap_chars = cfg["overlap_chars"]

    all_chunks: list[FinalChunk] = []

    # Build a map: section_id -> list of blocks within that section's line range
    section_blocks: dict[str, list[MarkdownBlock]] = {}
    for section in sections:
        section_blocks[section.id] = [
            b
            for b in blocks
            if b.start_line >= section.start_line and b.end_line <= section.end_line
        ]

    # ── process each section ──────────────────────────────────────────
    for section in sections:
        sec_blocks = section_blocks.get(section.id, [])
        if not sec_blocks:
            continue

        section_chunks: list[FinalChunk] = []
        acc = _ChunkAccumulator()
        previous_tail = ""

        for block in sec_blocks:
            # ── code / mermaid blocks: flush accumulator, emit intact ──
            if _is_code_block(block):
                if acc.total_chars > 0:
                    chunk_text = acc.flush()
                    section_chunks.append(
                        _make_text_chunk(
                            chunk_text,
                            file_id=file_id,
                            section=section,
                            chunk_index=len(section_chunks),
                        )
                    )
                    previous_tail = _get_overlap_text(chunk_text, overlap_chars)

                block_text = block.content
                ct = ContentType.MERMAID if _is_mermaid_block(block) else ContentType.CODE
                section_chunks.append(
                    _make_typed_chunk(
                        block_text,
                        content_type=ct,
                        file_id=file_id,
                        section=section,
                        chunk_index=len(section_chunks),
                    )
                )
                previous_tail = ""
                continue

            # ── table blocks: flush accumulator, emit intact ───────────
            if _is_table_block(block):
                if acc.total_chars > 0:
                    chunk_text = acc.flush()
                    section_chunks.append(
                        _make_text_chunk(
                            chunk_text,
                            file_id=file_id,
                            section=section,
                            chunk_index=len(section_chunks),
                        )
                    )
                    previous_tail = _get_overlap_text(chunk_text, overlap_chars)

                section_chunks.append(
                    _make_typed_chunk(
                        block.content,
                        content_type=ContentType.TABLE,
                        file_id=file_id,
                        section=section,
                        chunk_index=len(section_chunks),
                    )
                )
                previous_tail = ""
                continue

            # ── text-like blocks (paragraphs, lists, quotes, breaks) ──
            block_text = block.content

            # Add overlap from previous chunk if accumulator is empty
            if acc.total_chars == 0 and previous_tail:
                block_text = f"{previous_tail} {block_text}"
                previous_tail = ""

            # If block + accumulator exceeds hard limit, flush & split
            if acc.total_chars + len(block_text) > hard_limit:
                if acc.total_chars > 0:
                    chunk_text = acc.flush()
                    section_chunks.append(
                        _make_text_chunk(
                            chunk_text,
                            file_id=file_id,
                            section=section,
                            chunk_index=len(section_chunks),
                        )
                    )
                    previous_tail = _get_overlap_text(chunk_text, overlap_chars)

                # Split the large block itself
                if len(block_text) > hard_limit:
                    pieces = _split_text_by_sentences(block_text, target_chars)
                    for piece in pieces:
                        section_chunks.append(
                            _make_text_chunk(
                                piece,
                                file_id=file_id,
                                section=section,
                                chunk_index=len(section_chunks),
                            )
                        )
                    previous_tail = _get_overlap_text(pieces[-1], overlap_chars)
                else:
                    acc.add(block_text)
                    continue
            else:
                acc.add(block_text)

            # If accumulator reaches target, flush (splitting if needed)
            if acc.total_chars >= target_chars:
                chunk_text = acc.flush()
                if len(chunk_text) > target_chars:
                    pieces = _split_text_by_sentences(chunk_text, target_chars)
                    for piece in pieces:
                        section_chunks.append(
                            _make_text_chunk(
                                piece,
                                file_id=file_id,
                                section=section,
                                chunk_index=len(section_chunks),
                            )
                        )
                    previous_tail = _get_overlap_text(pieces[-1], overlap_chars)
                else:
                    section_chunks.append(
                        _make_text_chunk(
                            chunk_text,
                            file_id=file_id,
                            section=section,
                            chunk_index=len(section_chunks),
                        )
                    )
                    previous_tail = _get_overlap_text(chunk_text, overlap_chars)

        # flush remaining accumulator
        if acc.total_chars > 0:
            chunk_text = acc.flush()
            section_chunks.append(
                _make_text_chunk(
                    chunk_text,
                    file_id=file_id,
                    section=section,
                    chunk_index=len(section_chunks),
                )
            )

        all_chunks.extend(section_chunks)

    # ── global chunk indices & linking ─────────────────────────────────
    for i, chunk in enumerate(all_chunks):
        chunk.chunk_index = i
        chunk.previous_chunk_id = all_chunks[i - 1].id if i > 0 else None
        chunk.next_chunk_id = all_chunks[i + 1].id if i + 1 < len(all_chunks) else None

    return all_chunks


def _make_text_chunk(
    content: str,
    *,
    file_id: str,
    section: SectionNode,
    chunk_index: int,
) -> FinalChunk:
    """Create a TEXT FinalChunk."""
    return FinalChunk(
        id=str(uuid.uuid4()),
        document_id=file_id,
        section_id=section.id,
        content=content,
        content_type=ContentType.TEXT,
        chunk_index=chunk_index,
        heading_path=list(section.heading_path),
    )


def _make_typed_chunk(
    content: str,
    *,
    content_type: ContentType,
    file_id: str,
    section: SectionNode,
    chunk_index: int,
) -> FinalChunk:
    """Create a typed FinalChunk (CODE, MERMAID, TABLE)."""
    return FinalChunk(
        id=str(uuid.uuid4()),
        document_id=file_id,
        section_id=section.id,
        content=content,
        content_type=content_type,
        chunk_index=chunk_index,
        heading_path=list(section.heading_path),
    )
