"""Build final chunks from parsed blocks and section hierarchy.

Chunks are assembled from buffered block groups inside a section instead of
emitting one chunk per Markdown block.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from mdrack.markdown.ir import BlockType, ContentType, FinalChunk, MarkdownBlock, SectionNode

_DEFAULT_CONFIG: dict[str, int] = {
    "min_chunk_chars": 1200,
    "target_chunk_chars": 3200,
    "hard_limit_chars": 8000,
    "overlap_chars": 300,
}

_TEXT_BLOCK_TYPES = {
    BlockType.PARAGRAPH,
    BlockType.LIST,
    BlockType.BLOCKQUOTE,
}


def _is_mermaid_block(block: MarkdownBlock) -> bool:
    """Check if block is a mermaid diagram block."""
    return (
        block.type == BlockType.CODE
        and block.language is not None
        and "mermaid" in block.language.lower()
    )


def _is_blank_or_skippable(block: MarkdownBlock) -> bool:
    """Return True for blocks that must not produce chunk content."""
    return block.type == BlockType.THEMATIC_BREAK or not block.content.strip()


def _render_blocks(blocks: list[MarkdownBlock]) -> str:
    """Render a block list into chunk content."""
    return "\n\n".join(block.content for block in blocks if block.content.strip())


def _detect_content_type(blocks: list[MarkdownBlock]) -> ContentType:
    """Detect the dominant content type for a chunk draft."""
    relevant = [
        block for block in blocks
        if not _is_blank_or_skippable(block) and block.type != BlockType.HEADING
    ]
    if not relevant:
        return ContentType.TEXT

    has_text = any(block.type in _TEXT_BLOCK_TYPES for block in relevant)
    special_types: set[ContentType] = set()
    for block in relevant:
        if block.type == BlockType.CODE:
            if _is_mermaid_block(block):
                special_types.add(ContentType.MERMAID)
            else:
                special_types.add(ContentType.CODE)
        elif block.type == BlockType.TABLE:
            special_types.add(ContentType.TABLE)

    if has_text and special_types:
        return ContentType.MIXED
    if has_text:
        return ContentType.TEXT
    if len(special_types) == 1:
        return next(iter(special_types))
    return ContentType.MIXED if special_types else ContentType.TEXT


def _split_text_by_sentences(text: str, max_chars: int) -> list[str]:
    """Split text into pieces respecting sentence boundaries."""
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
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
    """Extract trailing text of *text* up to *overlap_chars* characters."""
    if overlap_chars <= 0 or len(text) <= overlap_chars:
        return text
    tail = text[-overlap_chars:]
    space_idx = tail.find(" ")
    if space_idx != -1:
        tail = tail[space_idx + 1 :]
    return tail


@dataclass
class _ChunkDraft:
    """Internal mutable chunk representation before final UUID/linking."""

    section: SectionNode
    blocks: list[MarkdownBlock]
    content: str
    content_type: ContentType


@dataclass
class _ChunkAccumulator:
    """Accumulates grouped blocks for one section-local chunk."""

    groups: list[list[MarkdownBlock]] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    total_chars: int = 0

    def add(self, group: list[MarkdownBlock], text: str) -> None:
        if self.texts:
            self.total_chars += 2
        self.groups.append(group)
        self.texts.append(text)
        self.total_chars += len(text)

    def flush(self) -> tuple[str, list[MarkdownBlock]]:
        content = "\n\n".join(self.texts)
        blocks = [block for group in self.groups for block in group]
        self.groups.clear()
        self.texts.clear()
        self.total_chars = 0
        return content, blocks


def _group_section_blocks(blocks: list[MarkdownBlock]) -> list[list[MarkdownBlock]]:
    """Attach heading blocks to the next content block in the same section."""
    grouped: list[list[MarkdownBlock]] = []
    pending_headings: list[MarkdownBlock] = []

    for block in blocks:
        if _is_blank_or_skippable(block):
            continue
        if block.type == BlockType.HEADING:
            pending_headings.append(block)
            continue

        if pending_headings:
            grouped.append([*pending_headings, block])
            pending_headings.clear()
        else:
            grouped.append([block])

    return grouped


def _can_split_group(group: list[MarkdownBlock]) -> bool:
    """Only text-like groups may be split into smaller pieces."""
    content_type = _detect_content_type(group)
    return content_type == ContentType.TEXT


def _make_draft(section: SectionNode, blocks: list[MarkdownBlock], content: str) -> _ChunkDraft | None:
    """Create a chunk draft unless the content is effectively empty."""
    if not content.strip():
        return None

    relevant = [block for block in blocks if not _is_blank_or_skippable(block)]
    if not relevant:
        return None

    content_type = _detect_content_type(relevant)
    if content_type == ContentType.TEXT and not any(
        block.type != BlockType.HEADING for block in relevant
    ):
        return None

    return _ChunkDraft(
        section=section,
        blocks=list(relevant),
        content=content,
        content_type=content_type,
    )


def _flush_accumulator(section: SectionNode, acc: _ChunkAccumulator) -> list[_ChunkDraft]:
    """Convert the current accumulator into one chunk draft."""
    if acc.total_chars == 0:
        return []
    content, blocks = acc.flush()
    draft = _make_draft(section, blocks, content)
    return [draft] if draft is not None else []


def _split_large_text_group(
    section: SectionNode,
    group: list[MarkdownBlock],
    text: str,
    target_chars: int,
) -> list[_ChunkDraft]:
    """Split an oversized text group into sentence-bounded drafts."""
    pieces = _split_text_by_sentences(text, target_chars)
    drafts: list[_ChunkDraft] = []
    for piece in pieces:
        draft = _make_draft(section, group, piece)
        if draft is not None:
            drafts.append(draft)
    return drafts


def _pack_section_blocks(
    section: SectionNode,
    blocks: list[MarkdownBlock],
    target_chars: int,
    hard_limit: int,
) -> list[_ChunkDraft]:
    """Pack one section's blocks into buffered chunk drafts."""
    groups = _group_section_blocks(blocks)
    drafts: list[_ChunkDraft] = []
    acc = _ChunkAccumulator()

    for group in groups:
        group_text = _render_blocks(group)
        if not group_text.strip():
            continue

        projected = acc.total_chars + len(group_text) + (2 if acc.total_chars > 0 else 0)
        if acc.total_chars > 0 and projected > hard_limit:
            drafts.extend(_flush_accumulator(section, acc))

        if acc.total_chars == 0 and len(group_text) > hard_limit and _can_split_group(group):
            drafts.extend(_split_large_text_group(section, group, group_text, target_chars))
            continue

        acc.add(group, group_text)
        if acc.total_chars >= target_chars:
            drafts.extend(_flush_accumulator(section, acc))

    drafts.extend(_flush_accumulator(section, acc))
    return drafts


def _merge_drafts(left: _ChunkDraft, right: _ChunkDraft) -> _ChunkDraft:
    """Merge two chunk drafts from the same section."""
    merged_blocks = [*left.blocks, *right.blocks]
    merged_content = f"{left.content}\n\n{right.content}"
    return _ChunkDraft(
        section=left.section,
        blocks=merged_blocks,
        content=merged_content,
        content_type=_detect_content_type(merged_blocks),
    )


def _merge_small_chunks(
    chunks: list[_ChunkDraft],
    min_chars: int,
    hard_limit: int,
) -> list[_ChunkDraft]:
    """Merge undersized chunks with neighbors inside one section."""
    if len(chunks) <= 1:
        return chunks

    merged: list[_ChunkDraft] = []
    index = 0
    while index < len(chunks):
        chunk = chunks[index]
        if len(chunk.content) < min_chars:
            if merged:
                previous = merged[-1]
                if len(previous.content) + 2 + len(chunk.content) <= hard_limit:
                    merged[-1] = _merge_drafts(previous, chunk)
                    index += 1
                    continue

            if index + 1 < len(chunks):
                following = chunks[index + 1]
                if len(chunk.content) + 2 + len(following.content) <= hard_limit:
                    merged.append(_merge_drafts(chunk, following))
                    index += 2
                    continue

        merged.append(chunk)
        index += 1

    if len(merged) >= 2 and len(merged[-1].content) < min_chars:
        previous = merged[-2]
        last = merged[-1]
        if len(previous.content) + 2 + len(last.content) <= hard_limit:
            merged[-2] = _merge_drafts(previous, last)
            merged.pop()

    return merged


def _apply_overlap(
    chunks: list[_ChunkDraft],
    overlap_chars: int,
    hard_limit: int,
) -> list[_ChunkDraft]:
    """Add trailing overlap between adjacent text-only chunks."""
    if overlap_chars <= 0:
        return chunks

    for index in range(1, len(chunks)):
        previous = chunks[index - 1]
        current = chunks[index]
        if previous.section.id != current.section.id:
            continue
        if previous.content_type != ContentType.TEXT or current.content_type != ContentType.TEXT:
            continue
        overlap = _get_overlap_text(previous.content, overlap_chars)
        if not overlap:
            continue
        remaining = hard_limit - len(current.content)
        if remaining <= 1:
            continue
        safe_overlap = overlap if len(overlap) + 1 <= remaining else overlap[-(remaining - 1) :]
        if not safe_overlap.strip():
            continue
        current.content = f"{safe_overlap} {current.content}".strip()

    return chunks


def _assign_blocks_to_sections(
    blocks: list[MarkdownBlock],
    sections: list[SectionNode],
) -> dict[str, list[MarkdownBlock]]:
    """Assign each block to the deepest covering section exactly once."""
    assigned: dict[str, list[MarkdownBlock]] = {section.id: [] for section in sections}
    ranked_sections = sorted(
        sections,
        key=lambda section: (section.level, section.start_line),
        reverse=True,
    )

    for block in blocks:
        owner: SectionNode | None = None
        for section in ranked_sections:
            if block.start_line >= section.start_line and block.end_line <= section.end_line:
                owner = section
                break
        if owner is not None:
            assigned[owner.id].append(block)

    return assigned


def _finalize_chunks(chunks: list[_ChunkDraft], file_id: str) -> list[FinalChunk]:
    """Convert chunk drafts to FinalChunk and relink indices."""
    final_chunks: list[FinalChunk] = []
    for index, draft in enumerate(chunks):
        final_chunks.append(
            FinalChunk(
                id=str(uuid.uuid4()),
                document_id=file_id,
                section_id=draft.section.id,
                content=draft.content,
                content_type=draft.content_type,
                chunk_index=index,
                heading_path=list(draft.section.heading_path),
            )
        )

    for index, chunk in enumerate(final_chunks):
        chunk.previous_chunk_id = final_chunks[index - 1].id if index > 0 else None
        chunk.next_chunk_id = final_chunks[index + 1].id if index + 1 < len(final_chunks) else None

    return final_chunks


def build_chunks(
    blocks: list[MarkdownBlock],
    sections: list[SectionNode],
    file_id: str,
    config: dict | None = None,
) -> list[FinalChunk]:
    """Build final chunks from parsed blocks and section hierarchy."""
    if not blocks or not sections:
        return []

    cfg = {**_DEFAULT_CONFIG, **(config or {})}
    min_chars = cfg["min_chunk_chars"]
    target_chars = cfg["target_chunk_chars"]
    hard_limit = cfg["hard_limit_chars"]
    overlap_chars = cfg["overlap_chars"]

    section_blocks = _assign_blocks_to_sections(blocks, sections)
    drafts: list[_ChunkDraft] = []

    for section in sorted(sections, key=lambda current: current.start_line):
        owned_blocks = section_blocks.get(section.id, [])
        if not owned_blocks:
            continue
        section_drafts = _pack_section_blocks(section, owned_blocks, target_chars, hard_limit)
        section_drafts = _merge_small_chunks(section_drafts, min_chars, hard_limit)
        drafts.extend(section_drafts)

    drafts = _apply_overlap(drafts, overlap_chars, hard_limit)
    return _finalize_chunks(drafts, file_id)
