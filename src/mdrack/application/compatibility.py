"""S6 app-owned projection between legacy MDRack and the frozen core contracts."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import Any, Iterator, Literal, cast

from mdrack.adapters.sqlite.generation_runtime import SQLiteGenerationRuntime
from mdrack.adapters.sqlite.index_storage import SQLiteIndexStorage, create_sqlite_index_storage
from mdrack.adapters.sqlite.resource_store import SQLiteResourceStore
from mdrack.application.generation_manager import (
    StoreGenerationManager,
    StoreGenerationManagerError,
)
from mdrack.application.metadata_projection import (
    DEFAULT_METADATA_PROJECTION_POLICY,
    MetadataProjectionPolicy,
    metadata_projection_policy_from_config,
)
from mdrack.application.store_generations import (
    ActiveGenerationPointer,
    GenerationContractKind,
    GenerationState,
    assert_pointer_serves_generation,
)
from mdrack.domain.identifiers import logical_id
from mdrack.domain.indexing import PreparedFile, SourceLocator
from mdrack.domain.retrieval import (
    RetrievalCandidate,
    RetrievalItem,
    RetrievalMode,
    RetrievalResult,
)
from mdrack.storage.sqlite.connection import get_read_only_connection
from mdrack.storage.sqlite.migrations import (
    EXPECTED_MIGRATION_MANIFEST_DIGEST,
    EXPECTED_MIGRATION_VERSION,
)
from mdrack_core.application.indexing import CoreIndexingService
from mdrack_core.application.retrieval import RetrievalService as CoreRetrievalService
from mdrack_core.domain import (
    METRIC_COSINE,
    MODALITY_TEXT,
    REPRESENTATION_RETRIEVAL_TEXT,
    RESOURCE_DOCUMENT,
    TARGET_UNIT,
    UNIT_TEXT_CHUNK,
    UNIT_WHOLE_RESOURCE,
    EmbeddingSpaceRecord,
    Locator,
    PreparedResourceBatch,
    RankedCandidate,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchRequest,
    SearchResult,
    SearchUnitRecord,
    VectorRecord,
)
from mdrack_media import AggregationFingerprint, WholeResourceTextPolicy, weighted_centroid

_DOCUMENT_LOCATOR = "document"
_DOCUMENT_SPAN_LOCATOR = "document_span"
_TEXT_BRANCH = "text"
_SEMANTIC_BRANCH = "semantic"
_METADATA_REPRESENTATION = "metadata_text"


def embedding_space_id(profile_name: str, fingerprint: str) -> str:
    """Return the app-owned deterministic identity for one ready text-vector space."""
    return logical_id("embedding-space", profile_name, fingerprint)


def prepared_file_to_resource_batch(
    prepared: PreparedFile,
    *,
    whole_text_policy: WholeResourceTextPolicy | None = None,
    aggregation_fingerprint: AggregationFingerprint | None = None,
    whole_vector: Sequence[float] | None = None,
    metadata_policy: MetadataProjectionPolicy | None = None,
) -> PreparedResourceBatch:
    """Project one fully prepared Markdown document into one complete core graph.

    SQLite row IDs and run IDs deliberately stay on the legacy side of this edge.
    Resource, representation, and unit identities are caller-owned logical IDs.
    """
    if not isinstance(prepared, PreparedFile):
        raise TypeError("prepared must be a PreparedFile")
    if whole_text_policy is not None and not isinstance(whole_text_policy, WholeResourceTextPolicy):
        raise ValueError("whole_text_policy must be a WholeResourceTextPolicy or None")
    if aggregation_fingerprint is not None and not isinstance(aggregation_fingerprint, AggregationFingerprint):
        raise ValueError("aggregation_fingerprint must be an AggregationFingerprint or None")
    if (whole_text_policy is None) != (aggregation_fingerprint is None):
        raise ValueError("whole_text_policy and aggregation_fingerprint must be supplied together")
    if whole_vector is not None and not isinstance(whole_vector, Sequence):
        raise TypeError("whole_vector must be a sequence or None")

    resource_id = prepared.logical_id
    projection = (metadata_policy or DEFAULT_METADATA_PROJECTION_POLICY).project(
        prepared.source_metadata,
        fallback_title=prepared.title,
    )
    representation_id = logical_id(
        "representation",
        resource_id,
        REPRESENTATION_RETRIEVAL_TEXT,
        prepared.parser_name,
        prepared.parser_version,
        prepared.chunk_strategy_name,
        prepared.chunk_strategy_version,
    )
    sections = {section.record_id: section for section in prepared.sections}
    diagnostic_counts = {
        diagnostic.category: diagnostic.count
        for diagnostic in prepared.metadata_diagnostics
    }
    resource = ResourceRecord(
        resource_id=resource_id,
        resource_kind=RESOURCE_DOCUMENT,
        media_type="text/markdown",
        source_namespace=prepared.root_id,
        locator=Locator(
            _DOCUMENT_LOCATOR,
            {
                "document_logical_id": resource_id,
                "root_id": prepared.root_id,
            },
        ),
        content_hash=f"sha256:{prepared.source_hash}",
        title=projection.canonical_title,
        metadata={
            "source": dict(prepared.source_metadata),
            "ingestion": {
                "adapter": "markdown",
                "adapter_version": "1.1",
                "normalizer_version": prepared.metadata_normalizer_version or "legacy",
                "metadata_fingerprint": prepared.metadata_fingerprint or None,
                "normalization_policy_fingerprint": (
                    prepared.metadata_policy_fingerprint or None
                ),
                "projection_policy_fingerprint": projection.policy_fingerprint,
                "parser_name": prepared.parser_name,
                "parser_version": prepared.parser_version,
                "chunk_strategy_name": prepared.chunk_strategy_name,
                "chunk_strategy_version": prepared.chunk_strategy_version,
            },
            "derived": {
                "metadata_key_count": len(prepared.source_metadata),
                "diagnostic_count": sum(diagnostic_counts.values()),
                "diagnostic_categories": tuple(diagnostic_counts),
                "diagnostic_counts": diagnostic_counts,
            },
            # Frozen v0.3 compatibility keys; source values never live here.
            "chunk_strategy_name": prepared.chunk_strategy_name,
            "chunk_strategy_version": prepared.chunk_strategy_version,
            "parser_name": prepared.parser_name,
            "parser_version": prepared.parser_version,
            "relative_path": prepared.relative_path,
        },
    )
    representation = RepresentationRecord(
        representation_id=representation_id,
        resource_id=resource_id,
        representation_kind=REPRESENTATION_RETRIEVAL_TEXT,
        modality=MODALITY_TEXT,
        text="\n\n".join(chunk.embedding_text for chunk in prepared.chunks),
        producer_fingerprint=logical_id(
            "producer",
            prepared.parser_name,
            prepared.parser_version,
            prepared.chunk_strategy_name,
            prepared.chunk_strategy_version,
        ),
        metadata={},
    )
    units = tuple(
        SearchUnitRecord(
            unit_id=chunk.logical_id,
            resource_id=resource_id,
            representation_id=representation_id,
            unit_kind=UNIT_TEXT_CHUNK,
            modality=MODALITY_TEXT,
            text=chunk.content,
            evidence_locator=Locator(
                _DOCUMENT_SPAN_LOCATOR,
                {
                    "block_kind": chunk.block_kind,
                    "block_logical_id": chunk.block_logical_id,
                    "chunk_kind": chunk.chunk_kind,
                    "chunk_logical_id": chunk.logical_id,
                    "end_line": chunk.end_line,
                    "end_offset": chunk.end_offset,
                    "heading_path": chunk.heading_path,
                    "relative_path": prepared.relative_path,
                    "root_id": prepared.root_id,
                    "start_line": chunk.start_line,
                    "start_offset": chunk.start_offset,
                },
            ),
            ordinal=chunk.chunk_index,
            metadata={
                "content_preview": _preview(chunk.content),
                "heading_path": chunk.heading_path,
                "section_title": (
                    sections[chunk.section_record_id].title
                    if chunk.section_record_id in sections
                    else None
                ),
            },
        )
        for chunk in prepared.chunks
    )

    spaces: tuple[EmbeddingSpaceRecord, ...] = ()
    vectors: tuple[VectorRecord, ...] = ()
    if prepared.vectors:
        profile = prepared.embedding_profile
        if profile is None:
            raise ValueError("embedding profile is required when vectors are present")
        if len(prepared.vectors) != len(units):
            raise ValueError("embedding count must match the search-unit count")
        space_id = embedding_space_id(profile.name, profile.fingerprint)
        spaces = (
            EmbeddingSpaceRecord(
                space_id=space_id,
                dimensions=profile.output_dimensions,
                metric=METRIC_COSINE,
                fingerprint=profile.fingerprint,
                metadata={"profile": profile.name},
            ),
        )
        vectors = tuple(
            VectorRecord(unit.unit_id, space_id, vector)
            for unit, vector in zip(units, prepared.vectors, strict=True)
        )

    representations: tuple[RepresentationRecord, ...] = (representation,)
    if projection.lexical_values:
        metadata_text = "\n".join(projection.lexical_values)
        metadata_representation_id = logical_id(
            "representation",
            resource_id,
            _METADATA_REPRESENTATION,
            projection.policy_fingerprint,
        )
        metadata_unit_id = logical_id(
            "whole-resource",
            resource_id,
            metadata_representation_id,
        )
        metadata_token_count = len(metadata_text.split())
        representations = representations + (
            RepresentationRecord(
                representation_id=metadata_representation_id,
                resource_id=resource_id,
                representation_kind=_METADATA_REPRESENTATION,
                modality=MODALITY_TEXT,
                text=metadata_text,
                producer_fingerprint=projection.policy_fingerprint,
                token_count=metadata_token_count,
                token_count_kind="estimated",
                metadata={"projection_policy_fingerprint": projection.policy_fingerprint},
            ),
        )
        units = units + (
            SearchUnitRecord(
                unit_id=metadata_unit_id,
                resource_id=resource_id,
                representation_id=metadata_representation_id,
                unit_kind=UNIT_WHOLE_RESOURCE,
                modality=MODALITY_TEXT,
                text=metadata_text,
                evidence_locator=Locator(
                    "whole_resource",
                    {"relative_path": prepared.relative_path, "root_id": prepared.root_id},
                ),
                ordinal=0,
                token_count=metadata_token_count,
                token_count_kind="estimated",
                metadata={"projection_policy_fingerprint": projection.policy_fingerprint},
            ),
        )
    if whole_text_policy is not None:
        assert aggregation_fingerprint is not None
        token_weights = {
            chunk.logical_id: max(1, len(chunk.embedding_text.split()))
            for chunk in prepared.chunks
        }
        total_tokens = sum(token_weights.values())
        is_long = total_tokens > whole_text_policy.max_tokens
        if is_long and whole_text_policy.overflow == "reject":
            raise ValueError("Markdown whole-resource text exceeds whole_text_policy.max_tokens")
        whole_representation_id = logical_id(
            "representation",
            resource_id,
            "whole_resource_text",
            aggregation_fingerprint.value,
            representation_id,
        )
        whole_unit_id = logical_id(
            "whole-resource",
            resource_id,
            representation_id,
            aggregation_fingerprint.value,
        )
        whole_representation = RepresentationRecord(
            representation_id=whole_representation_id,
            resource_id=resource_id,
            representation_kind="whole_resource_text",
            modality=MODALITY_TEXT,
            text=representation.text,
            producer_fingerprint=aggregation_fingerprint.value,
            token_count=total_tokens,
            token_count_kind="estimated",
            metadata={
                "aggregation_fingerprint": aggregation_fingerprint.value,
                "similarity_basis": "markdown_retrieval_text",
            },
        )
        whole_unit = SearchUnitRecord(
            unit_id=whole_unit_id,
            resource_id=resource_id,
            representation_id=whole_representation_id,
            unit_kind=UNIT_WHOLE_RESOURCE,
            modality=MODALITY_TEXT,
            text=representation.text,
            evidence_locator=Locator(
                "whole_resource",
                {"relative_path": prepared.relative_path, "root_id": prepared.root_id},
            ),
            ordinal=0,
            token_count=total_tokens,
            token_count_kind="estimated",
            metadata={
                "aggregation_fingerprint": aggregation_fingerprint.value,
                "similarity_basis": "markdown_retrieval_text",
            },
        )
        units = units + (whole_unit,)
        if is_long:
            if not prepared.vectors:
                raise ValueError("long Markdown whole-resource text requires chunk vectors")
            whole_vector = weighted_centroid(
                {
                    chunk.logical_id: vector
                    for chunk, vector in zip(prepared.chunks, prepared.vectors, strict=True)
                },
                token_weights,
            )
        if whole_vector is not None:
            if prepared.embedding_profile is None:
                raise ValueError("embedding profile is required for whole-resource vectors")
            if not whole_vector:
                raise ValueError("whole_vector must be a non-empty vector")
            if spaces:
                space = spaces[0]
                if len(whole_vector) != space.dimensions:
                    raise ValueError("whole_vector must match the chunk vector dimensions")
                vectors = vectors + (VectorRecord(whole_unit_id, space.space_id, tuple(whole_vector)),)
            else:
                space_id = embedding_space_id(
                    prepared.embedding_profile.name,
                    prepared.embedding_profile.fingerprint,
                )
                spaces = (
                    EmbeddingSpaceRecord(
                        space_id=space_id,
                        dimensions=prepared.embedding_profile.output_dimensions,
                        metric=METRIC_COSINE,
                        fingerprint=prepared.embedding_profile.fingerprint,
                        metadata={"profile": prepared.embedding_profile.name},
                    ),
                )
                if len(whole_vector) != spaces[0].dimensions:
                    raise ValueError("whole_vector must match the embedding profile dimensions")
                vectors = (VectorRecord(whole_unit_id, space_id, tuple(whole_vector)),)
        representations = representations + (whole_representation,)

    return PreparedResourceBatch(
        resource=resource,
        representations=representations,
        units=units,
        spaces=spaces,
        vectors=vectors,
        facets=tuple(
            ResourceFacet(
                resource_id,
                facet,
                "source",
                producer_fingerprint=projection.policy_fingerprint,
            )
            for facet in projection.facets
        ),
    )


class CoreCompatibilityMapper:
    """The sole legacy result/locator projection for core-backed app retrieval."""

    def retrieval_result(
        self,
        *,
        query: str,
        mode: RetrievalMode,
        result: SearchResult,
        offset: int = 0,
        limit: int | None = None,
        degraded_reason: str | None = None,
    ) -> RetrievalResult:
        if result.target != TARGET_UNIT:
            raise ValueError("legacy document retrieval requires unit-target results")
        selected = result.items[offset : None if limit is None else offset + limit]
        items = tuple(self._item(mode, item) for item in selected)
        core_reason = result.degradations[0].category.value if result.degradations else None
        reason = degraded_reason or core_reason
        return RetrievalResult(
            query=query,
            mode=mode,
            results=items,
            total_count=len(items),
            degraded=reason is not None,
            degraded_reason=reason,
        )

    def _item(self, mode: RetrievalMode, item: Any) -> RetrievalItem:
        evidence = tuple(item.evidence)
        representative = evidence[0] if evidence else None
        if representative is None:
            raise ValueError("core compatibility result requires evidence")
        text = self._branch(evidence, _TEXT_BRANCH)
        semantic = self._branch(evidence, _SEMANTIC_BRANCH)
        metadata = dict((text or representative).metadata)
        score = representative.raw_score if mode in {_TEXT_BRANCH, _SEMANTIC_BRANCH} else item.score
        return RetrievalItem(
            logical_id=item.unit_id or item.logical_id,
            score=score,
            source_locator=self.source_locator(representative.evidence_locator),
            content_preview=str(metadata.get("content_preview") or ""),
            text_rank=text.rank if text is not None else None,
            semantic_rank=semantic.rank if semantic is not None else None,
            rrf_rank=item.rank if mode == "hybrid" else None,
            rrf_score=item.score if mode == "hybrid" else None,
            text_score=text.raw_score if text is not None else None,
            semantic_score=semantic.raw_score if semantic is not None else None,
            metadata={
                "heading_path": metadata.get("heading_path"),
                "section_title": metadata.get("section_title"),
            },
        )

    @staticmethod
    def _branch(
        evidence: tuple[RankedCandidate, ...],
        branch_id: str,
    ) -> RankedCandidate | None:
        return next((candidate for candidate in evidence if candidate.branch_id == branch_id), None)

    @staticmethod
    def source_locator(locator: Locator) -> SourceLocator:
        if locator.kind != _DOCUMENT_SPAN_LOCATOR:
            raise ValueError("core locator is not representable as a legacy document locator")
        payload: Mapping[str, object] = cast(Mapping[str, object], locator.payload)
        heading = payload.get("heading_path", ())
        if not isinstance(heading, tuple) or any(not isinstance(item, str) for item in heading):
            raise ValueError("document heading_path is invalid")
        return SourceLocator(
            root_id=_required_string(payload, "root_id"),
            relative_path=_required_string(payload, "relative_path"),
            start_line=_required_int(payload, "start_line"),
            end_line=_required_int(payload, "end_line"),
            heading_path=heading,
            block_id=_required_string(payload, "block_logical_id"),
            chunk_id=_required_string(payload, "chunk_logical_id"),
            start_offset=_optional_int(payload, "start_offset"),
            end_offset=_optional_int(payload, "end_offset"),
            block_kind=_required_string(payload, "block_kind"),
            chunk_kind=_required_string(payload, "chunk_kind"),
        )

    @staticmethod
    def core_locator(locator: SourceLocator) -> Locator:
        """Project an app document locator into the generic core evidence locator."""
        return Locator(
            _DOCUMENT_SPAN_LOCATOR,
            {
                "block_kind": locator.block_kind,
                "block_logical_id": locator.block_id,
                "chunk_kind": locator.chunk_kind,
                "chunk_logical_id": locator.chunk_id,
                "end_line": locator.end_line,
                "end_offset": locator.end_offset,
                "heading_path": locator.heading_path,
                "relative_path": locator.relative_path,
                "root_id": locator.root_id,
                "start_line": locator.start_line,
                "start_offset": locator.start_offset,
            },
        )


def _preview(content: str) -> str:
    return content[:200] + ("..." if len(content) > 200 else "")


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"document locator {key} is invalid")
    return value


def _required_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int:
        raise ValueError(f"document locator {key} is invalid")
    return value


def _optional_int(payload: Mapping[str, object], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError(f"document locator {key} is invalid")
    return value


class CoreCompatibilityStorage:
    """One active-generation composition for core writes/search and legacy reads."""

    def __init__(
        self,
        connection: _AtomicProjectionConnection,
        *,
        metadata_policy: MetadataProjectionPolicy | None = None,
    ) -> None:
        self.connection = connection
        self.legacy = SQLiteIndexStorage(connection)
        self.resource_store = SQLiteResourceStore(connection)
        self.core_indexing = CoreIndexingService(self.resource_store)
        self.core_retrieval = CoreRetrievalService(self.resource_store)
        self.metadata_policy = metadata_policy or DEFAULT_METADATA_PROJECTION_POLICY
        self._closed = False

    def start_run(self, **kwargs: Any) -> str:
        return self.legacy.start_run(**kwargs)

    def plan_changes(self, scanned: list[Path], root: Path) -> Any:
        return self.legacy.plan_changes(scanned, root)

    def get_file_by_path(self, relative_path: str) -> dict[str, Any] | None:
        return self.legacy.get_file_by_path(relative_path)

    def get_public_file_by_path(self, relative_path: str) -> dict[str, Any] | None:
        return self.legacy.get_public_file_by_path(relative_path)

    def find_rename_source(
        self,
        deleted_paths: list[str],
        source_hash: str,
    ) -> dict[str, Any] | None:
        return self.legacy.find_rename_source(deleted_paths, source_hash)

    def replace_file(self, prepared: PreparedFile) -> None:
        with self.connection.atomic_projection():
            self.core_indexing.index(
                prepared_file_to_resource_batch(prepared, metadata_policy=self.metadata_policy)
            )
            self.legacy.replace_file(prepared)

    def delete_file(self, relative_path: str) -> None:
        current = self.legacy.get_file_by_path(relative_path)
        with self.connection.atomic_projection():
            if current is not None:
                logical_id_value = current.get("logical_id")
                if isinstance(logical_id_value, str) and logical_id_value:
                    self.core_indexing.delete(logical_id_value)
            self.legacy.delete_file(relative_path)

    def record_error(self, run_id: str, code: str, *, file_ref: str) -> None:
        self.legacy.record_error(run_id, code, file_ref=file_ref)

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        stats: dict[str, int],
        error_codes: Sequence[str],
    ) -> None:
        self.legacy.finish_run(run_id, status=status, stats=stats, error_codes=error_codes)

    def search_core(self, request: SearchRequest) -> SearchResult:
        result = self.core_retrieval.search(request)
        if not request.lexical_branches:
            return result
        legacy_by_branch = {
            branch.branch_id: {
                candidate.logical_id: candidate
                for candidate in self.legacy.retrieve_text_candidates(
                    branch.query,
                    limit=branch.candidate_limit,
                )
            }
            for branch in request.lexical_branches
        }
        return replace(
            result,
            items=tuple(
                replace(
                    item,
                    evidence=tuple(
                        _restore_legacy_lexical_candidate(candidate, legacy_by_branch)
                        for candidate in item.evidence
                    ),
                )
                for item in result.items
            ),
        )

    def rebuild_fts_index(self) -> tuple[int, int]:
        """Rebuild the active core FTS projection and return FTS/unit counts."""
        with self.connection.atomic_projection():
            self.connection.execute("DELETE FROM core_search_units_fts")
            self.connection.execute(
                "INSERT INTO core_search_units_fts(unit_id, content) "
                "SELECT unit_id, text_content FROM core_search_units "
                "WHERE text_content IS NOT NULL AND trim(text_content) <> ''"
            )
        fts_count = int(
            self.connection.execute("SELECT COUNT(*) FROM core_search_units_fts").fetchone()[0]
        )
        unit_count = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM core_search_units "
                "WHERE text_content IS NOT NULL AND trim(text_content) <> ''"
            ).fetchone()[0]
        )
        return fts_count, unit_count

    def resolve_embedding_space(
        self,
        profile: str,
        profile_fingerprint: str | None,
    ) -> str | None:
        del profile
        if profile_fingerprint is not None:
            rows = self.connection.execute(
                "SELECT space_id FROM core_embedding_spaces WHERE fingerprint=? ORDER BY space_id",
                (profile_fingerprint,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT space_id FROM core_embedding_spaces ORDER BY space_id"
            ).fetchall()
        return str(rows[0][0]) if len(rows) == 1 else None

    def retrieve_text_candidates(
        self,
        query: str,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[RetrievalCandidate]:
        return self.legacy.retrieve_text_candidates(query, limit=limit, offset=offset)

    def retrieve_semantic_candidates(
        self,
        query_vector: list[float],
        *,
        profile: str,
        profile_fingerprint: str | None,
        limit: int,
    ) -> list[RetrievalCandidate]:
        return self.legacy.retrieve_semantic_candidates(
            query_vector,
            profile=profile,
            profile_fingerprint=profile_fingerprint,
            limit=limit,
        )

    def search_text(self, query: str, *, limit: int, offset: int = 0) -> Any:
        return self.legacy.search_text(query, limit=limit, offset=offset)

    def get_chunk_source_locator(self, chunk_id: str) -> SourceLocator:
        return self.legacy.get_chunk_source_locator(chunk_id)

    def get_chunk_by_logical_id(self, logical_id_value: str) -> dict[str, Any] | None:
        return self.legacy.get_chunk_by_logical_id(logical_id_value)

    def close(self) -> None:
        if not self._closed:
            self.connection.close()
            self._closed = True


def create_application_storage(root: Path, config: Any) -> SQLiteIndexStorage | CoreCompatibilityStorage:
    """Open the legacy store or its fail-closed active resource generation."""
    database_path, contract_kind = _resolve_application_database(root, config)
    if contract_kind is None:
        return create_sqlite_index_storage(root.resolve(), config)
    if contract_kind is GenerationContractKind.LEGACY_V0_2:
        return SQLiteIndexStorage(
            get_read_only_connection(database_path),
            owns_connection=True,
        )
    return CoreCompatibilityStorage(
        _get_atomic_projection_connection(database_path),
        metadata_policy=metadata_projection_policy_from_config(config.metadata),
    )


def resolve_application_database_path(root: Path, config: Any) -> Path:
    """Resolve the verified database used by normal application composition."""
    return _resolve_application_database(root, config)[0]


def _resolve_application_database(
    root: Path,
    config: Any,
) -> tuple[Path, GenerationContractKind | None]:
    resolved_root = root.resolve()
    configured = Path(config.paths.store)
    store_dir = configured if configured.is_absolute() else resolved_root / configured
    pointer_path = store_dir / "active-generation.json"
    if not pointer_path.exists():
        return store_dir / "knowledge.db", None

    manager = StoreGenerationManager(store_dir, runtime=SQLiteGenerationRuntime())
    pointer, generation, database_path = manager.resolve_active()
    if pointer.contract_kind is GenerationContractKind.LEGACY_V0_2:
        return database_path, pointer.contract_kind
    if (
        generation.contract_kind is not GenerationContractKind.RESOURCE_CORE_V1
        or generation.state is not GenerationState.READY
    ):
        raise StoreGenerationManagerError("active_generation_not_ready")
    return database_path, pointer.contract_kind


def create_active_generation_rebuild_storage(root: Path, config: Any) -> CoreCompatibilityStorage:
    """Open the active ready core generation for an explicit index repair.

    Pointer and metadata identity remain fail-closed, while the full runtime FTS
    verification is intentionally deferred to the rebuild operation itself.
    """
    resolved_root = root.resolve()
    configured = Path(config.paths.store)
    store_dir = configured if configured.is_absolute() else resolved_root / configured
    manager = StoreGenerationManager(store_dir, runtime=SQLiteGenerationRuntime())
    try:
        pointer = ActiveGenerationPointer.from_bytes(manager.pointer_path.read_bytes())
        generation = manager.load_generation(pointer.generation_id)
        assert_pointer_serves_generation(
            pointer,
            generation,
            expected_manifest_digest=EXPECTED_MIGRATION_MANIFEST_DIGEST,
            expected_schema_version=EXPECTED_MIGRATION_VERSION,
        )
    except Exception as exc:
        if isinstance(exc, StoreGenerationManagerError):
            raise
        raise StoreGenerationManagerError("active_generation_invalid") from exc
    if pointer.contract_kind is not GenerationContractKind.RESOURCE_CORE_V1:
        raise StoreGenerationManagerError("active_generation_not_core")
    database_path = manager.database_path(pointer.generation_id)
    if not database_path.is_file():
        raise StoreGenerationManagerError("active_generation_invalid")
    connection = _get_atomic_projection_connection(database_path)
    try:
        versions = [
            str(row[0])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        expected = [f"{index:04d}" for index in range(int(EXPECTED_MIGRATION_VERSION) + 1)]
        required = {"core_search_units", "core_search_units_fts"}
        objects = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        if versions != expected or not required <= objects:
            raise StoreGenerationManagerError("active_generation_invalid")
    except Exception as exc:
        connection.close()
        if isinstance(exc, StoreGenerationManagerError):
            raise
        raise StoreGenerationManagerError("active_generation_invalid") from exc
    return CoreCompatibilityStorage(
        connection,
        metadata_policy=metadata_projection_policy_from_config(config.metadata),
    )


class _AtomicProjectionConnection(sqlite3.Connection):
    """Defer adapter commits so core and legacy projections publish together."""

    _projection_active = False

    def commit(self) -> None:
        if not self._projection_active:
            super().commit()

    def __enter__(self) -> _AtomicProjectionConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        if self._projection_active:
            if exc_type is not None:
                super().rollback()
            return False
        return super().__exit__(exc_type, exc, traceback)

    @contextmanager
    def atomic_projection(self) -> Iterator[None]:
        if self._projection_active or self.in_transaction:
            raise RuntimeError("compatibility projection transaction is already active")
        self._projection_active = True
        try:
            yield
        except Exception:
            super().rollback()
            raise
        else:
            super().commit()
        finally:
            self._projection_active = False


def _get_atomic_projection_connection(database_path: Path) -> _AtomicProjectionConnection:
    connection = sqlite3.connect(str(database_path), factory=_AtomicProjectionConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _restore_legacy_lexical_candidate(
    candidate: RankedCandidate,
    legacy_by_branch: Mapping[str, Mapping[str, RetrievalCandidate]],
) -> RankedCandidate:
    legacy = legacy_by_branch.get(candidate.branch_id, {}).get(candidate.unit_id)
    if legacy is None:
        return candidate
    return replace(
        candidate,
        raw_score=legacy.score,
        metadata={
            **dict(candidate.metadata),
            "content_preview": legacy.content_preview,
            "heading_path": legacy.source_locator.heading_path,
            "section_title": legacy.metadata.get("section_title"),
        },
    )


__all__ = [
    "CoreCompatibilityMapper",
    "CoreCompatibilityStorage",
    "StoreGenerationManagerError",
    "create_active_generation_rebuild_storage",
    "create_application_storage",
    "embedding_space_id",
    "prepared_file_to_resource_batch",
    "resolve_application_database_path",
]
