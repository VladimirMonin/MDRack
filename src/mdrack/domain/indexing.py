"""Domain models shared by application services and storage adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

from mdrack.domain.assets import Asset, AssetReference
from mdrack.domain.profiles import EmbeddingProfile

IndexStatus = Literal["success", "partial_success", "failed"]

_ROOT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class SourceLocator:
    """Portable source location containing no absolute filesystem path."""

    root_id: str
    relative_path: str
    start_line: int
    end_line: int
    heading_path: tuple[str, ...]
    block_id: str
    chunk_id: str
    start_offset: int | None = None
    end_offset: int | None = None
    block_kind: str = "unknown"
    chunk_kind: str = "unknown"

    def __post_init__(self) -> None:
        if not _ROOT_ID_PATTERN.fullmatch(self.root_id):
            raise ValueError("root_id must be a non-empty portable identifier")
        path = self.relative_path
        pure_path = PurePosixPath(path)
        if (
            not path
            or "\\" in path
            or ":" in path
            or path.startswith("/")
            or "//" in path
            or pure_path.is_absolute()
            or not pure_path.parts
            or path != pure_path.as_posix()
            or any(part in {"", ".", ".."} for part in pure_path.parts)
        ):
            raise ValueError("relative_path must be a normalized relative POSIX path")
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("source span must use positive ordered line numbers")
        if not self.block_id or not self.chunk_id:
            raise ValueError("block_id and chunk_id are required")
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("source offsets must either both be present or both be absent")
        if self.start_offset is not None and (
            self.start_offset < 0
            or self.end_offset is None
            or self.end_offset < self.start_offset
        ):
            raise ValueError("source offsets must be non-negative and ordered")
        if not self.block_kind or not self.chunk_kind:
            raise ValueError("block_kind and chunk_kind are required")

    def to_dict(self) -> dict[str, object]:
        return {
            "root_id": self.root_id,
            "relative_path": self.relative_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "heading_path": list(self.heading_path),
            "block_kind": self.block_kind,
            "chunk_kind": self.chunk_kind,
            "block_logical_id": self.block_id,
            "chunk_logical_id": self.chunk_id,
        }


@dataclass(frozen=True)
class StoredSection:
    record_id: str
    logical_id: str
    title: str
    heading_path: tuple[str, ...]
    level: int
    start_line: int
    end_line: int
    parent_record_id: str | None


@dataclass(frozen=True)
class StoredChunk:
    record_id: str
    logical_id: str
    section_record_id: str
    content: str
    content_type: str
    chunk_index: int
    heading_path: tuple[str, ...]
    previous_record_id: str | None
    next_record_id: str | None
    embedding_text: str
    embedding_text_hash: str
    start_line: int
    end_line: int
    block_logical_id: str
    start_offset: int | None = None
    end_offset: int | None = None
    block_kind: str = "unknown"
    chunk_kind: str = "unknown"


@dataclass(frozen=True)
class PreparedFile:
    record_id: str
    logical_id: str
    root_id: str
    relative_path: str
    title: str
    source_hash: str
    indexed_at: str
    parser_name: str
    parser_version: str
    chunk_strategy_name: str
    chunk_strategy_version: str
    index_run_id: str
    sections: tuple[StoredSection, ...]
    chunks: tuple[StoredChunk, ...]
    assets: tuple[Asset, ...] = ()
    asset_references: tuple[AssetReference, ...] = ()
    vectors: tuple[tuple[float, ...], ...] = ()
    embedding_profile: EmbeddingProfile | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    embedding_endpoint: str | None = None


@dataclass(frozen=True)
class IndexingResult:
    run_id: str
    status: IndexStatus
    files_seen: int = 0
    files_changed: int = 0
    files_indexed: int = 0
    files_failed: int = 0
    files_deleted: int = 0
    chunks_created: int = 0
    errors_count: int = 0
    error_codes: tuple[str, ...] = field(default_factory=tuple)
