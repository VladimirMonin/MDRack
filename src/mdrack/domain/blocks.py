"""Stable parser-independent source block models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping, TypeAlias

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


class BlockType(str, Enum):
    """Structural Markdown block kinds exposed by the stable Document IR."""

    FRONTMATTER = "frontmatter"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    BLOCKQUOTE = "blockquote"
    CALLOUT = "callout"
    CODE = "code"
    MERMAID = "mermaid"
    TABLE = "table"
    THEMATIC_BREAK = "thematic_break"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceSpan:
    """One-based inclusive line span and optional half-open character offsets."""

    start_line: int
    end_line: int
    start_offset: int | None = None
    end_offset: int | None = None

    def __post_init__(self) -> None:
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("source span must use positive ordered line numbers")
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("source offsets must either both be present or both be absent")
        if self.start_offset is not None and (
            self.start_offset < 0 or self.end_offset is None or self.end_offset < self.start_offset
        ):
            raise ValueError("source offsets must be non-negative and ordered")


@dataclass(frozen=True)
class SourceBlock:
    """A lossless structural source unit independent of parser-library tokens."""

    block_id: str
    document_id: str
    block_type: BlockType
    raw_markdown: str
    plain_text: str | None
    language: str | None
    heading_level: int | None
    heading_path: tuple[str, ...]
    source_span: SourceSpan
    attributes: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.block_id or not self.document_id:
            raise ValueError("block_id and document_id are required")
        if self.heading_level is not None and not 1 <= self.heading_level <= 6:
            raise ValueError("heading_level must be in [1, 6]")
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))
