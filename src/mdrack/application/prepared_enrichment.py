"""Prepared text/media enrichment with four explicit intents and real host custody.

Projectors never call ingest, models or providers. The neutral publication port
owns transactions/FTS; the existing pure merger owns dependency invalidation.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from mdrack._observability import AppEventLogger
from mdrack.application.durable_artifacts import DurableArtifactStore
from mdrack.application.enrichment import (
    CapturedInputs,
    PreparedEnrichment,
    Slot,
    _outputs,
    _required_output,
    capture,
    prepare,
    publish,
)
from mdrack.application.ready_ocr import _pairs, _read
from mdrack.application.reindexing import PreparedReindex, publish_reindex
from mdrack.application.transcript_ingestion import TranscriptIngestionService
from mdrack.application.vector_values import canonicalize_for_space, canonicalize_prepared_batch_vectors
from mdrack.application.video_composition import VideoCompositionService
from mdrack.ingestion.frame_captions import read_frame_captions
from mdrack_core import (
    EmbeddingSpaceRecord,
    PreparedResourceBatch,
    RepresentationRecord,
    SearchUnitRecord,
    VectorRecord,
)
from mdrack_core.domain.common import JSONValue, to_plain_json
from mdrack_core.ports.publication import PublicationPort
from mdrack_media import (
    FrameBatchBuilderInput,
    MediaResourceDescriptor,
    TranscriptArtifact,
    build_video_frame_caption_batch,
)

logger = AppEventLogger(logging.getLogger(__name__))


def artifact_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _slots(kind: str, purpose: str, variant: str) -> tuple[tuple[str, str], ...]:
    if (
        kind not in {"ocr", "text", "transcript", "frames", "video"}
        or not isinstance(purpose, str)
        or not purpose.strip()
        or not isinstance(variant, str)
        or not variant.strip()
    ):
        raise ValueError("enrichment_intent_invalid")
    return (
        ((purpose + "/transcript", variant), (purpose + "/frames", variant))
        if kind == "video"
        else ((purpose, variant),)
    )


@dataclass(frozen=True)
class PreparedEnrichmentOperation:
    captured: CapturedInputs
    publication: PreparedEnrichment | PreparedReindex | None
    path: Path
    file_digest: str
    reference: str
    archive: DurableArtifactStore | None
    status: str
    lexical: bool
    dense: bool
    publication_owner: object = field(default=None, repr=False, compare=False)


class PreparedArtifactService:
    def __init__(self, port: PublicationPort, archive: DurableArtifactStore | None = None) -> None:
        self.port, self.archive = port, archive

    def binding(self, resource_id: str) -> dict[str, object]:
        captured = capture(self.port.read_snapshot(resource_id), targets=(("binding", "default"),))
        state = captured.snapshot.state
        assert state.source_locator is not None
        return {
            "resource_id": resource_id,
            "lifecycle_id": state.lifecycle_id,
            "source_namespace": state.source_namespace,
            "source_locator": {
                "kind": state.source_locator.kind,
                "payload": to_plain_json(state.source_locator.payload),
            },
            "source_revision": captured.source_revision,
        }

    def prepare(
        self,
        resource_id: str,
        path: Path,
        *,
        kind: str,
        purpose: str,
        variant: str = "default",
        lexical: bool = True,
        dense: bool = False,
        require_durable: bool = False,
        inputs: tuple[tuple[str, str], ...] = (),
    ) -> PreparedEnrichmentOperation:
        if any(type(flag) is not bool for flag in (lexical, dense, require_durable)):
            raise ValueError("enrichment_intent_invalid")
        captured = capture(self.port.read_snapshot(resource_id), targets=_slots(kind, purpose, variant), inputs=inputs)
        if require_durable and self.archive is None:
            raise ValueError("durable_artifact_owner_unavailable")
        data = _read(Path(path))  # deliberately after capture, including before stat/open
        try:
            value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs)
            fields = {
                "contract",
                "version",
                "binding",
                "artifact_revision",
                "producer",
                "recipe",
                "manual",
                "artifact",
                "artifact_sha256",
                "dependencies",
                "dense",
            }
            if (
                type(value) is not dict
                or set(value) != fields
                or value["contract"] != "mdrack.prepared-enrichment"
                or type(value["version"]) is not int
                or value["version"] != 1
            ):
                raise ValueError
            if value["binding"] != self._binding(captured):
                raise ValueError
            for key in ("artifact_revision", "producer", "recipe"):
                if not isinstance(value[key], str) or not value[key].strip():
                    raise ValueError
            if type(value["manual"]) is not bool or value["artifact_sha256"] != artifact_digest(value["artifact"]):
                raise ValueError
            if not isinstance(value["dependencies"], list):
                raise ValueError
            for dependency in value["dependencies"]:
                if type(dependency) is not dict or set(dependency) != {"purpose", "variant", "revision"}:
                    raise ValueError
            declared = tuple(((item["purpose"], item["variant"]), item["revision"]) for item in value["dependencies"])
            assert captured.snapshot.batch is not None
            expected = {(key, _outputs(captured.snapshot.batch)[key][1].artifact_revision) for key in captured.inputs}
            if len(set(declared)) != len(declared) or set(declared) != expected:
                raise ValueError
            active_metadata: dict[Slot, Mapping[str, JSONValue]] = {}
            for slot, (record, _) in _outputs(captured.snapshot.batch).items():
                metadata = record.metadata["enrichment_v1"]
                assert isinstance(metadata, Mapping)
                active_metadata[slot] = metadata
            if (
                (lexical or dense)
                and not value["manual"]
                and any(
                    active_metadata[key].get("manual") is True
                    for key in captured.targets
                    if key in active_metadata
                )
            ):
                raise ValueError
            projections = self._project(captured, value["artifact"], kind)
            reference = (
                "artifact:sha256:" + hashlib.sha256(data).hexdigest()
                if self.archive is not None
                else "caller:sha256:" + hashlib.sha256(data).hexdigest()
            )
            addition = self._intent(captured, projections, value, reference, kind=kind, lexical=lexical, dense=dense)
            publication: PreparedEnrichment | PreparedReindex | None = None
            if lexical or dense:
                remove = tuple(
                    key for key, projection in zip(captured.targets, projections, strict=True) if projection is None
                )
                if remove:
                    publication = PreparedReindex(
                        captured.snapshot,
                        addition,
                        captured.snapshot.batch.resource,
                        "prepared-artifact",
                        captured.inputs,
                        remove,
                    )
                elif addition is not None:
                    publication = prepare(captured, addition)
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise ValueError("prepared_artifact_invalid_or_stale") from None
        # Retain only a validated artifact; cache is never an archival owner.
        if self.archive is not None:
            reference = self.archive.retain(data)
        status = "no_text" if not any(projections) else "read_only" if not lexical and not dense else "prepared"
        return PreparedEnrichmentOperation(
            captured,
            publication,
            Path(path),
            hashlib.sha256(data).hexdigest(),
            reference,
            self.archive,
            status,
            lexical,
            dense,
            self.port,
        )

    @staticmethod
    def _binding(captured: CapturedInputs) -> dict[str, object]:
        state = captured.snapshot.state
        assert state.source_locator is not None
        return {
            "resource_id": state.resource_id,
            "lifecycle_id": state.lifecycle_id,
            "source_namespace": state.source_namespace,
            "source_locator": {
                "kind": state.source_locator.kind,
                "payload": to_plain_json(state.source_locator.payload),
            },
            "source_revision": captured.source_revision,
        }

    def _project(self, captured: CapturedInputs, artifact: Any, kind: str) -> tuple[PreparedResourceBatch | None, ...]:
        assert captured.snapshot.batch is not None
        resource = captured.snapshot.batch.resource
        if kind in {"text", "ocr"}:
            if not isinstance(artifact, str):
                raise ValueError
            if kind == "ocr" and resource.resource_kind != "image":
                raise ValueError
            if not artifact.strip():
                return (None,)
            rep = RepresentationRecord(
                "ready-text", resource.resource_id, "ocr_text" if kind == "ocr" else "caption_text", "text", artifact
            )
            unit = SearchUnitRecord(
                "ready-unit",
                resource.resource_id,
                rep.representation_id,
                "whole_resource",
                "text",
                artifact,
                resource.locator,
                0,
            )
            return (PreparedResourceBatch(resource, (rep,), (unit,)),)

        def transcript(value: Any) -> PreparedResourceBatch | None:
            record = TranscriptArtifact.from_dict(value)
            if record.resource_id != resource.resource_id or resource.resource_kind not in {"audio", "video"}:
                raise ValueError
            if not record.atoms:
                return None
            batch = TranscriptIngestionService(self.port).prepare(
                record,
                resource_kind=resource.resource_kind,
                media_type=resource.media_type,
                source_namespace=resource.source_namespace,
                source_locator=resource.locator,
            )
            return self._passages(batch, "timed_passage")

        def frames(value: Any) -> PreparedResourceBatch | None:
            record = read_frame_captions(json.dumps(value).encode()).artifact
            if record.resource_id != resource.resource_id or resource.resource_kind != "video":
                raise ValueError
            if not record.observations:
                return None
            descriptor = MediaResourceDescriptor(
                resource.resource_id, "video", resource.media_type, resource.source_namespace, resource.locator
            )
            return build_video_frame_caption_batch(FrameBatchBuilderInput(resource=descriptor, frames=record))

        if kind == "transcript":
            return (transcript(artifact),)
        if kind == "frames":
            return (frames(artifact),)
        if type(artifact) is not dict or set(artifact) != {"transcript", "frames"}:
            raise ValueError
        # Reuse the existing complete provider-free .prepare, not its final ingest.
        t = TranscriptArtifact.from_dict(artifact["transcript"])
        f = read_frame_captions(json.dumps(artifact["frames"]).encode()).artifact
        if (
            resource.resource_kind != "video"
            or t.resource_id != resource.resource_id
            or f.resource_id != resource.resource_id
        ):
            raise ValueError
        if not t.atoms or not f.observations:
            return (transcript(artifact["transcript"]), frames(artifact["frames"]))
        batch = VideoCompositionService(self.port).prepare(
            t,
            f,
            media_type=resource.media_type,
            source_namespace=resource.source_namespace,
            source_locator=resource.locator,
        )
        return (self._passages(batch, "timed_passage"), self._passages(batch, "frame_caption"))

    @staticmethod
    def _passages(batch: PreparedResourceBatch, kind: str) -> PreparedResourceBatch:
        reps = tuple(r for r in batch.representations if r.representation_kind == kind)
        ids = {r.representation_id for r in reps}
        return replace(
            batch,
            representations=reps,
            units=tuple(u for u in batch.units if u.representation_id in ids),
            spaces=(),
            vectors=(),
            facets=(),
        )

    def _intent(
        self,
        captured: CapturedInputs,
        projections: tuple[PreparedResourceBatch | None, ...],
        value: dict[str, Any],
        reference: str,
        *,
        kind: str,
        lexical: bool,
        dense: bool,
    ) -> PreparedResourceBatch | None:
        if len(projections) != len(captured.targets):
            raise ValueError
        assert captured.snapshot.batch is not None
        resource = captured.snapshot.batch.resource
        spaces: tuple[EmbeddingSpaceRecord, ...] = ()
        dense_values: list[Any] = []
        dense_info = value["dense"]
        ready_space: EmbeddingSpaceRecord | None = None
        if dense_info is not None:
            if (
                type(dense_info) is not dict
                or set(dense_info) != {"space", "recipe", "artifact_sha256", "vectors"}
                or not isinstance(dense_info["recipe"], str)
                or not dense_info["recipe"].strip()
                or dense_info["artifact_sha256"] != value["artifact_sha256"]
            ):
                raise ValueError
            ready_space = EmbeddingSpaceRecord(**dense_info["space"])
            dense_values = dense_info["vectors"]
            if type(dense_values) is not list or len(dense_values) != sum(
                len(p.units) for p in projections if p is not None
            ):
                raise ValueError
            dense_values = [canonicalize_for_space(vector, ready_space) for vector in dense_values]
            if any(len(vector) != ready_space.dimensions for vector in dense_values):
                raise ValueError
        if not any(projections) or not lexical and not dense:
            return None
        if dense:
            if ready_space is None:
                raise ValueError
            spaces = (ready_space,)
        representations, units, vectors = [], [], []
        ready_by_unit: dict[str, tuple[float, ...]] = {}
        index = 0
        components = (
            [value["artifact"]["transcript"], value["artifact"]["frames"]] if kind == "video" else [value["artifact"]]
        )
        for (purpose, variant), projection, component in zip(captured.targets, projections, components, strict=True):
            if projection is None:
                continue
            if len(projection.representations) != 1 or not projection.units:
                raise ValueError
            key = artifact_digest([resource.resource_id, purpose, variant])
            rid = "enrichment:" + key
            info: dict[str, JSONValue] = {
                "purpose": purpose,
                "variant": variant,
                "source_revision": captured.source_revision,
                "artifact_revision": value["artifact_revision"],
                "producer": value["producer"],
                "recipe": value["recipe"],
                "dependencies": value["dependencies"],
                "artifact_reference": reference,
                "artifact_sha256": value["artifact_sha256"],
                "component_sha256": artifact_digest(component),
                "retention": "host_durable" if self.archive else "caller_owned",
                "manual": value["manual"],
                "embedding_recipe": dense_info["recipe"] if ready_space is not None else None,
                "embedding_space": ready_space.space_id if ready_space is not None else None,
                "dense_indexed": dense,
                "lexical_indexed": lexical,
            }
            rep = projection.representations[0]
            representations.append(
                replace(
                    rep,
                    representation_id=rid,
                    resource_id=resource.resource_id,
                    text=rep.text if lexical else None,
                    metadata={**rep.metadata, "enrichment_v1": info},
                )
            )
            for ordinal, unit in enumerate(projection.units):
                uid = rid + ":" + str(ordinal)
                units.append(
                    replace(
                        unit,
                        unit_id=uid,
                        representation_id=rid,
                        resource_id=resource.resource_id,
                        text=unit.text if lexical else None,
                        metadata={**unit.metadata, "source_revision": captured.source_revision},
                    )
                )
                if ready_space is not None:
                    ready_by_unit[uid] = dense_values[index]
                    index += 1
                    if dense:
                        vectors.append(VectorRecord(uid, ready_space.space_id, ready_by_unit[uid]))
        batch = PreparedResourceBatch(resource, tuple(representations), tuple(units), spaces, tuple(vectors))
        batch = canonicalize_prepared_batch_vectors(batch)
        active = _outputs(captured.snapshot.batch)
        finalized = []
        for rep in batch.representations:
            metadata = rep.metadata["enrichment_v1"]
            assert isinstance(metadata, Mapping)
            info = dict(metadata)
            ids = {u.unit_id for u in batch.units if u.representation_id == rep.representation_id}
            material_vectors = (
                []
                if ready_space is None
                else [
                    [u.unit_id, ready_space.space_id, list(ready_by_unit[u.unit_id])]
                    for u in batch.units
                    if u.unit_id in ids
                ]
            )
            material_spaces = [] if ready_space is None else [ready_space]
            material = artifact_digest(
                [
                    info["component_sha256"],
                    info["producer"],
                    info["recipe"],
                    info["embedding_recipe"],
                    material_vectors,
                    [
                        [s.space_id, s.dimensions, s.metric, s.fingerprint, to_plain_json(s.metadata)]
                        for s in material_spaces
                    ],
                ]
            )
            previous = active.get(_required_output(rep).slot)
            if kind == "video":
                info["caller_artifact_revision"] = info["artifact_revision"]
                info["artifact_revision"] = "sha256:" + material
            elif previous is not None and previous[1].artifact_revision == info["artifact_revision"]:
                previous_info = previous[0].metadata["enrichment_v1"]
                assert isinstance(previous_info, Mapping)
                if previous_info.get("material_fingerprint") != material:
                    raise ValueError("artifact_revision_reused_for_different_material")
            info["material_fingerprint"] = material
            finalized.append(replace(rep, metadata={**rep.metadata, "enrichment_v1": info}))
        return replace(batch, representations=tuple(finalized))

    def publish(self, operation: PreparedEnrichmentOperation, *, max_retries: int = 1) -> dict[str, object]:
        if operation.publication_owner is not self.port:
            raise ValueError("prepared_operation_belongs_to_other_context")
        if type(max_retries) is not int or not 0 <= max_retries <= 3:
            raise ValueError("invalid_retry_budget")
        data = operation.archive.read_bytes(operation.reference) if operation.archive else _read(operation.path)
        if hashlib.sha256(data).hexdigest() != operation.file_digest:
            raise ValueError("prepared_artifact_changed")
        if operation.publication is None:
            status, attempts = "not_requested", 0
        elif isinstance(operation.publication, PreparedReindex):
            result = publish_reindex(self.port, operation.publication, max_retries=max_retries)
            status, attempts = result["publication_status"], result["attempt_count"]
        else:
            outcome = publish(self.port, operation.publication, max_retries=max_retries)
            status, attempts = outcome.status, outcome.attempts
        return {
            "resource_id": operation.captured.snapshot.state.resource_id,
            "publication_status": status,
            "artifact_status": operation.status,
            "artifact_reference": operation.reference,
            "retention": "host_durable" if operation.archive else "caller_owned_verified",
            "source_validation": "catalog_binding_only",
            "attempt_count": attempts,
            "embedding_calls": 0,
            "extraction_calls": 0,
            "lexical": operation.lexical,
            "dense": operation.dense,
        }

    def enrich(self, resource_id: str, path: Path, **options: Any) -> dict[str, object]:
        retries = options.pop("max_retries", 1)
        logger.info("enrichment.started", extra={"provider_call_count": 0, "extraction_call_count": 0})
        try:
            if type(retries) is not int or not 0 <= retries <= 3:
                raise ValueError("invalid_retry_budget")
            result = self.publish(self.prepare(resource_id, path, **options), max_retries=retries)
        except Exception:
            logger.error("enrichment.failed", extra={"reason": "operation_failed"})
            raise
        logger.info(
            "enrichment.completed",
            extra={
                "publication_status": result["publication_status"],
                "attempt_count": result["attempt_count"],
                "provider_call_count": 0,
                "extraction_call_count": 0,
            },
        )
        return result
