"""Connected native temporal use case; existing media builders and W4 CAS own the graph."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from mdrack._observability import AppEventLogger
from mdrack.application._native_observation import NativeObservation, ObservedProvider
from mdrack.application.media_search import MediaSearchRequest, MediaSearchResult, MediaSearchService
from mdrack.application.reindexing import producer_job, publish_reindex, source_revision
from mdrack.application.source_resolution import SourceResolver
from mdrack.application.vector_values import canonicalize_for_space
from mdrack.domain.embedding_inputs import AudioInput, EncodingRole, ImageInput, VideoInput
from mdrack.domain.native_media import NativeMediaKind, NativeMediaPolicy
from mdrack.ingestion.native_decoding import DecodedNativeSource, NativeMediaDecoder
from mdrack.ingestion.raw_source_provenance import canonical_json, sha256_digest
from mdrack.ports.embeddings import PreparedEmbeddingProvider
from mdrack_core import (
    EmbeddingSpaceRecord,
    ExpectedPublication,
    JSONValue,
    Locator,
    PreparedResourceBatch,
    PublicationResult,
    ResourceSnapshot,
    SearchScope,
)
from mdrack_core.ports.publication import PublicationPort
from mdrack_media import MediaResourceDescriptor, validate_media_id
from mdrack_media.native import (
    NativeAudioObservation,
    NativeBatchInput,
    NativeFrameObservation,
    NativeVideoObservation,
    build_native_audio_batch,
    build_native_frame_batch,
    build_native_video_batch,
)

logger = AppEventLogger(logging.getLogger(__name__))


def native_media_capabilities(provider: PreparedEmbeddingProvider) -> dict[str, object]:
    """Declared adapter contracts only; never a runtime/model probe."""
    value = provider.capabilities
    return {
        "schema": "mdrack.native-capabilities.v1",
        "routes": [
            {
                "role": item.role.value,
                "input_modality": item.input_modality,
                "candidate_modalities": list(item.candidate_modalities),
                "max_input_bytes": item.max_input_bytes,
                "max_duration_ms": item.max_duration_ms,
            }
            for item in value.inputs
        ],
        "reuse_document_vector": value.reuse_document_vector,
    }


@dataclass(frozen=True)
class NativeMediaResult:
    resource_id: str
    kind: NativeMediaKind
    source_revision: str
    duration_ms: int
    selected_range: tuple[int, int]
    coordinates: tuple[tuple[int, int], ...]
    unit_ids: tuple[str, ...]
    recipe_fingerprint: str
    publication_status: str
    attempt_count: int
    provider_call_count: int
    prepared_input_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "mdrack.native-media.v1",
            "resource_id": self.resource_id,
            "kind": self.kind,
            "source_revision": self.source_revision,
            "duration_ms": self.duration_ms,
            "selected_range_ms": list(self.selected_range),
            "observed_coordinates_ms": [list(c) for c in self.coordinates],
            "coverage_kind": "sampled_frames" if self.kind == "frames" else "decoded_windows",
            "unit_ids": list(self.unit_ids),
            "recipe_fingerprint": self.recipe_fingerprint,
            "publication_status": self.publication_status,
            "attempt_count": self.attempt_count,
            "provider_call_count": self.provider_call_count,
            "prepared_input_bytes": self.prepared_input_bytes,
            "extraction_call_count": 0,
        }


class _SourceGuardedPublication:
    def __init__(self, port: PublicationPort, source: DecodedNativeSource) -> None:
        self.port, self.source = port, source

    def read_snapshot(self, resource_id: str) -> ResourceSnapshot:
        return self.port.read_snapshot(resource_id)

    def compare_and_publish(self, batch: PreparedResourceBatch, expected: ExpectedPublication) -> PublicationResult:
        self.source.check()
        return self.port.compare_and_publish(batch, expected)

    def withdraw_if_current(self, resource_id: str, expected: ExpectedPublication) -> PublicationResult:
        self.source.check()
        return self.port.withdraw_if_current(resource_id, expected)

    def delete_if_current(self, resource_id: str, expected: ExpectedPublication) -> PublicationResult:
        self.source.check()
        return self.port.delete_if_current(resource_id, expected)


def _generation(snapshot: ResourceSnapshot, source: DecodedNativeSource, namespace: str, locator: Locator) -> str:
    if snapshot.batch is not None:
        resource = snapshot.batch.resource
        old = resource.metadata.get("provenance")
        if (
            isinstance(old, Mapping)
            and old.get("raw_source_sha256") == source.source_revision
            and resource.source_namespace == namespace
            and resource.locator == locator
        ):
            return source_revision(resource)
    return source.source_revision


def _retained_vectors(
    snapshot: ResourceSnapshot, source: DecodedNativeSource, purpose: str, generation: str, space: EmbeddingSpaceRecord
) -> dict[tuple[int, int, str], tuple[float, ...]]:
    """Reuse actual matching native inputs/profile, never transcript or a guessed aggregate."""
    batch = snapshot.batch
    if batch is None:
        return {}
    result = {}
    for rep in batch.representations:
        info = rep.metadata.get("enrichment_v1")
        if not isinstance(info, Mapping) or (info.get("purpose"), info.get("variant")) != (purpose, "default"):
            continue
        if info.get("manual") is True:
            raise ValueError("manual_artifact_is_authoritative")
        if (
            space not in batch.spaces
            or info.get("source_revision") != generation
            or info.get("recipe") != source.recipe_fingerprint
        ):
            continue
        vectors = {v.unit_id: v.vector for v in batch.vectors if v.space_id == space.space_id}
        for unit in batch.units:
            if unit.representation_id != rep.representation_id or unit.unit_id not in vectors:
                continue
            payload = unit.evidence_locator.payload
            left = payload.get("timestamp_ms", payload.get("start_ms"))
            right = payload.get("timestamp_ms", payload.get("end_ms"))
            identity = unit.metadata.get("input_fingerprint")
            if type(left) is int and type(right) is int and isinstance(identity, str):
                result[(left, right, identity)] = vectors[unit.unit_id]
    return result


async def ingest_native_media(
    provider: PreparedEmbeddingProvider,
    catalog_factory: Callable[[], object],
    path: Path | None,
    *,
    kind: NativeMediaKind,
    resource_id: str,
    source_namespace: str,
    source_ref: str,
    policy: NativeMediaPolicy = NativeMediaPolicy(),
    expected_revision: str | None = None,
    resolver: SourceResolver | None = None,
    decoder: NativeMediaDecoder | None = None,
    max_retries: int = 1,
) -> NativeMediaResult:
    if kind not in {"frames", "audio", "video"}:
        raise ValueError("native_kind_invalid")
    modality = "image" if kind == "frames" else kind
    capability = provider.capabilities.require(EncodingRole.DOCUMENT, modality)
    validate_media_id(resource_id, "resource_id", kind="resource")
    if not isinstance(policy, NativeMediaPolicy) or type(max_retries) is not int or not 0 <= max_retries <= 3:
        raise ValueError("native_policy_invalid")
    selected = (resolver or SourceResolver()).selected_path(
        namespace=source_namespace, source_ref=source_ref, selected=path
    )
    port = catalog_factory()
    if not isinstance(port, PublicationPort):
        raise TypeError("publication capability required")
    # Before filesystem/decoder/provider work, never acquire authority from a later snapshot.
    snapshot = port.read_snapshot(resource_id)
    observation = NativeObservation(
        logger, "ingest", modality=modality, role=EncodingRole.DOCUMENT, fingerprint=provider.space.fingerprint
    )
    observation.start()
    observed = ObservedProvider(provider, observation)
    try:
        async with (decoder or NativeMediaDecoder()).prepare(
            selected, kind, policy, max_input_bytes=capability.max_input_bytes, expected_revision=expected_revision
        ) as source:
            observation.fields.update(
                source_bytes=source.source_bytes,
                artifact_status="decoded_native",
                artifact_requested_count=len(source.units),
                artifact_available_count=len(source.units),
            )
            locator = Locator("raw_local_source", {"source_ref": source_ref})
            generation = _generation(snapshot, source, source_namespace, locator)
            descriptor = MediaResourceDescriptor(
                resource_id, source.resource_kind, source.media_type, source_namespace, locator
            )
            spec = NativeBatchInput(
                descriptor,
                source.source_revision,
                source.recipe_fingerprint,
                provider.space,
                source.duration_ms,
                source.start_ms,
                source.end_ms,
            )
            purpose = f"native/{kind}/{source.start_ms}:{source.end_ms}"
            retained = _retained_vectors(snapshot, source, purpose, generation, provider.space)
            # Validate the complete prepared sequence BEFORE the first provider await.
            for unit in source.units:
                provider.capabilities.validate(unit.value, EncodingRole.DOCUMENT)
            frames: list[NativeFrameObservation] = []
            audio: list[NativeAudioObservation] = []
            video: list[NativeVideoObservation] = []
            for unit in source.units:
                cached = retained.get((unit.start_ms, unit.end_ms, unit.value.identity))
                if cached is None:
                    result = await observed.encode(unit.value, EncodingRole.DOCUMENT)
                    if result.space != provider.space:
                        raise ValueError("incompatible_embedding_space")
                    vector = canonicalize_for_space(result.vector, result.space)
                else:
                    vector = cached
                if isinstance(unit.value, ImageInput):
                    frames.append(NativeFrameObservation(unit.start_ms, unit.value.identity, vector))
                elif isinstance(unit.value, AudioInput):
                    audio.append(NativeAudioObservation(unit.start_ms, unit.end_ms, unit.value.identity, vector))
                elif isinstance(unit.value, VideoInput):
                    video.append(NativeVideoObservation(unit.start_ms, unit.end_ms, unit.value.identity, vector))
            if kind == "frames":
                batch = build_native_frame_batch(spec, tuple(frames))
            elif kind == "audio":
                batch = build_native_audio_batch(spec, tuple(audio))
            else:
                batch = build_native_video_batch(spec, tuple(video))
            # Range is purpose, not model/profile. A new sampling recipe replaces this range's active variant.
            revision = sha256_digest(
                canonical_json(
                    [
                        source.recipe_fingerprint,
                        provider.space.fingerprint,
                        [[u.value.identity, u.start_ms, u.end_ms] for u in source.units],
                        [list(v.vector) for v in batch.vectors],
                    ]
                )
            )
            native: dict[str, JSONValue] = {
                "purpose": purpose,
                "variant": "default",
                "source_revision": generation,
                "artifact_revision": revision,
                "dependencies": (),
                "producer": "native.temporal.v1",
                "recipe": source.recipe_fingerprint,
                "retention": "search_graph",
            }
            provenance: dict[str, JSONValue] = {
                "schema": "mdrack.native-source.v1",
                "source_ref": source_ref,
                "raw_source_sha256": source.source_revision,
                "byte_size": source.source_bytes,
                "duration_ms": source.duration_ms,
            }
            resource = replace(
                batch.resource,
                metadata={**batch.resource.metadata, "source_revision": generation, "provenance": provenance},
            )
            batch = replace(
                batch,
                resource=resource,
                representations=tuple(
                    replace(
                        rep,
                        metadata={
                            **rep.metadata,
                            "enrichment_v1": native,
                            "native_preparation": {
                                "kind": kind,
                                "decoder_fingerprint": source.decoder_fingerprint,
                                "selected_frame_count": len(frames),
                                "selected_range_ms": (source.start_ms, source.end_ms),
                                "observed_coordinates_ms": tuple((u.start_ms, u.end_ms) for u in source.units),
                            },
                        },
                    )
                    for rep in batch.representations
                ),
            )
            source.check()
            observation.fields["publication_status"] = "failed"
            with observation.stage("publication_duration_ms"):
                outcome = publish_reindex(
                    _SourceGuardedPublication(port, source),
                    producer_job(snapshot, batch, owner="native.temporal", generation=generation),
                    max_retries=max_retries,
                )
            observation.fields.update(
                publication_status=outcome["publication_status"], attempt_count=outcome["attempt_count"]
            )
            result_dto = NativeMediaResult(
                resource_id,
                kind,
                source.source_revision,
                source.duration_ms,
                (source.start_ms, source.end_ms),
                tuple((u.start_ms, u.end_ms) for u in source.units),
                tuple(u.unit_id for u in batch.units),
                source.recipe_fingerprint,
                outcome["publication_status"],
                outcome["attempt_count"],
                observed.call_count,
                observed.input_bytes,
            )
    except BaseException as error:
        observation.finish(error)
        raise
    observation.fields.update(unique_resource_count=1, unique_unit_count=len(result_dto.unit_ids), result_count=1)
    observation.finish()
    return result_dto


async def search_native_media(
    service: MediaSearchService,
    provider: PreparedEmbeddingProvider,
    path: Path | None,
    *,
    kind: NativeMediaKind,
    policy: NativeMediaPolicy,
    scope: SearchScope = SearchScope(),
    target: str = "resource",
    mode: str = "hybrid",
    allow_partial: bool = False,
    limit: int = 20,
    candidate_budget: int = 100,
    source_ref: str = "query-media",
    expected_revision: str | None = None,
    resolver: SourceResolver | None = None,
    decoder: NativeMediaDecoder | None = None,
) -> MediaSearchResult:
    modality = "image" if kind == "frames" else kind
    capability = provider.capabilities.require(EncodingRole.QUERY, modality, scope.modalities)
    if mode == "text":
        raise ValueError("no_applicable_search_branch")
    selected = (resolver or SourceResolver()).selected_path(namespace="query", source_ref=source_ref, selected=path)
    async with (decoder or NativeMediaDecoder()).prepare(
        selected, kind, policy, max_input_bytes=capability.max_input_bytes, expected_revision=expected_revision
    ) as source:
        if len(source.units) != 1:
            raise ValueError("native_query_requires_single_unit")
        return await service.search(
            MediaSearchRequest(source.units[0].value, scope, target, mode, allow_partial, limit, candidate_budget)
        )
