"""Retrieval chunk models kept distinct from lossless source blocks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mdrack.domain.blocks import SourceSpan


class RetrievalContentType(str, Enum):
    TEXT = "text"
    LIST = "list"
    BLOCKQUOTE = "blockquote"
    CALLOUT = "callout"
    CODE = "code"
    TABLE = "table"
    MERMAID = "mermaid"
    IMAGE_REFERENCE = "image_reference"
    UNKNOWN = "unknown"
    HEADING = "heading"


@dataclass(frozen=True)
class RetrievalChunk:
    """A bounded model-facing representation derived from one source block."""

    chunk_id: str
    document_id: str
    parent_block_ids: tuple[str, ...]
    display_content: str
    embedding_text: str
    content_type: RetrievalContentType
    chunk_index: int
    heading_path: tuple[str, ...]
    source_span: SourceSpan
    estimated_tokens: int

    def __post_init__(self) -> None:
        if not self.chunk_id or not self.document_id or not self.parent_block_ids:
            raise ValueError("chunk, document, and parent block identifiers are required")
        if not self.display_content.strip() or not self.embedding_text.strip():
            raise ValueError("display_content and embedding_text are required")
        if self.chunk_index < 0 or self.estimated_tokens < 1:
            raise ValueError("chunk_index and estimated_tokens must be positive")
