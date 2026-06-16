"""Intermediate Representation models for Markdown document processing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class BlockType(Enum):
    """Types of Markdown blocks."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    CODE = "code"
    TABLE = "table"
    LIST = "list"
    BLOCKQUOTE = "blockquote"
    THEMATIC_BREAK = "thematic_break"


class ContentType(Enum):
    """Types of content in final chunks."""

    TEXT = "text"
    CODE = "code"
    MERMAID = "mermaid"
    TABLE = "table"
    MIXED = "mixed"


@dataclass
class MarkdownBlock:
    """A single parsed block from a Markdown document.

    Represents a structural unit extracted from Markdown source.
    Parser-agnostic: the parser creates these blocks, this model validates them.
    """

    type: BlockType
    content: str
    start_line: int
    end_line: int
    language: str | None = None

    def __post_init__(self) -> None:
        if self.start_line < 1:
            raise ValueError(f"start_line must be >= 1, got {self.start_line}")
        if self.end_line < self.start_line:
            raise ValueError(
                f"end_line ({self.end_line}) must be >= start_line ({self.start_line})"
            )
        if self.type == BlockType.CODE and not self.language:
            raise ValueError("language is required for code blocks")


@dataclass
class SectionNode:
    """A section in the document hierarchy derived from headings.

    Sections form a tree structure via parent_id links.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    document_id: str = ""
    title: str = ""
    heading_path: list[str] = field(default_factory=list)
    level: int = 1
    start_line: int = 1
    end_line: int = 1
    parent_id: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.level <= 4:
            raise ValueError(f"level must be in [1, 4], got {self.level}")


@dataclass
class FinalChunk:
    """A final chunk ready for embedding and retrieval.

    Chunks form a doubly-linked list via previous/next pointers
    to preserve document order.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    document_id: str = ""
    section_id: str = ""
    content: str = ""
    content_type: ContentType = ContentType.TEXT
    chunk_index: int = 0
    heading_path: list[str] = field(default_factory=list)
    previous_chunk_id: str | None = None
    next_chunk_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.content_type, ContentType):
            raise TypeError(
                f"content_type must be a ContentType enum, got {type(self.content_type)}"
            )


@dataclass
class ParsedDocument:
    """The complete parsed representation of a Markdown file.

    Contains metadata, frontmatter, structural blocks, and the source hash
    for deduplication.
    """

    file_path: str
    relative_path: str = ""
    title: str = ""
    frontmatter: dict[str, Any] = field(default_factory=dict)
    blocks: list[MarkdownBlock] = field(default_factory=list)
    source_hash: str = ""

    def __post_init__(self) -> None:
        fp = self.file_path
        is_abs = Path(fp).is_absolute() or fp.startswith("/") or (len(fp) >= 2 and fp[1] == ":")
        if not is_abs:
            raise ValueError(f"file_path must be absolute, got: {fp}")
