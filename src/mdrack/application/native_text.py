"""Ordinary selected text/Markdown through the existing prepared native-v2 port."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from mdrack.application.compatibility import prepared_file_to_resource_batch
from mdrack.application.indexing import IndexingService
from mdrack.application.reindexing import PreparedReindex, producer_job, publish_reindex
from mdrack.application.source_resolution import SourceResolver
from mdrack.application.vector_values import canonicalize_for_space
from mdrack.domain.embedding_inputs import EncodingRole, TextInput
from mdrack.domain.identifiers import logical_id
from mdrack.domain.profiles import IncompatibleEmbeddingProfileError
from mdrack.ingestion.raw_source_provenance import (
    RawInputBudget,
    RawMediaKind,
    RawSignatureFact,
    RawSignatureKind,
    RawSourceError,
    RawSourceErrorCode,
    RawSourceSnapshot,
    capture_source,
    check_source_after,
)
from mdrack.ports.embeddings import PreparedEmbeddingProvider
from mdrack_core import (
    REPRESENTATION_RETRIEVAL_TEXT,
    EmbeddingSpaceRecord,
    ExpectedPublication,
    PreparedResourceBatch,
    PublicationResult,
    ResourceSnapshot,
)
from mdrack_core.ports.publication import PublicationPort

_NATIVE_TEXT_SPACE_NAME = "native"


@dataclass(frozen=True)
class NativeTextIngestionResult:
    """One ordinary document's confirmed native publication or unchanged replay."""

    resource_id: str
    source_revision: str
    source_bytes: int
    space_id: str
    profile_fingerprint: str
    representation_ids: tuple[str, ...]
    unit_ids: tuple[str, ...]
    chunk_count: int
    vector_count: int
    model_call_count: int
    prepared_input_bytes: int
    publication_status: str
    unchanged: bool
    status: str = "success"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "mdrack.native-text-ingest.v1",
            "resource_id": self.resource_id,
            "resource_kind": "document",
            "source_revision": self.source_revision,
            "source_bytes": self.source_bytes,
            "space_id": self.space_id,
            "profile_fingerprint": self.profile_fingerprint,
            "representation_ids": list(self.representation_ids),
            "unit_ids": list(self.unit_ids),
            "chunk_count": self.chunk_count,
            "vector_count": self.vector_count,
            "model_call_count": self.model_call_count,
            "prepared_input_bytes": self.prepared_input_bytes,
            "publication_status": self.publication_status,
            "unchanged": self.unchanged,
            "status": self.status,
        }


def preflight_native_text_space(storage: object, space: EmbeddingSpaceRecord) -> bool:
    """Refuse an existing incompatible document space before any provider call.

    Native image/visual spaces remain independent. ``read_text_embedding_spaces``
    is the existing authoritative app TEXT projection; unknown or legacy bindings
    are not adopted as compatible with this exact caller-owned space.
    """
    if not isinstance(space, EmbeddingSpaceRecord):
        raise TypeError("space must be an EmbeddingSpaceRecord")
    reader = getattr(storage, "read_text_embedding_spaces", None)
    if not callable(reader):
        raise IncompatibleEmbeddingProfileError()
    try:
        spaces = reader(_NATIVE_TEXT_SPACE_NAME)
    except IncompatibleEmbeddingProfileError:
        raise
    except (TypeError, ValueError):
        raise IncompatibleEmbeddingProfileError() from None
    if not isinstance(spaces, tuple):
        raise IncompatibleEmbeddingProfileError()
    for existing in spaces:
        if not isinstance(existing, EmbeddingSpaceRecord) or existing != space:
            raise IncompatibleEmbeddingProfileError()
    return bool(spaces)


def _target_snapshot_compatible(snapshot: ResourceSnapshot, space: EmbeddingSpaceRecord) -> None:
    batch = snapshot.batch
    if batch is None:
        return
    if batch.resource.resource_kind != "document":
        raise ValueError("native_text_resource_kind_conflict")
    used = {vector.space_id for vector in batch.vectors}
    for existing in batch.spaces:
        if existing.space_id in used and existing != space:
            raise IncompatibleEmbeddingProfileError()


def _same_document_identity(
    old: PreparedResourceBatch,
    base: PreparedResourceBatch,
    space: EmbeddingSpaceRecord,
) -> bool:
    """Compare graph identity without treating new vectors as a second document."""
    if old.resource.resource_kind != "document" or old.resource.resource_kind != base.resource.resource_kind:
        return False
    if (
        old.resource.content_hash != base.resource.content_hash
        or old.resource.source_namespace != base.resource.source_namespace
        or old.resource.locator != base.resource.locator
        or old.resource.media_type != base.resource.media_type
    ):
        return False
    old_rep = next((r for r in old.representations if r.representation_kind == REPRESENTATION_RETRIEVAL_TEXT), None)
    base_rep = next((r for r in base.representations if r.representation_kind == REPRESENTATION_RETRIEVAL_TEXT), None)
    if old_rep is None or base_rep is None:
        return False
    if old_rep.representation_id != base_rep.representation_id or old_rep.text != base_rep.text:
        return False
    old_units = {
        unit.unit_id: (unit.text, unit.evidence_locator)
        for unit in old.units
        if unit.representation_id == old_rep.representation_id
    }
    base_units = {
        unit.unit_id: (unit.text, unit.evidence_locator)
        for unit in base.units
        if unit.representation_id == base_rep.representation_id
    }
    if old_units != base_units:
        return False
    used = {vector.space_id for vector in old.vectors if vector.unit_id in old_units}
    return used == {space.space_id} and all(
        existing == space for existing in old.spaces if existing.space_id == space.space_id
    )


def _document_units(batch: PreparedResourceBatch) -> tuple[str, ...]:
    base_ids = {
        rep.representation_id
        for rep in batch.representations
        if rep.representation_kind == REPRESENTATION_RETRIEVAL_TEXT
    }
    return tuple(unit.unit_id for unit in batch.units if unit.representation_id in base_ids)


class _SourceGuardedPublication:
    """Check the captured source immediately before every publication decision."""

    def __init__(
        self,
        port: PublicationPort,
        source: RawSourceSnapshot,
        source_path: Path,
        budget: RawInputBudget,
    ) -> None:
        self.port = port
        self.source = source
        self.source_path = source_path
        self.budget = budget

    def _check(self) -> None:
        check_source_after(self.source, self.source_path, self.budget)

    def read_snapshot(self, resource_id: str) -> ResourceSnapshot:
        self._check()
        return self.port.read_snapshot(resource_id)

    def compare_and_publish(
        self, batch: PreparedResourceBatch, expected: ExpectedPublication,
    ) -> PublicationResult:
        self._check()
        return self.port.compare_and_publish(batch, expected)

    def withdraw_if_current(self, resource_id: str, expected: ExpectedPublication) -> PublicationResult:
        self._check()
        return self.port.withdraw_if_current(resource_id, expected)

    def delete_if_current(self, resource_id: str, expected: ExpectedPublication) -> PublicationResult:
        self._check()
        return self.port.delete_if_current(resource_id, expected)


async def ingest_native_text(
    provider: PreparedEmbeddingProvider,
    storage: object,
    path: Path | None,
    *,
    config: Any,
    resource_id: str | None = None,
    source_namespace: str,
    source_ref: str,
    media_type: str = "text/markdown",
    expected_revision: str | None = None,
    resolver: SourceResolver | None = None,
) -> NativeTextIngestionResult:
    """Encode one ordinary UTF-8 document through the prepared port.

    ``text/markdown`` keeps the canonical Markdown parser/chunker semantics;
    ``text/plain`` uses one literal text block and the same structural chunker,
    so the original bytes are not reinterpreted as Markdown. The provider is
    caller-owned and must already declare DOCUMENT/text. All source/identity
    preflight runs before the first model dispatch; publication uses the existing
    Core conditional writer and document provenance owner.
    """
    if media_type not in {"text/markdown", "text/plain"}:
        raise ValueError("unsupported_text_media_type")
    provider.capabilities.require(EncodingRole.DOCUMENT, "text")
    if not isinstance(source_namespace, str) or not source_namespace:
        raise ValueError("source_namespace_required")
    if not isinstance(source_ref, str) or not source_ref:
        raise ValueError("source_ref_required")
    if resource_id is None:
        resource_id = logical_id("doc", source_namespace, source_ref)
    if not isinstance(resource_id, str) or not resource_id:
        raise ValueError("resource_id_required")
    port = getattr(storage, "resource_store", None)
    if not isinstance(port, PublicationPort):
        raise TypeError("publication capability required")

    # Identity/data admission precedes source capture and every provider dispatch.
    preflight_native_text_space(storage, provider.space)
    before = port.read_snapshot(resource_id)
    _target_snapshot_compatible(before, provider.space)

    selected = (resolver or SourceResolver()).selected_path(
        namespace=source_namespace, source_ref=source_ref, selected=path,
    )
    signature = RawSignatureFact(RawSignatureKind.UTF8_TEXT, media_type, "utf8-strict-v1")
    budget = RawInputBudget()
    source = capture_source(selected, source_ref, RawMediaKind.TEXT, signature, budget=budget)
    try:
        revision = source.provenance.raw_source_sha256
        if expected_revision is not None and expected_revision != revision:
            raise RawSourceError(RawSourceErrorCode.SOURCE_CHANGED)
        try:
            text = source.source_bytes.decode("utf-8", "strict")
        except UnicodeDecodeError:
            raise RawSourceError(RawSourceErrorCode.SIGNATURE_UNSUPPORTED) from None

        indexing = IndexingService(
            selected.parent,
            config,
            cast(Any, storage),
            provider=None,
            profile="native",
            root_id=source_namespace,
        )
        prepare = (
            indexing.prepare_selected_plain_text
            if media_type == "text/plain"
            else indexing.prepare_selected_document
        )
        prepared = prepare(
            source_ref=source_ref,
            content=text,
            document_logical_id=resource_id,
            record_id=str(uuid.uuid4()),
            run_id=uuid.uuid4().hex,
        )
        if not prepared.chunks:
            raise RawSourceError(RawSourceErrorCode.PREPARED_EVIDENCE_INVALID)
        prepared = replace(prepared, source_provenance=cast(Any, {"provenance": source.provenance.to_dict()}))
        metadata_policy = getattr(storage, "metadata_policy", None)
        plain = media_type == "text/plain"
        adapter = "plain_text" if plain else "markdown"
        adapter_version = "1" if plain else "1.1"
        similarity_basis = "plain_text_retrieval_text" if plain else "markdown_retrieval_text"
        whole_resource_contract = (
            "mdrack.plain-text.whole-resource.v1" if plain else "mdrack.markdown.whole-resource.v1"
        )
        def project(prepared_file: Any) -> PreparedResourceBatch:
            return prepared_file_to_resource_batch(
                prepared_file,
                metadata_policy=metadata_policy,
                media_type=media_type,
                adapter=adapter,
                adapter_version=adapter_version,
                similarity_basis=similarity_basis,
                whole_resource_contract=whole_resource_contract,
            )

        base_batch = project(prepared)

        if before.batch is not None and _same_document_identity(before.batch, base_batch, provider.space):
            old = before.batch
            # Revalidate the actual publication state at the common conditional
            # owner. max_retries=0 makes the no-op path a one-shot conditional
            # check: a concurrent delete/withdraw/recreate returns conflict and
            # is never rebased into a write or stale success.
            outcome = publish_reindex(
                _SourceGuardedPublication(port, source, selected, budget),
                PreparedReindex(before, old, old.resource, "native.text"),
                max_retries=0,
            )
            if outcome["publication_status"] != "already_published":
                raise ValueError("native_text_publication_conflict")
            return NativeTextIngestionResult(
                resource_id,
                revision,
                source.provenance.byte_size,
                provider.space.space_id,
                provider.space.fingerprint,
                tuple(sorted(rep.representation_id for rep in old.representations)),
                tuple(sorted(unit.unit_id for unit in old.units)),
                len(_document_units(old)),
                len(old.vectors),
                0,
                0,
                "already_published",
                True,
            )

        values = tuple(
            TextInput(chunk.embedding_text, chunk.embedding_text_hash)
            for chunk in prepared.chunks
        )
        # Validate every prepared input before the first forward; no partial encode.
        for value in values:
            provider.capabilities.validate(value, EncodingRole.DOCUMENT)
        vectors: list[tuple[float, ...]] = []
        prepared_input_bytes = 0
        for value in values:
            prepared_input_bytes += value.byte_size
            result = await provider.encode(value, EncodingRole.DOCUMENT)
            if result.space != provider.space:
                raise ValueError("incompatible_embedding_space")
            vectors.append(canonicalize_for_space(result.vector, result.space))
        prepared = replace(prepared, vectors=tuple(vectors), embedding_space=provider.space)
        batch = project(prepared)
        job = producer_job(before, batch, owner="native.text", generation=revision)
        outcome = publish_reindex(
            _SourceGuardedPublication(port, source, selected, budget),
            job,
            max_retries=1,
        )
        publication_status = str(outcome["publication_status"])
        if publication_status not in {"published", "already_published"}:
            raise ValueError("native_text_publication_conflict")
        unit_ids = tuple(sorted(str(value) for value in outcome.get("unit_ids", ())))
        if not unit_ids:
            unit_ids = tuple(sorted(_document_units(batch)))
        representation_ids = tuple(sorted(str(value) for value in outcome.get("representation_ids", ())))
        if not representation_ids:
            representation_ids = tuple(sorted(rep.representation_id for rep in batch.representations))
        return NativeTextIngestionResult(
            resource_id,
            revision,
            source.provenance.byte_size,
            provider.space.space_id,
            provider.space.fingerprint,
            representation_ids,
            unit_ids,
            len(values),
            int(outcome.get("vector_count", len(batch.vectors))),
            len(values),
            prepared_input_bytes,
            publication_status,
            publication_status == "already_published",
        )
    finally:
        source.cleanup()


__all__ = [
    "NativeTextIngestionResult",
    "ingest_native_text",
    "preflight_native_text_space",
]
