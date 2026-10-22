"""Common application producer reconciliation; SQL remains the only writer.

A producer supplies a complete ready base, not an enrichment ingest side effect.
Unconditional low-level import/replace retains its existing explicit semantics.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast
from uuid import uuid4

from mdrack.application.enrichment import (
    StaleEnrichment,
    _output,
    _outputs,
    _projection,
    _required_output,
    independent_output,
    invalid_dependencies,
    prune_graph,
)
from mdrack.application.manifest import decode_prepared_resource_manifest
from mdrack.application.ready_ocr import _pairs, _read
from mdrack_core import ExpectedPublication, PreparedResourceBatch, ResourceRecord, ResourceSnapshot
from mdrack_core.application.indexing import CoreIndexingService
from mdrack_core.application.validation import validate_prepared_batch
from mdrack_core.domain.common import JSONValue, to_plain_json
from mdrack_core.ports.catalog import ResourceWritePort
from mdrack_core.ports.publication import PublicationPort

_SOURCE_KEYS = frozenset(
    {"source_revision", "source_revision_kind", "provenance", "ingestion", "derived", "source", "adapter", "byte_size"}
)


def require_prepared_binding(snapshot: ResourceSnapshot, metadata: Mapping[str, Any]) -> None:
    """An already prepared artifact cannot acquire a newer source/lifecycle here."""
    if snapshot.state.status == "never_created":
        if metadata.get("enrichment_binding") is not None:
            raise StaleEnrichment("foreign_prepared_binding_is_not_creation_authority")
        return  # first complete creation has no pre-existing source authority to borrow
    if snapshot.batch is None:
        raise StaleEnrichment("prepared_source_binding_unavailable")
    state = snapshot.state
    if state.source_locator is None:
        raise StaleEnrichment("prepared_source_binding_unavailable")
    expected = {
        "resource_id": state.resource_id,
        "lifecycle_id": state.lifecycle_id,
        "source_namespace": state.source_namespace,
        "source_locator": {"kind": state.source_locator.kind, "payload": to_plain_json(state.source_locator.payload)},
        "source_revision": source_revision(snapshot.batch.resource),
    }
    if metadata.get("enrichment_binding") != expected:
        raise StaleEnrichment("prepared_source_binding_missing_or_stale")


def source_revision(resource: ResourceRecord) -> str:
    value = resource.metadata.get("source_revision")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source_generation_unavailable")
    return value


def _raw(resource: ResourceRecord) -> object:
    value = resource.metadata.get("provenance")
    return value.get("raw_source_sha256") if isinstance(value, Mapping) else None


def _union(old: tuple[Any, ...], new: tuple[Any, ...], key: Any) -> tuple[Any, ...]:
    result = {key(value): value for value in old}
    for value in new:
        identity = key(value)
        if identity in result and result[identity] != value:
            raise ValueError("independent_projection_identity_conflict")
        result.setdefault(identity, value)
    return tuple(result.values())


def reconcile(
    current: ResourceSnapshot,
    incoming: PreparedResourceBatch | None,
    resource: ResourceRecord,
    *,
    owner: str,
    remove: tuple[tuple[str, str], ...] = (),
) -> PreparedResourceBatch | None:
    generation = source_revision(resource)
    old = current.batch
    if incoming is not None:
        validate_prepared_batch(incoming)
        if incoming.resource != resource or incoming.resource.resource_id != current.state.resource_id:
            raise ValueError("producer_resource_mismatch")
        outputs = _outputs(incoming)
        if any(
            not independent_output(rep) and output.source_revision != generation for rep, output in outputs.values()
        ):
            raise StaleEnrichment("incoming_source_revision_mismatch")
        if old is None:
            invalid_dependencies(outputs, frozenset(outputs))
    if old is None:
        if incoming is None:
            return None
        return prune_graph(incoming)
    old_generation = source_revision(old.resource)
    changed = generation != old_generation
    if not changed and (
        _raw(old.resource) != _raw(resource)
        or old.resource.source_namespace != resource.source_namespace
        or old.resource.locator != resource.locator
        or old.resource.resource_kind != resource.resource_kind
        or old.resource.media_type != resource.media_type
    ):
        raise ValueError("source_changed_without_new_generation")
    incoming_reps = () if incoming is None else incoming.representations
    incoming_outputs = {} if incoming is None else _outputs(incoming)
    for rep, output in incoming_outputs.values():
        if not independent_output(rep) and output.source_revision != generation:
            raise StaleEnrichment("incoming_source_revision_mismatch")
    incoming_slots = set(incoming_outputs)
    base_kinds = {
        r.representation_kind
        for r in incoming_reps
        if "enrichment_v1" not in r.metadata or r.metadata.get("producer_owner") == owner
    }
    preserved = []
    for rep in old.representations:
        existing_output = _output(rep)
        if existing_output is not None and existing_output.slot in remove:
            continue
        if independent_output(rep):
            preserved.append(rep)
        elif changed:
            continue  # unknown legacy data is NOT proof of independence
        elif existing_output is not None:
            if existing_output.slot not in incoming_slots:
                preserved.append(rep)
        elif rep.metadata.get("producer_owner") != owner and rep.representation_kind not in base_kinds:
            preserved.append(rep)
    kept = {r.representation_id for r in preserved}
    old_units = tuple(u for u in old.units if u.representation_id in kept)
    kept_units = {u.unit_id for u in old_units}
    old_vectors = tuple(v for v in old.vectors if v.unit_id in kept_units)
    old_spaces = tuple(s for s in old.spaces if any(v.space_id == s.space_id for v in old_vectors))
    reps = _union(tuple(preserved), incoming_reps, lambda r: r.representation_id)
    if not reps:
        return None
    metadata = dict(old.resource.metadata)
    for key, value in resource.metadata.items():
        if key not in _SOURCE_KEYS and key in metadata and metadata[key] != value:
            raise ValueError("independent_metadata_conflict")
        metadata[key] = value
    actual_resource = replace(
        resource, metadata=metadata, title=resource.title if resource.title is not None else old.resource.title
    )
    batch = PreparedResourceBatch(
        actual_resource,
        reps,
        _union(old_units, () if incoming is None else incoming.units, lambda u: u.unit_id),
        _union(old_spaces, () if incoming is None else incoming.spaces, lambda s: s.space_id),
        _union(old_vectors, () if incoming is None else incoming.vectors, lambda v: (v.unit_id, v.space_id)),
        _union(
            old.facets,
            () if incoming is None else incoming.facets,
            lambda f: (f.resource_id, f.facet.namespace, f.facet.value, f.origin, f.producer_fingerprint),
        ),
    )
    validate_prepared_batch(batch)
    invalid_dependencies(_outputs(batch), frozenset(incoming_slots))
    return prune_graph(batch)


@dataclass(frozen=True)
class PreparedReindex:
    snapshot: ResourceSnapshot
    incoming: PreparedResourceBatch | None
    resource: ResourceRecord
    owner: str
    inputs: tuple[tuple[str, str], ...] = ()
    remove: tuple[tuple[str, str], ...] = ()
    publication_owner: object = field(default=None, repr=False, compare=False)


def _slot_projection(batch: PreparedResourceBatch, slot: tuple[str, str]) -> object:
    output = _outputs(batch).get(slot)
    return None if output is None else _projection(batch, output[0].representation_id)


class _ReindexResult(TypedDict):
    resource_id: str
    publication_status: str
    attempt_count: int
    representation_ids: NotRequired[list[str]]
    unit_ids: NotRequired[list[str]]
    vector_count: NotRequired[int]


def _result(resource_id: str, status: str, attempts: int, batch: PreparedResourceBatch | None) -> _ReindexResult:
    return {
        "resource_id": resource_id,
        "publication_status": status,
        "attempt_count": attempts,
        "representation_ids": [] if batch is None else [r.representation_id for r in batch.representations],
        "unit_ids": [] if batch is None else [u.unit_id for u in batch.units],
        "vector_count": 0 if batch is None else len(batch.vectors),
    }


def _same_graph(left: PreparedResourceBatch | None, right: PreparedResourceBatch | None) -> bool:
    if left is None or right is None:
        return left is right
    return (
        left.resource == right.resource
        and {r.representation_id: r for r in left.representations}
        == {r.representation_id: r for r in right.representations}
        and {u.unit_id: u for u in left.units} == {u.unit_id: u for u in right.units}
        and {s.space_id: s for s in left.spaces} == {s.space_id: s for s in right.spaces}
        and {(v.unit_id, v.space_id): v for v in left.vectors} == {(v.unit_id, v.space_id): v for v in right.vectors}
        and set(left.facets) == set(right.facets)
    )


def _source_identity(resource: ResourceRecord) -> tuple[object, ...]:
    return (
        source_revision(resource),
        resource.source_namespace,
        resource.locator,
        _raw(resource),
        resource.resource_kind,
        resource.media_type,
    )


def _rebased_material(
    job: PreparedReindex, current: ResourceSnapshot
) -> tuple[PreparedResourceBatch | None, ResourceRecord]:
    resource = job.resource
    if job.snapshot.batch is not None and current.batch is not None:
        metadata = dict(resource.metadata)
        for key, value in job.snapshot.batch.resource.metadata.items():
            if key not in _SOURCE_KEYS and key in metadata and metadata[key] == value:
                if key in current.batch.resource.metadata:
                    metadata[key] = current.batch.resource.metadata[key]
                else:
                    metadata.pop(key, None)
        title = resource.title
        if title == job.snapshot.batch.resource.title:
            title = current.batch.resource.title
        resource = replace(resource, metadata=metadata, title=title)
    incoming = None if job.incoming is None else replace(job.incoming, resource=resource)
    return incoming, resource


def publish_reindex(
    port: PublicationPort, job: PreparedReindex, *, max_retries: int = 1, allow_noop: bool = True
) -> _ReindexResult:
    if job.publication_owner is not None and job.publication_owner is not port:
        raise ValueError("prepared_operation_belongs_to_other_context")
    if type(max_retries) is not int or not 0 <= max_retries <= 3:
        raise ValueError("invalid_retry_budget")
    original, current = job.snapshot, job.snapshot
    attempts = 0
    rebases = 0
    while True:
        if (
            current.state.lifecycle_id != original.state.lifecycle_id
            or current.state.live != original.state.live
            or current.state.status == "deleted"
            and current.state != original.state
        ):
            raise StaleEnrichment("producer_lifecycle_changed")
        if current != original:
            if current.batch is None or original.batch is None:
                raise StaleEnrichment("producer_source_unavailable")
            before, now = original.batch.resource, current.batch.resource
            if _source_identity(before) != _source_identity(now):
                # Lost response: prove exact desired postconditions, never write
                # using the newer generation as an old job's replacement grant.
                if job.incoming is not None and _source_identity(now) == _source_identity(job.resource):
                    incoming, resource = _rebased_material(job, current)
                    desired = reconcile(current, incoming, resource, owner=job.owner, remove=job.remove)
                    if _same_graph(current.batch, desired):
                        return _result(current.state.resource_id, "already_published", attempts, desired)
                raise StaleEnrichment("producer_source_changed")
            previous, active = _outputs(original.batch), _outputs(current.batch)
            targets = {} if job.incoming is None else _outputs(job.incoming)
            for key in set(targets) | set(job.remove):
                desired_projection = None if job.incoming is None else _slot_projection(job.incoming, key)
                if _slot_projection(original.batch, key) != _slot_projection(
                    current.batch, key
                ) and desired_projection != _slot_projection(current.batch, key):
                    raise StaleEnrichment("producer_target_changed")
            for key in job.inputs:
                if (
                    key not in previous
                    or key not in active
                    or previous[key][1].artifact_revision != active[key][1].artifact_revision
                ):
                    raise StaleEnrichment("producer_immediate_input_changed")
        incoming, resource = _rebased_material(job, current)
        batch = reconcile(current, incoming, resource, owner=job.owner, remove=job.remove)
        if allow_noop and current.state.live and _same_graph(current.batch, batch):
            latest = port.read_snapshot(current.state.resource_id)
            if latest.state != current.state:
                if rebases >= max_retries:
                    return {
                        "resource_id": current.state.resource_id,
                        "publication_status": "conflict",
                        "attempt_count": attempts,
                    }
                rebases += 1
                current = latest
                continue
            return _result(
                current.state.resource_id,
                "already_withdrawn" if batch is None else "already_published",
                attempts,
                batch,
            )
        attempts += 1
        expected = ExpectedPublication(current.state, create=not current.state.live)
        if batch is None:
            if not current.state.live:
                raise ValueError("cannot_create_empty_graph")
            outcome = port.withdraw_if_current(current.state.resource_id, expected)
        else:
            outcome = CoreIndexingService(cast(ResourceWritePort, port)).index_if_current(batch, expected)
        if outcome.applied:
            return _result(current.state.resource_id, "withdrawn" if batch is None else "published", attempts, batch)
        if rebases >= max_retries:
            return {
                "resource_id": current.state.resource_id,
                "publication_status": "conflict",
                "attempt_count": attempts,
            }
        rebases += 1
        current = port.read_snapshot(current.state.resource_id)


def _source_purpose(kind: str) -> str:
    return {
        "timed_passage": "source/transcript",
        "frame_caption": "source/frames",
        "ocr_text": "ocr:whole",
        "caption_text": "caption:whole",
    }.get(kind, "source/" + kind)


def _source_components(batch: PreparedResourceBatch) -> PreparedResourceBatch:
    """One logical output may contain several real observations/chunks, not variants."""
    groups: dict[tuple[str, str], list[Any]] = {}
    for rep in batch.representations:
        key = (
            ("existing", rep.representation_id)
            if "enrichment_v1" in rep.metadata
            else ("purpose", _source_purpose(rep.representation_kind))
        )
        groups.setdefault(key, []).append(rep)
    reps, units = [], []
    for group in groups.values():
        first = group[0]
        members = {r.representation_id for r in group}
        grouped_units = [u for u in batch.units if u.representation_id in members]
        if len(group) > 1:
            if any(r.modality != first.modality or r.representation_kind != first.representation_kind for r in group):
                raise ValueError("ambiguous_source_component_coverage")
            text = "\n\n".join(r.text for r in group if r.text is not None) or None
            components = [
                {
                    "representation_id": r.representation_id,
                    "producer_fingerprint": r.producer_fingerprint,
                    "metadata": to_plain_json(r.metadata),
                }
                for r in group
            ]
            first = replace(
                first,
                text=text,
                token_count=None,
                token_count_kind=None,
                language=first.language if all(r.language == first.language for r in group) else None,
                producer_fingerprint=first.producer_fingerprint
                if all(r.producer_fingerprint == first.producer_fingerprint for r in group)
                else None,
                metadata={**first.metadata, "source_components": components},
            )
            grouped_units = [
                replace(u, representation_id=first.representation_id, ordinal=index)
                for index, u in enumerate(grouped_units)
            ]
        reps.append(first)
        units.extend(grouped_units)
    used_spaces = {v.space_id for v in batch.vectors}
    return replace(
        batch,
        representations=tuple(reps),
        units=tuple(units),
        spaces=tuple(s for s in batch.spaces if s.space_id in used_spaces),
    )


def producer_job(
    snapshot: ResourceSnapshot, batch: PreparedResourceBatch, *, owner: str, generation: str | None = None
) -> PreparedReindex:
    """Annotate a newly prepared source base; never infer raw version from content_hash."""
    batch = _source_components(batch)
    old = snapshot.batch
    initial_unbound = generation is None and old is None
    if generation is None:
        generation = (
            source_revision(old.resource)
            if old is not None and old.resource.metadata.get("source_revision")
            else str(uuid4())
        )
    metadata = {**batch.resource.metadata, "source_revision": generation}
    if initial_unbound:
        metadata["source_revision_kind"] = "prepared_catalog_binding"
    resource = replace(batch.resource, metadata=metadata)
    from mdrack.application.prepared_enrichment import artifact_digest

    reps = []
    for rep in batch.representations:
        if "enrichment_v1" in rep.metadata:
            reps.append(rep)
            continue
        units = [u for u in batch.units if u.representation_id == rep.representation_id]
        ids = {u.unit_id for u in units}
        vectors = [v for v in batch.vectors if v.unit_id in ids]
        spaces = [s for s in batch.spaces if any(v.space_id == s.space_id for v in vectors)]
        revision = artifact_digest(
            [
                rep.representation_id,
                rep.representation_kind,
                rep.producer_fingerprint,
                rep.text,
                to_plain_json(rep.metadata),
                [
                    [
                        u.text,
                        u.evidence_locator.kind,
                        to_plain_json(u.evidence_locator.payload),
                        to_plain_json(u.metadata),
                    ]
                    for u in units
                ],
                [[v.unit_id, v.space_id, list(v.vector)] for v in vectors],
                [[s.space_id, s.fingerprint, to_plain_json(s.metadata)] for s in spaces],
            ]
        )
        purpose = _source_purpose(rep.representation_kind)
        if old is not None and source_revision(old.resource) == generation:
            previous = _outputs(old).get((purpose, "default"))
            if previous is not None:
                previous_info = previous[0].metadata["enrichment_v1"]
                assert isinstance(previous_info, Mapping)
                if previous_info.get("manual") is True:
                    raise StaleEnrichment("manual_artifact_is_authoritative")
        info: dict[str, JSONValue] = {
            "purpose": purpose,
            "variant": "default",
            "source_revision": generation,
            "artifact_revision": revision,
            "dependencies": (),
            "recipe": owner + ".prepare",
            "producer": owner,
            "retention": "search_graph",
        }
        reps.append(replace(rep, metadata={**rep.metadata, "producer_owner": owner, "enrichment_v1": info}))
    components = [r for r in reps if r.representation_kind in {"timed_passage", "frame_caption"}]
    updated = []
    for rep in reps:
        if (
            rep.modality == "text"
            and any(
                u.unit_kind == "whole_resource" for u in batch.units if u.representation_id == rep.representation_id
            )
            and components
            and "enrichment_v1"
            not in next(r for r in batch.representations if r.representation_id == rep.representation_id).metadata
        ):
            dependencies: list[JSONValue] = []
            for component in components:
                output = _required_output(component)
                dependencies.append({"purpose": output.slot[0], "variant": output.slot[1],
                                     "revision": output.artifact_revision})
            previous_info = rep.metadata["enrichment_v1"]
            assert isinstance(previous_info, Mapping)
            info = {**previous_info, "dependencies": tuple(dependencies)}
            rep = replace(rep, metadata={**rep.metadata, "enrichment_v1": info})
        updated.append(rep)
    return PreparedReindex(snapshot, replace(batch, resource=resource, representations=tuple(updated)), resource, owner)


class ReindexService:
    def __init__(self, port: PublicationPort) -> None:
        self.port = port

    def binding(self, resource_id: str) -> dict[str, object]:
        state = self.port.read_snapshot(resource_id).state
        return {
            "resource_id": resource_id,
            "lifecycle_id": state.lifecycle_id,
            "state_token": state.state_token,
            "status": state.status,
        }

    def prepare_file(
        self, resource_id: str, path: Path, *, inputs: tuple[tuple[str, str], ...] = ()
    ) -> PreparedReindex:
        snapshot = self.port.read_snapshot(resource_id)  # BEFORE any artifact I/O
        if not snapshot.state.live:
            raise ValueError("reindex_requires_live_resource")
        try:
            value = json.loads(_read(path).decode("utf-8"), object_pairs_hook=_pairs)
            if (
                type(value) is not dict
                or set(value) != {"contract", "version", "expected", "batch", "owner"}
                or value["contract"] != "mdrack.reindex-resource"
                or type(value["version"]) is not int
                or value["version"] != 1
                or not isinstance(value["owner"], str)
                or not value["owner"].strip()
            ):
                raise ValueError
            expected = {
                "resource_id": resource_id,
                "lifecycle_id": snapshot.state.lifecycle_id,
                "state_token": snapshot.state.state_token,
                "status": snapshot.state.status,
            }
            if value["expected"] != expected:
                raise ValueError
            batch = decode_prepared_resource_manifest(json.dumps(value["batch"]).encode())
            source_revision(batch.resource)
            for key, (rep, _) in _outputs(batch).items():
                info = rep.metadata["enrichment_v1"]
                assert isinstance(info, Mapping)
                retention = info.get("retention")
                if retention is not None and not isinstance(retention, str):
                    raise ValueError("artifact_retention_invalid")
                if retention in {"host_durable", "caller_owned", "cache"}:
                    if snapshot.batch is None or _slot_projection(batch, key) != _slot_projection(snapshot.batch, key):
                        raise StaleEnrichment("artifact_custody_requires_common_preparer")
                    old_spaces = {space.space_id: space for space in snapshot.batch.spaces}
                    ids = {unit.unit_id for unit in batch.units if unit.representation_id == rep.representation_id}
                    used = {vector.space_id for vector in batch.vectors if vector.unit_id in ids}
                    if any(old_spaces.get(space.space_id) != space for space in batch.spaces if space.space_id in used):
                        raise StaleEnrichment("artifact_profile_requires_common_preparer")
            if inputs:
                if snapshot.batch is None or any(key not in _outputs(snapshot.batch) for key in inputs):
                    raise ValueError
            job = replace(
                producer_job(snapshot, batch, owner=value["owner"], generation=source_revision(batch.resource)),
                inputs=inputs,
                publication_owner=self.port,
            )
            reconcile(snapshot, job.incoming, job.resource, owner=job.owner)
            return job
        except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
            raise ValueError("reindex_artifact_invalid_or_stale") from None

    def reindex_file(self, resource_id: str, path: Path, *, max_retries: int = 1) -> dict[str, object]:
        return dict(publish_reindex(self.port, self.prepare_file(resource_id, path), max_retries=max_retries))
