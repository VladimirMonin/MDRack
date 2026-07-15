"""Portable image asset and source-reference domain models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from mdrack.domain.blocks import SourceSpan

AssetResolutionStatus = Literal["resolved", "missing", "unsafe_reference", "external_reference"]
AssetSyntax = Literal["markdown", "obsidian", "html"]


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or value.startswith("/")
        or path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("asset path must be a normalized relative POSIX path")


@dataclass(frozen=True)
class Asset:
    """One root-relative asset identity; file content is never stored here."""

    asset_id: str
    root_id: str
    relative_path: str
    content_hash: str | None
    mime_type: str | None
    size_bytes: int | None
    width: int | None
    height: int | None
    exists: bool

    def __post_init__(self) -> None:
        if not self.asset_id or not self.root_id:
            raise ValueError("asset_id and root_id are required")
        _validate_relative_path(self.relative_path)
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("asset size cannot be negative")
        if (self.width is None) != (self.height is None):
            raise ValueError("asset dimensions must both be present or absent")
        if self.width is not None and (self.width < 1 or self.height is None or self.height < 1):
            raise ValueError("asset dimensions must be positive")


@dataclass(frozen=True)
class AssetReference:
    """Lossless source reference linked to a portable asset identity when safe."""

    reference_id: str
    asset_id: str | None
    document_id: str
    document_relative_path: str
    block_id: str
    chunk_id: str | None
    raw_reference: str
    syntax: AssetSyntax
    source_span: SourceSpan
    alt_text: str | None
    surrounding_text: str | None
    resolution_status: AssetResolutionStatus

    def __post_init__(self) -> None:
        if not self.reference_id or not self.document_id or not self.block_id or not self.raw_reference:
            raise ValueError("reference, document, block, and raw reference are required")
        _validate_relative_path(self.document_relative_path)
        if self.resolution_status in {"resolved", "missing"} and self.asset_id is None:
            raise ValueError("safe resolved or missing references require an asset identity")
        if self.resolution_status in {"unsafe_reference", "external_reference"} and self.asset_id is not None:
            raise ValueError("unsafe or external references cannot have an asset identity")

    @property
    def searchable_text(self) -> str:
        """Return only explicitly searchable non-visual text."""
        parts = [part.strip() for part in (self.alt_text, self.surrounding_text) if part and part.strip()]
        return "\n".join(dict.fromkeys(parts))


@dataclass(frozen=True)
class AssetGraph:
    assets: tuple[Asset, ...]
    references: tuple[AssetReference, ...]
