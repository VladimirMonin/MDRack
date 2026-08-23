"""Provider-free ingestion of one explicitly selected local text source."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from mdrack.adapters.markdown_it import MarkdownItParser
from mdrack.application.chunking import StructuralChunker, StructuralChunkingConfig
from mdrack.domain.blocks import BlockType, SourceBlock, SourceSpan
from mdrack.domain.documents import Document
from mdrack.domain.identifiers import logical_id
from mdrack.ingestion.raw_source_provenance import (
    RawInputBudget,
    RawMediaKind,
    RawSignatureFact,
    RawSignatureKind,
    RawSourceError,
    RawSourceErrorCode,
    RawSourceSnapshot,
    canonical_json,
    capture_source,
    check_source_after,
    resource_metadata,
    sha256_digest,
    validate_budget,
    validate_source_ref,
)
from mdrack_core import (
    MODALITY_TEXT,
    REPRESENTATION_RETRIEVAL_TEXT,
    UNIT_TEXT_CHUNK,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchUnitRecord,
)
from mdrack_core.application.indexing import CoreIndexingService
from mdrack_core.ports.catalog import ResourceWritePort

logger = logging.getLogger(__name__)
RAW_TEXT_KIND = "raw_text"
RAW_TEXT_SCHEMA = "mdrack.raw-text-prepared-evidence.v1"
RAW_TEXT_SPAN_LOCATOR = "raw_local_source_span"


@dataclass(frozen=True)
class RawTextResult:
    resource_id: str
    resource_kind: str
    representation_count: int
    unit_count: int
    vector_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "resource_kind": self.resource_kind,
            "representation_count": self.representation_count,
            "unit_count": self.unit_count,
            "vector_count": self.vector_count,
        }


def _line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _plain_document(resource_id: str, source_ref: str, text: str) -> Document:
    block = SourceBlock(
        logical_id("raw-text-block", resource_id, 0, len(text)),
        resource_id,
        BlockType.PARAGRAPH,
        text,
        text,
        None,
        None,
        (),
        SourceSpan(1, _line_for_offset(text, len(text)), 0, len(text)),
    )
    return Document(
        resource_id, source_ref, "", {}, (block,), sha256_digest(text), "raw_text_plain", "1"
    )


class _GuardedWritePort:
    def __init__(self, delegate: ResourceWritePort, snapshot: RawSourceSnapshot, source_path: Path, budget: RawInputBudget) -> None:  # noqa: E501
        self._delegate = delegate
        self._snapshot = snapshot
        self._source_path = source_path
        self._budget = budget

    def replace_resource(self, batch: PreparedResourceBatch) -> None:
        check_source_after(self._snapshot, self._source_path, self._budget)
        self._delegate.replace_resource(batch)

    def delete_resource(self, resource_id: str) -> None:
        self._delegate.delete_resource(resource_id)


class RawTextIngestionService:
    """Prepare and atomically replace one raw text graph without providers."""

    def __init__(self, catalog: ResourceWritePort, *, budget: RawInputBudget | None = None, chunking: StructuralChunkingConfig | None = None) -> None:  # noqa: E501
        if not callable(getattr(catalog, "replace_resource", None)):
            raise TypeError("catalog must support complete resource replacement")
        self._catalog = catalog
        self._budget = budget or RawInputBudget()
        self._chunker = StructuralChunker(chunking)

    def prepare(self, snapshot: RawSourceSnapshot, *, source_path: Path, source_ref: str, media_type: str, root: Path) -> PreparedResourceBatch:  # noqa: E501
        validate_source_ref(source_ref)
        if media_type not in {"text/plain", "text/markdown"}:
            raise RawSourceError(RawSourceErrorCode.MIME_SIGNATURE_MISMATCH)
        resolved = source_path.resolve()
        root_resolved = root.resolve()
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            pass
        else:
            raise RawSourceError(RawSourceErrorCode.SOURCE_REF_INVALID)
        try:
            text = snapshot.source_bytes.decode("utf-8", "strict")
        except UnicodeDecodeError:
            raise RawSourceError(RawSourceErrorCode.SIGNATURE_UNSUPPORTED) from None
        resource_id = logical_id("raw-text", "v1", source_ref)
        if media_type == "text/plain":
            document = _plain_document(resource_id, source_ref, text)
        else:
            document = MarkdownItParser().parse(Path(snapshot.temporary_path), content=text, document_id=resource_id, relative_path=source_ref)  # noqa: E501
        chunks = self._chunker.build(document)
        if not chunks:
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
        prepared_text = "\n\n".join(chunk.embedding_text for chunk in chunks)
        validate_budget(snapshot.provenance, self._budget, prepared_text=prepared_text, whole_resource_tokens=sum(chunk.estimated_tokens for chunk in chunks))  # noqa: E501
        evidence = {
            "schema": RAW_TEXT_SCHEMA,
            "media_type": media_type,
            "parser": {"name": document.parser_name, "version": document.parser_version},
            "chunker": {"name": self._chunker.name, "version": self._chunker.version},
            "chunks": [  # noqa: E501
                {"display": chunk.display_content, "embedding": chunk.embedding_text, "heading_path": list(chunk.heading_path), "block_kind": chunk.content_type.value, "chunk_kind": UNIT_TEXT_CHUNK, "span": {"start_line": chunk.source_span.start_line, "end_line": chunk.source_span.end_line, "start_offset": chunk.source_span.start_offset, "end_offset": chunk.source_span.end_offset}}  # noqa: E501
                for chunk in chunks
            ],
        }
        prepared_digest = sha256_digest(canonical_json(evidence))
        provenance = snapshot.provenance
        provenance = type(provenance)(provenance.source_ref, provenance.media_kind, provenance.raw_source_sha256, provenance.byte_size, provenance.signature, provenance.duration_ms, provenance.selected_frame_count, prepared_digest, provenance.budget_fingerprint)  # noqa: E501
        metadata = {**resource_metadata(provenance), "adapter": "raw_text", "parser": document.parser_name, "parser_version": document.parser_version, "chunker": self._chunker.name, "chunker_version": self._chunker.version}  # noqa: E501
        representations = (RepresentationRecord(logical_id("raw-text-representation", resource_id, media_type), resource_id, REPRESENTATION_RETRIEVAL_TEXT, MODALITY_TEXT, prepared_text, producer_fingerprint="raw_text-v1", token_count=sum(chunk.estimated_tokens for chunk in chunks), token_count_kind="estimated"),)  # noqa: E501
        units = tuple(SearchUnitRecord(logical_id("raw-text-unit", resource_id, index, chunk.chunk_id), resource_id, representations[0].representation_id, UNIT_TEXT_CHUNK, MODALITY_TEXT, chunk.embedding_text, Locator(RAW_TEXT_SPAN_LOCATOR, cast(Any, {"source_ref": source_ref, "start_line": chunk.source_span.start_line, "end_line": chunk.source_span.end_line, "start_char": chunk.source_span.start_offset or 0, "end_char": chunk.source_span.end_offset or 0, "heading_path": list(chunk.heading_path), "block_kind": chunk.content_type.value, "chunk_kind": UNIT_TEXT_CHUNK})), index, chunk.estimated_tokens, "estimated") for index, chunk in enumerate(chunks))  # noqa: E501
        return PreparedResourceBatch(ResourceRecord(resource_id, RAW_TEXT_KIND, media_type, "raw_local", provenance.locator, provenance.raw_source_sha256, None, metadata), representations, units)  # noqa: E501

    def ingest(self, source_path: Path, *, source_ref: str, media_type: str, root: Path) -> RawTextResult:
        signature = RawSignatureFact(RawSignatureKind.UTF8_TEXT, media_type, "utf8-strict-v1")
        snapshot = capture_source(source_path, source_ref, RawMediaKind.TEXT, signature, self._budget)
        try:
            batch = self.prepare(snapshot, source_path=source_path, source_ref=source_ref, media_type=media_type, root=root)  # noqa: E501
            CoreIndexingService(cast(ResourceWritePort, _GuardedWritePort(self._catalog, snapshot, source_path, self._budget))).index(batch)  # noqa: E501
            return RawTextResult(batch.resource.resource_id, batch.resource.resource_kind, len(batch.representations), len(batch.units))  # noqa: E501
        finally:
            snapshot.cleanup()


__all__ = ["RAW_TEXT_KIND", "RAW_TEXT_SPAN_LOCATOR", "RawTextIngestionService", "RawTextResult"]
