"""Offline asset resolution and metadata extraction."""

from __future__ import annotations

import hashlib
import mimetypes
import posixpath
import struct
from pathlib import Path, PurePosixPath
from typing import cast
from urllib.parse import unquote, urlsplit

from mdrack.domain.assets import (
    Asset,
    AssetGraph,
    AssetReference,
    AssetResolutionStatus,
    AssetSyntax,
)
from mdrack.domain.blocks import BlockType, SourceBlock
from mdrack.domain.chunks import RetrievalChunk
from mdrack.domain.documents import Document
from mdrack.domain.identifiers import content_fingerprint, logical_id


def _reference_target(block: SourceBlock) -> str:
    value = block.attributes.get("reference")
    return value if isinstance(value, str) else ""


def _normalized_target(raw_reference: str, syntax: str) -> tuple[str | None, str | None]:
    target = raw_reference.strip()
    if syntax == "obsidian":
        target = target.split("|", 1)[0].split("#", 1)[0].strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or target.startswith("//"):
        return None, "external_reference"
    target = unquote(parsed.path).replace("\\", "/")
    if not target or target.startswith("/") or ":" in target:
        return None, "unsafe_reference"
    return target, None


def _resolve_relative(document_path: str, target: str) -> str | None:
    joined = posixpath.normpath(str(PurePosixPath(document_path).parent / target))
    if joined in {"", ".", ".."} or joined.startswith("../") or joined.startswith("/"):
        return None
    pure = PurePosixPath(joined)
    if any(part in {"", ".", ".."} for part in pure.parts):
        return None
    return pure.as_posix()


def _image_dimensions(path: Path, mime_type: str | None) -> tuple[int | None, int | None]:
    try:
        with path.open("rb") as stream:
            header = stream.read(32)
    except OSError:
        return None, None
    if mime_type == "image/png" and header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
        return struct.unpack(">II", header[16:24])
    if mime_type == "image/gif" and header[:6] in {b"GIF87a", b"GIF89a"} and len(header) >= 10:
        return struct.unpack("<HH", header[6:10])
    return None, None


def _asset(root: Path, root_id: str, relative_path: str) -> Asset:
    path = root / PurePosixPath(relative_path)
    exists = path.is_file()
    content_hash: str | None = None
    size_bytes: int | None = None
    mime_type = mimetypes.guess_type(relative_path)[0]
    width: int | None = None
    height: int | None = None
    if exists:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        content_hash = digest.hexdigest()
        size_bytes = path.stat().st_size
        width, height = _image_dimensions(path, mime_type)
    return Asset(
        asset_id=logical_id("asset", root_id, relative_path),
        root_id=root_id,
        relative_path=relative_path,
        content_hash=content_hash,
        mime_type=mime_type,
        size_bytes=size_bytes,
        width=width,
        height=height,
        exists=exists,
    )


def build_asset_graph(
    document: Document,
    chunks: tuple[RetrievalChunk, ...],
    *,
    root: Path,
    root_id: str,
) -> AssetGraph:
    """Resolve image references without network, vision, OCR, or source mutation."""
    resolved_root = root.resolve()
    chunk_by_block = {
        block_id: chunk.chunk_id
        for chunk in chunks
        for block_id in chunk.parent_block_ids
        if chunk.content_type.value == "image_reference"
    }
    assets: dict[str, Asset] = {}
    references: list[AssetReference] = []
    for block in document.blocks:
        if block.block_type != BlockType.IMAGE_REFERENCE:
            continue
        raw_reference = _reference_target(block)
        syntax_value = block.attributes.get("syntax")
        syntax = syntax_value if syntax_value in {"markdown", "obsidian", "html"} else "markdown"
        target, failure = _normalized_target(raw_reference, syntax)
        relative_path = _resolve_relative(document.relative_path, target) if target is not None else None
        if target is not None and relative_path is None:
            failure = "unsafe_reference"
        asset_id: str | None = None
        status = failure or "missing"
        if relative_path is not None:
            candidate = (resolved_root / PurePosixPath(relative_path)).resolve()
            if not candidate.is_relative_to(resolved_root):
                status = "unsafe_reference"
            else:
                item = _asset(resolved_root, root_id, relative_path)
                assets[item.asset_id] = item
                asset_id = item.asset_id
                status = "resolved" if item.exists else "missing"
        alt_value = block.attributes.get("alt_text")
        surrounding_value = block.attributes.get("surrounding_text")
        references.append(
            AssetReference(
                reference_id=logical_id(
                    "asset-reference",
                    document.document_id,
                    block.block_id,
                    content_fingerprint(raw_reference),
                    block.source_span.start_line,
                    block.source_span.end_line,
                ),
                asset_id=asset_id,
                document_id=document.document_id,
                document_relative_path=document.relative_path,
                block_id=block.block_id,
                chunk_id=chunk_by_block.get(block.block_id),
                raw_reference=raw_reference,
                syntax=cast(AssetSyntax, syntax),
                source_span=block.source_span,
                alt_text=alt_value if isinstance(alt_value, str) and alt_value.strip() else None,
                surrounding_text=(
                    surrounding_value
                    if isinstance(surrounding_value, str) and surrounding_value.strip()
                    else None
                ),
                resolution_status=cast(AssetResolutionStatus, status),
            )
        )
    return AssetGraph(tuple(assets.values()), tuple(references))
