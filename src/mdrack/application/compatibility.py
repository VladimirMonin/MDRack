"""Application-owned projection over the fixed resource-core catalog."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from mdrack.adapters.sqlite.canonical_catalog import (
    ApplicationStoreError,
    canonical_catalog_path,
    open_application_catalog,
)
from mdrack.adapters.sqlite.resource_store import SQLiteResourceStore
from mdrack.application.metadata_projection import (
    DEFAULT_METADATA_PROJECTION_POLICY,
    MetadataProjectionPolicy,
    metadata_projection_policy_from_config,
)
from mdrack.application.resources import TextualWholeResourceProjection
from mdrack.application.textual_embedding_space import CanonicalTextEmbeddingSpace, embedding_space_id
from mdrack.application.vector_values import (
    apply_vector_value_policy,
    canonicalize_for_value_policy,
    value_policy_from_space_metadata,
)
from mdrack.domain.identifiers import logical_id
from mdrack.domain.indexing import PreparedFile, SourceLocator
from mdrack.domain.retrieval import (
    RetrievalCandidate,
    RetrievalItem,
    RetrievalMode,
    RetrievalResult,
)
from mdrack.indexing.change_detector import ChangePlan, compute_file_hash
from mdrack.search.text import TextSearchItem, TextSearchResult
from mdrack_core.application.indexing import CoreIndexingService
from mdrack_core.application.retrieval import RetrievalService as CoreRetrievalService
from mdrack_core.domain import (
    MODALITY_TEXT,
    REPRESENTATION_RETRIEVAL_TEXT,
    RESOURCE_DOCUMENT,
    TARGET_UNIT,
    UNIT_TEXT_CHUNK,
    UNIT_WHOLE_RESOURCE,
    BranchScopeOverride,
    EmbeddingSpaceRecord,
    LexicalBranch,
    Locator,
    PreparedResourceBatch,
    RankedCandidate,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchRequest,
    SearchResult,
    SearchScope,
    SearchUnitRecord,
    VectorBranch,
    VectorRecord,
)
from mdrack_media import AggregationFingerprint, WholeResourceTextPolicy, weighted_centroid

_DOCUMENT_LOCATOR = "document"
_DOCUMENT_SPAN_LOCATOR = "document_span"
_TEXT_BRANCH = "text"
_SEMANTIC_BRANCH = "semantic"
_METADATA_REPRESENTATION = "metadata_text"
_DEFAULT_MARKDOWN_WHOLE_TEXT_POLICY = WholeResourceTextPolicy(overflow="caller_split")
_DIRECT_TEXT_AGGREGATION = "direct_text_v1"
_CENTROID_TEXT_AGGREGATION = "token_weighted_centroid_v1"


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

    default_whole_projection = whole_text_policy is None
    if default_whole_projection:
        whole_text_policy = _DEFAULT_MARKDOWN_WHOLE_TEXT_POLICY
        aggregation_fingerprint = AggregationFingerprint.from_payload(
            {
                "aggregation": _CENTROID_TEXT_AGGREGATION,
                "contract": "mdrack.markdown.whole-resource.v1",
                "policy": whole_text_policy.to_dict(),
            }
        )

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
            "source": cast(dict[str, Any], dict(prepared.source_metadata)),
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
            None if prepared.embedding_profile is None else prepared.embedding_profile.fingerprint,
            None if prepared.embedding_profile is None else prepared.embedding_profile.vector_value_policy,
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
        space = CanonicalTextEmbeddingSpace(profile)
        spaces = (space.record,)
        vectors = tuple(
            VectorRecord(unit.unit_id, space.space_id, vector)
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
        aggregation = (
            _DIRECT_TEXT_AGGREGATION
            if whole_vector is not None and not is_long
            else _CENTROID_TEXT_AGGREGATION
        )
        whole_representation_id = logical_id(
            "representation",
            resource_id,
            "whole_resource_text",
            aggregation_fingerprint.value,
            aggregation,
            representation_id,
        )
        whole_unit_id = logical_id(
            "whole-resource",
            resource_id,
            representation_id,
            aggregation_fingerprint.value,
            aggregation,
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
                "aggregation": aggregation,
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
                "aggregation": aggregation,
                "aggregation_fingerprint": aggregation_fingerprint.value,
                "similarity_basis": "markdown_retrieval_text",
            },
        )
        units = units + (whole_unit,)
        if whole_vector is None and prepared.vectors:
            whole_vector = weighted_centroid(
                {
                    chunk.logical_id: vector
                    for chunk, vector in zip(prepared.chunks, prepared.vectors, strict=True)
                },
                token_weights,
            )
        elif is_long and not default_whole_projection and not prepared.vectors:
            raise ValueError("long Markdown whole-resource text requires chunk vectors")
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
                space = CanonicalTextEmbeddingSpace(prepared.embedding_profile)
                spaces = (space.record,)
                if len(whole_vector) != spaces[0].dimensions:
                    raise ValueError("whole_vector must match the embedding profile dimensions")
                vectors = (VectorRecord(whole_unit_id, space.space_id, tuple(whole_vector)),)
        representations = representations + (whole_representation,)

    batch = PreparedResourceBatch(
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
    return apply_vector_value_policy(
        batch,
        None if prepared.embedding_profile is None else prepared.embedding_profile.vector_value_policy,
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
    """Compatibility surface projected solely over the fixed clean catalog."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        metadata_policy: MetadataProjectionPolicy | None = None,
    ) -> None:
        self.connection = connection
        self.resource_store = SQLiteResourceStore(connection)
        self.core_indexing = CoreIndexingService(self.resource_store)
        self.core_retrieval = CoreRetrievalService(self.resource_store)
        self.metadata_policy = metadata_policy or DEFAULT_METADATA_PROJECTION_POLICY
        self._closed = False

    def start_run(self, **kwargs: Any) -> str:
        del kwargs
        return str(uuid.uuid4())

    def plan_changes(self, scanned: list[Path], root: Path) -> Any:
        indexed = self._document_records()
        seen: set[str] = set()
        plan = ChangePlan()
        for path in scanned:
            relative_path = path.as_posix()
            seen.add(relative_path)
            try:
                source_hash = compute_file_hash(root / path)
            except (OSError, UnicodeError):
                source_hash = ""
            existing = indexed.get(relative_path)
            if existing is None:
                plan.new_files.append(path)
            elif existing["source_hash"] == source_hash:
                plan.unchanged_files.append(path)
            else:
                plan.changed_files.append(path)
        plan.deleted_files.extend(sorted(set(indexed) - seen))
        return plan

    def get_file_by_path(self, relative_path: str) -> dict[str, Any] | None:
        return self._document_records().get(relative_path)

    def get_public_file_by_path(self, relative_path: str) -> dict[str, Any] | None:
        return self.get_file_by_path(relative_path)

    def get_public_file_by_logical_id(self, logical_id_value: str) -> dict[str, Any] | None:
        """Return one document record by its portable resource identity."""
        if not isinstance(logical_id_value, str) or not logical_id_value:
            raise ValueError("logical_id_value must be non-empty")
        return next(
            (
                record
                for record in self._document_records().values()
                if record["logical_id"] == logical_id_value
            ),
            None,
        )

    def get_file_outline(self, logical_id_value: str) -> dict[str, Any] | None:
        """Return canonical document headings without legacy section identifiers."""
        if self.get_public_file_by_logical_id(logical_id_value) is None:
            return None
        rows = self.connection.execute(
            "SELECT evidence_locator_json FROM core_search_units "
            "WHERE resource_id=? AND unit_kind=? ORDER BY ordinal,unit_id",
            (logical_id_value, UNIT_TEXT_CHUNK),
        ).fetchall()
        headings: list[dict[str, object]] = []
        seen: set[tuple[str, ...]] = set()
        for row in rows:
            locator = json.loads(str(row["evidence_locator_json"]))
            if not isinstance(locator, Mapping):
                continue
            raw_path = locator.get("heading_path")
            if not isinstance(raw_path, list) or not all(isinstance(item, str) for item in raw_path):
                continue
            heading_path = tuple(raw_path)
            if not heading_path or heading_path in seen:
                continue
            seen.add(heading_path)
            start_line = locator.get("start_line")
            end_line = locator.get("end_line")
            headings.append(
                {
                    "logical_id": logical_id(
                        "heading",
                        logical_id_value,
                        heading_path,
                        start_line,
                        end_line,
                    ),
                    "title": heading_path[-1],
                    "heading_path": list(heading_path),
                    "start_line": start_line if type(start_line) is int else None,
                    "end_line": end_line if type(end_line) is int else None,
                }
            )
        return {"file_logical_id": logical_id_value, "headings": headings}

    def list_public_files(self, *, offset: int, limit: int) -> tuple[dict[str, Any], ...]:
        """List document records from the fixed catalog in stable path order."""
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if limit <= 0:
            raise ValueError("limit must be positive")
        records = sorted(self._document_records().values(), key=lambda record: str(record["relative_path"]))
        return tuple(records[offset : offset + limit])

    def count_public_files(self) -> int:
        """Return the number of document resources in the fixed catalog."""
        return len(self._document_records())

    def _document_records(self) -> dict[str, dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT resource_id,source_namespace,content_hash,title,metadata_json,indexed_at "
            "FROM core_resources WHERE resource_kind=? ORDER BY resource_id",
            (RESOURCE_DOCUMENT,),
        ).fetchall()
        records: dict[str, dict[str, Any]] = {}
        for row in rows:
            metadata = json.loads(str(row["metadata_json"]))
            if not isinstance(metadata, Mapping):
                raise ValueError("document resource metadata is invalid")
            relative_path = metadata.get("relative_path")
            if not isinstance(relative_path, str) or not relative_path:
                raise ValueError("document resource relative_path is invalid")
            ingestion = metadata.get("ingestion")
            ingestion_values = ingestion if isinstance(ingestion, Mapping) else {}
            resource_id = str(row["resource_id"])
            content_hash = row["content_hash"]
            if not isinstance(content_hash, str) or not content_hash:
                raise ValueError("document resource content_hash is invalid")
            records[relative_path] = {
                "id": resource_id,
                "logical_id": resource_id,
                "root_id": str(row["source_namespace"]),
                "relative_path": relative_path,
                "title": row["title"],
                "source_hash": content_hash.removeprefix("sha256:"),
                "indexed_at": str(row["indexed_at"]),
                "status": "active",
                "parser_name": ingestion_values.get("parser_name"),
                "parser_version": ingestion_values.get("parser_version"),
                "chunk_strategy_name": ingestion_values.get("chunk_strategy_name"),
                "chunk_strategy_version": ingestion_values.get("chunk_strategy_version"),
            }
        return records

    def find_rename_source(
        self,
        deleted_paths: list[str],
        source_hash: str,
    ) -> dict[str, Any] | None:
        matches = [
            record
            for path, record in self._document_records().items()
            if path in deleted_paths and record["source_hash"] == source_hash
        ]
        return matches[0] if len(matches) == 1 else None

    def replace_file(self, prepared: PreparedFile) -> None:
        self.core_indexing.index(
            prepared_file_to_resource_batch(prepared, metadata_policy=self.metadata_policy)
        )

    def resolve_textual_whole_resource_units(
        self,
        resource_id: str,
    ) -> tuple[TextualWholeResourceProjection, ...]:
        """Resolve persisted whole-text identities without exposing SQLite record IDs."""
        if not isinstance(resource_id, str) or not resource_id:
            raise ValueError("resource_id must be non-empty")
        rows = self.connection.execute(
            "SELECT u.resource_id,u.unit_id,s.space_id,s.dimensions,s.metric,s.fingerprint "
            "FROM core_search_units u "
            "JOIN core_unit_embeddings e ON e.unit_id=u.unit_id "
            "JOIN core_embedding_spaces s ON s.space_id=e.space_id "
            "WHERE u.resource_id=? AND u.unit_kind=? AND u.modality=? "
            "ORDER BY u.unit_id,s.space_id",
            (resource_id, UNIT_WHOLE_RESOURCE, MODALITY_TEXT),
        ).fetchall()
        return tuple(
            TextualWholeResourceProjection(
                str(row["resource_id"]),
                str(row["unit_id"]),
                EmbeddingSpaceRecord(
                    str(row["space_id"]),
                    int(row["dimensions"]),
                    str(row["metric"]),
                    str(row["fingerprint"]),
                    {},
                ),
            )
            for row in rows
        )

    def delete_file(self, relative_path: str) -> None:
        current = self.get_file_by_path(relative_path)
        if current is not None:
            self.core_indexing.delete(str(current["logical_id"]))

    def record_error(self, run_id: str, code: str, *, file_ref: str) -> None:
        del run_id, code, file_ref

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        stats: dict[str, int],
        error_codes: Sequence[str],
    ) -> None:
        del run_id, status, stats, error_codes

    def search_core(self, request: SearchRequest) -> SearchResult:
        return self.core_retrieval.search(request)

    def rebuild_fts_index(self) -> tuple[int, int]:
        """Rebuild the active core FTS projection and return FTS/unit counts."""
        with self.connection:
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

    def canonicalize_vector_for_space(
        self,
        space_id: str,
        vector: Sequence[float],
    ) -> tuple[float, ...]:
        row = self.connection.execute(
            "SELECT metadata_json FROM core_embedding_spaces WHERE space_id=?",
            (space_id,),
        ).fetchone()
        if row is None:
            raise ValueError("embedding space is unavailable")
        metadata = json.loads(str(row[0]))
        if not isinstance(metadata, Mapping):
            raise ValueError("embedding space metadata is invalid")
        return canonicalize_for_value_policy(
            vector,
            value_policy_from_space_metadata(metadata),
        )

    def retrieve_text_candidates(
        self,
        query: str,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[RetrievalCandidate]:
        result = self.search_core(
            SearchRequest(
                lexical_branches=(
                    LexicalBranch(
                        _TEXT_BRANCH,
                        query,
                        candidate_limit=limit + offset,
                        scope_override=BranchScopeOverride(
                            representation_kinds=(REPRESENTATION_RETRIEVAL_TEXT,),
                            unit_kinds=(UNIT_TEXT_CHUNK,),
                        ),
                    ),
                ),
                vector_branches=(),
                scope=SearchScope(),
                target=TARGET_UNIT,
                limit=limit + offset,
            )
        )
        return self._retrieval_candidates(result, query=query, mode="text", limit=limit, offset=offset)

    def retrieve_semantic_candidates(
        self,
        query_vector: list[float],
        *,
        profile: str,
        profile_fingerprint: str | None,
        limit: int,
    ) -> list[RetrievalCandidate]:
        space_id = self.resolve_embedding_space(profile, profile_fingerprint)
        if space_id is None:
            return []
        result = self.search_core(
            SearchRequest(
                lexical_branches=(),
                vector_branches=(
                    VectorBranch(
                        _SEMANTIC_BRANCH,
                        space_id,
                        tuple(query_vector),
                        candidate_limit=limit,
                        expected_fingerprint=profile_fingerprint,
                    ),
                ),
                scope=SearchScope(
                    representation_kinds=(REPRESENTATION_RETRIEVAL_TEXT,),
                    unit_kinds=(UNIT_TEXT_CHUNK,),
                ),
                target=TARGET_UNIT,
                limit=limit,
            )
        )
        return self._retrieval_candidates(result, query="", mode="semantic", limit=limit)

    def search_text(self, query: str, *, limit: int, offset: int = 0) -> Any:
        candidates = self.retrieve_text_candidates(query, limit=limit, offset=offset)
        return TextSearchResult(
            query=query,
            results=[
                TextSearchItem(
                    chunk_id=candidate.logical_id,
                    score=candidate.score,
                    snippet=candidate.content_preview,
                    file_relative_path=candidate.source_locator.relative_path,
                    section_title=candidate.metadata.get("section_title"),
                    heading_path=json.dumps(candidate.source_locator.heading_path),
                    source_locator=candidate.source_locator,
                )
                for candidate in candidates
            ],
            total_count=len(candidates),
        )

    def get_chunk_source_locator(self, chunk_id: str) -> SourceLocator:
        unit = self.resource_store.read_unit(chunk_id)
        if unit is None:
            raise KeyError(chunk_id)
        return CoreCompatibilityMapper.source_locator(unit.evidence_locator)

    def get_chunk_by_logical_id(self, logical_id_value: str) -> dict[str, Any] | None:
        unit = self.resource_store.read_unit(logical_id_value)
        if unit is None:
            return None
        locator = CoreCompatibilityMapper.source_locator(unit.evidence_locator)
        return {
            "id": unit.unit_id,
            "logical_id": unit.unit_id,
            "content": unit.text,
            "content_type": unit.unit_kind,
            "chunk_index": unit.ordinal,
            "heading_path": list(locator.heading_path),
            "embedding_text_hash": None,
            "source_locator": locator.to_dict(),
        }

    def get_chunk_neighbors(self, logical_id_value: str, *, count: int = 1) -> tuple[dict[str, Any], ...]:
        """Return adjacent text chunks without using legacy linked-list tables."""
        if not isinstance(logical_id_value, str) or not logical_id_value:
            raise ValueError("logical_id_value must be non-empty")
        if count <= 0:
            raise ValueError("count must be positive")
        unit = self.resource_store.read_unit(logical_id_value)
        if unit is None or unit.unit_kind != UNIT_TEXT_CHUNK:
            return ()
        previous_rows = self.connection.execute(
            "SELECT unit_id FROM core_search_units "
            "WHERE resource_id=? AND representation_id=? AND unit_kind=? AND ordinal<? "
            "ORDER BY ordinal DESC,unit_id DESC LIMIT ?",
            (unit.resource_id, unit.representation_id, UNIT_TEXT_CHUNK, unit.ordinal, count),
        ).fetchall()
        next_rows = self.connection.execute(
            "SELECT unit_id FROM core_search_units "
            "WHERE resource_id=? AND representation_id=? AND unit_kind=? AND ordinal>? "
            "ORDER BY ordinal ASC,unit_id ASC LIMIT ?",
            (unit.resource_id, unit.representation_id, UNIT_TEXT_CHUNK, unit.ordinal, count),
        ).fetchall()
        neighbor_ids = [str(row["unit_id"]) for row in reversed(previous_rows)]
        neighbor_ids.extend(str(row["unit_id"]) for row in next_rows)
        return tuple(
            chunk
            for neighbor_id in neighbor_ids
            if (chunk := self.get_chunk_by_logical_id(neighbor_id)) is not None
        )

    @staticmethod
    def _retrieval_candidates(
        result: SearchResult,
        *,
        query: str,
        mode: RetrievalMode,
        limit: int,
        offset: int = 0,
    ) -> list[RetrievalCandidate]:
        mapped = CoreCompatibilityMapper().retrieval_result(
            query=query,
            mode=mode,
            result=result,
            limit=limit,
            offset=offset,
        )
        return [
            RetrievalCandidate(
                logical_id=item.logical_id,
                score=item.score,
                content_preview=item.content_preview,
                source_locator=item.source_locator,
                metadata=item.metadata,
            )
            for item in mapped.results
        ]

    def close(self) -> None:
        if not self._closed:
            self.connection.close()
            self._closed = True


def create_application_storage(
    root: Path,
    config: Any,
    *,
    create: bool = False,
) -> CoreCompatibilityStorage:
    """Open the only supported catalog; create it only for an explicit writer."""
    catalog = open_application_catalog(root.resolve(), config, create=create)
    return CoreCompatibilityStorage(
        catalog.connection,
        metadata_policy=metadata_projection_policy_from_config(config.metadata),
    )


def resolve_application_database_path(root: Path, config: Any) -> Path:
    """Return the fixed normal-operation path without opening or creating it."""
    return canonical_catalog_path(root.resolve(), config)


__all__ = [
    "ApplicationStoreError",
    "CoreCompatibilityMapper",
    "CoreCompatibilityStorage",
    "create_application_storage",
    "embedding_space_id",
    "prepared_file_to_resource_batch",
    "resolve_application_database_path",
]
