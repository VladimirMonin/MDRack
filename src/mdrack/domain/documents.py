"""Stable Markdown Document intermediate representation."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from mdrack.domain.blocks import JSONValue, SourceBlock


@dataclass(frozen=True)
class Document:
    """Parser-independent representation of one Markdown source document."""

    document_id: str
    relative_path: str
    title: str
    frontmatter: Mapping[str, JSONValue]
    blocks: tuple[SourceBlock, ...]
    source_hash: str
    parser_name: str
    parser_version: str

    def __post_init__(self) -> None:
        if not self.document_id or not self.relative_path:
            raise ValueError("document_id and relative_path are required")
        if not self.source_hash or not self.parser_name or not self.parser_version:
            raise ValueError("source hash and parser identity are required")
        object.__setattr__(self, "frontmatter", MappingProxyType(dict(self.frontmatter)))
