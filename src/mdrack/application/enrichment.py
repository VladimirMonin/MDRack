"""I/O-free, dependency-aware enrichment preparation over neutral core graphs.

The app-owned ``enrichment_v1`` metadata describes active output slots. Purpose
includes scope/track/range; producer, model and recipe never define a variant.
Legacy representations without this block are independent and preserved exactly.
"""
from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256

from mdrack_core.application.validation import validate_prepared_batch
from mdrack_core.domain.batches import PreparedResourceBatch
from mdrack_core.domain.common import JSONValue
from mdrack_core.domain.publication import ExpectedPublication, ResourceSnapshot
from mdrack_core.domain.resources import Locator, RepresentationRecord, ResourceRecord, SearchUnitRecord
from mdrack_core.ports.publication import PublicationPort

Slot = tuple[str, str]


@dataclass(frozen=True)
class CapturedInputs:
    snapshot: ResourceSnapshot
    source_revision: str
    targets: tuple[Slot, ...]
    inputs: tuple[Slot, ...]


@dataclass(frozen=True)
class PreparedEnrichment:
    captured: CapturedInputs
    addition: PreparedResourceBatch


@dataclass(frozen=True)
class EnrichmentPublication:
    status: str
    attempts: int
    merge: MergeResult | None = None


def capture(snapshot: ResourceSnapshot, *, targets: tuple[Slot, ...], inputs: tuple[Slot, ...] = ()) -> CapturedInputs:
    source = _source(snapshot)
    assert snapshot.batch is not None
    for slots in (targets, inputs):
        if not isinstance(slots, tuple) or len(set(slots)) != len(slots):
            raise ValueError('explicit unique slots required')
        for slot in slots:
            if not isinstance(slot, tuple) or len(slot) != 2:
                raise ValueError('slot must be (purpose, explicit variant)')
            _text(slot[0])
            _text(slot[1])
    if not targets:
        raise ValueError('at least one declared target required')
    outputs = _outputs(snapshot.batch)
    if any(key not in outputs for key in inputs):
        raise StaleEnrichment('immediate input unavailable')
    return CapturedInputs(snapshot, source, targets, inputs)


def prepare(captured: CapturedInputs, addition: PreparedResourceBatch) -> PreparedEnrichment:
    outputs = _outputs(addition)
    if set(outputs) != set(captured.targets):
        raise ValueError('prepared outputs differ from declared targets')
    for _, output in outputs.values():
        for key, revision in output.dependencies:
            if key not in captured.inputs:
                raise ValueError('immediate input was not captured before preparation')
            assert captured.snapshot.batch is not None
            if _outputs(captured.snapshot.batch)[key][1].artifact_revision != revision:
                raise StaleEnrichment('prepared input revision differs from capture')
    merge(captured.snapshot, addition)
    return PreparedEnrichment(captured, addition)


def publish(port: PublicationPort, prepared: PreparedEnrichment, *, max_retries: int = 1) -> EnrichmentPublication:
    if type(max_retries) is not int or not 0 <= max_retries <= 3:
        raise ValueError('max_retries must be an integer between zero and three')
    captured = prepared.captured
    if captured.snapshot.batch is None or prepared.addition.resource != captured.snapshot.batch.resource:
        raise ValueError('prepared resource differs from its original capture')
    current = captured.snapshot
    attempts = 0
    while True:
        _fresh(captured, current)
        assert current.batch is not None
        addition = replace(prepared.addition,resource=current.batch.resource)
        result = merge(current, addition)
        assert current.batch is not None
        if result.batch == current.batch:
            # Captured postconditions are not proof that they are still published.
            latest = port.read_snapshot(captured.snapshot.state.resource_id)
            _fresh(captured, latest)
            assert latest.batch is not None
            latest_result = merge(latest,replace(prepared.addition,resource=latest.batch.resource))
            if latest_result.batch == latest.batch:
                return EnrichmentPublication('already_published', attempts, latest_result)
            current = latest
            continue
        _targets_unchanged(captured, current)
        attempts += 1
        publication = port.compare_and_publish(result.batch, ExpectedPublication(current.state))
        if publication.applied:
            return EnrichmentPublication('published', attempts, result)
        if attempts > max_retries:
            return EnrichmentPublication('conflict', attempts, result)
        current = port.read_snapshot(captured.snapshot.state.resource_id)


def _fresh(captured: CapturedInputs, current: ResourceSnapshot) -> None:
    old, now = captured.snapshot.state, current.state
    if (not now.live or (old.resource_id, old.lifecycle_id, old.source_namespace, old.source_locator)
            != (now.resource_id, now.lifecycle_id, now.source_namespace, now.source_locator)):
        raise StaleEnrichment('lifecycle or source binding changed')
    if _source(current) != captured.source_revision:
        raise StaleEnrichment('source revision changed')
    assert captured.snapshot.batch is not None and current.batch is not None
    old_resource, new_resource = captured.snapshot.batch.resource,current.batch.resource
    def source_material(resource: ResourceRecord) -> tuple[object,...]:
        provenance = resource.metadata.get('provenance')
        raw = provenance.get('raw_source_sha256') if isinstance(provenance,Mapping) else None
        return resource.resource_kind,resource.media_type,raw
    if source_material(old_resource) != source_material(new_resource):
        raise StaleEnrichment('source material changed without a new generation')
    before, after = _outputs(captured.snapshot.batch), _outputs(current.batch)
    for key in captured.inputs:
        if (key not in after or before[key][1].artifact_revision != after[key][1].artifact_revision
                or _input_material(captured.snapshot.batch,key) != _input_material(current.batch,key)):
            raise StaleEnrichment('immediate input revision or material changed')


def _input_material(batch: PreparedResourceBatch, slot: Slot) -> tuple[object,...]:
    rep,output = _outputs(batch)[slot]
    info = rep.metadata['enrichment_v1']
    assert isinstance(info,Mapping)
    body = info.get('component_sha256',info.get('text_sha256',info.get('artifact_sha256')))
    units = tuple(u for u in batch.units if u.representation_id == rep.representation_id)
    ids = {u.unit_id for u in units}
    vectors = tuple((v.unit_id,v.space_id,struct.pack('<' + 'd' * len(v.vector),*v.vector))
                    for v in batch.vectors if v.unit_id in ids)
    spaces = tuple(s for s in batch.spaces if any(v[1] == s.space_id for v in vectors))
    shapes = tuple((u.unit_id,u.unit_kind,u.modality,u.evidence_locator,u.ordinal,
                    u.token_count,u.token_count_kind,u.metadata) for u in units)
    return (output.artifact_revision,body,info.get('material_fingerprint'),rep.representation_kind,
            rep.modality,rep.producer_fingerprint,rep.language,shapes,vectors,spaces)


def _targets_unchanged(captured: CapturedInputs, current: ResourceSnapshot) -> None:
    assert captured.snapshot.batch is not None and current.batch is not None
    before, after = _outputs(captured.snapshot.batch), _outputs(current.batch)
    for key in captured.targets:
        left, right = before.get(key), after.get(key)
        if (left is None) != (right is None):
            raise StaleEnrichment('active target changed during preparation')
        if left is not None and right is not None:
            if _projection(captured.snapshot.batch, left[0].representation_id) != _projection(
                    current.batch, right[0].representation_id):
                raise StaleEnrichment('active target revised during preparation')


@dataclass(frozen=True)
class DerivedArtifactRef:
    """Host/caller retention pledge, NOT a claim that this module persisted bytes."""
    reference: str
    resource_id: str
    lifecycle_id: str
    source_namespace: str
    source_locator: Locator
    source_revision: str
    purpose: str
    artifact_revision: str
    producer: str
    recipe: str
    text_sha256: str
    retention: str
    manual: bool = False

    def __post_init__(self) -> None:
        for value in (self.reference, self.resource_id, self.lifecycle_id, self.source_namespace,
                      self.source_revision, self.purpose, self.artifact_revision, self.producer, self.recipe):
            _text(value)
        if not isinstance(self.source_locator, Locator) or type(self.manual) is not bool:
            raise ValueError('invalid artifact binding')
        if (len(self.text_sha256) != 64 or any(c not in '0123456789abcdef' for c in self.text_sha256)):
            raise ValueError('artifact text digest must be SHA256')
        if self.retention not in {'cache', 'caller_owned', 'host_durable'}:
            raise ValueError('explicit artifact custody required')
        if self.manual and self.retention == 'cache':
            raise ArtifactRetentionUnavailable('manual artifact needs a non-cache owner')


@dataclass(frozen=True)
class ReadyOCR:
    reference: DerivedArtifactRef
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.reference, DerivedArtifactRef) or not isinstance(self.text, str):
            raise ValueError('ready OCR requires text and checked reference')
        if sha256(self.text.encode('utf-8')).hexdigest() != self.reference.text_sha256:
            raise ValueError('artifact text does not match reference digest')


@dataclass(frozen=True)
class OCRPreparation:
    artifact: ReadyOCR
    publication: PreparedEnrichment | None
    status: str


class ArtifactRetentionUnavailable(ValueError):
    """The requested retention cannot be supplied by an evictable cache."""


def prepare_ocr(captured: CapturedInputs, artifact: ReadyOCR, *, lexical: bool = True,
                dense: bool = False, require_durable: bool = False) -> OCRPreparation:
    """Prepare lexical-only or artifact-only OCR, without file/model/provider I/O.

    Retention is an explicit capability pledge supplied by the host/caller. The
    eventual public adapter must verify availability/durability or refuse it.
    Dense OCR needs a separate ready-vector projector and is not supported here.
    ``no_text`` does not withdraw a previously published slot.
    """
    if any(type(flag) is not bool for flag in (lexical, dense, require_durable)):
        raise ValueError('publication policy must use explicit booleans')
    if dense:
        raise ValueError('ready dense OCR projection is not supplied')
    ref = artifact.reference
    state = captured.snapshot.state
    assert captured.snapshot.batch is not None
    if ref.purpose != 'ocr:whole' or captured.snapshot.batch.resource.resource_kind != 'image':
        raise ValueError('this OCR projector supports whole-image coverage only')
    if (ref.resource_id, ref.lifecycle_id, ref.source_namespace, ref.source_locator, ref.source_revision) != (
            state.resource_id, state.lifecycle_id, state.source_namespace, state.source_locator,
            captured.source_revision):
        raise StaleEnrichment('ready artifact is bound to a different source or lifecycle')
    if len(captured.targets) != 1 or captured.targets[0][0] != ref.purpose or captured.inputs:
        raise ValueError('OCR must match one captured source-only purpose')
    if require_durable and ref.retention == 'cache':
        raise ArtifactRetentionUnavailable('durable retention is unavailable')
    if not artifact.text.strip():
        return OCRPreparation(artifact, None, 'no_text')
    if not lexical:
        return OCRPreparation(artifact, None, 'read_only')
    purpose, variant = captured.targets[0]
    key = sha256(json.dumps([state.resource_id, purpose, variant], ensure_ascii=True).encode()).hexdigest()
    rid = 'enrichment:' + key
    info: dict[str, JSONValue] = {'purpose': purpose, 'variant': variant, 'source_revision': ref.source_revision,
            'artifact_revision': ref.artifact_revision, 'recipe': ref.recipe, 'producer': ref.producer,
            'dependencies': (), 'artifact_reference': ref.reference, 'text_sha256': ref.text_sha256,
            'retention': ref.retention, 'manual': ref.manual}
    representation = RepresentationRecord(rid, state.resource_id, 'ocr_text', 'text', artifact.text,
                                          producer_fingerprint=ref.producer, metadata={'enrichment_v1': info})
    assert captured.snapshot.batch is not None
    unit = SearchUnitRecord(rid + ':text', state.resource_id, rid, 'whole_resource', 'text', artifact.text,
                            ref.source_locator, 0, metadata={'source_revision': ref.source_revision})
    addition = PreparedResourceBatch(captured.snapshot.batch.resource, (representation,), (unit,))
    return OCRPreparation(artifact, prepare(captured, addition), 'prepared')


class StaleEnrichment(ValueError):
    """Captured source, lifecycle or immediate input is no longer applicable."""


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('nonempty provenance value required')
    return value


@dataclass(frozen=True)
class Output:
    slot: Slot
    source_revision: str
    artifact_revision: str
    dependencies: tuple[tuple[Slot, str], ...]
    source_independent: bool = False


def _output(rep: RepresentationRecord) -> Output | None:
    if 'enrichment_v1' not in rep.metadata:
        return None
    info = rep.metadata['enrichment_v1']
    if not isinstance(info, Mapping):
        raise ValueError('invalid enrichment provenance')
    slot = (_text(info.get('purpose')), _text(info.get('variant')))
    _text(info.get('recipe'))
    _text(info.get('producer'))
    dependencies = info.get('dependencies')
    if not isinstance(dependencies, (tuple, list)):
        raise ValueError('explicit immediate dependencies required')
    parsed: list[tuple[Slot, str]] = []
    for dep in dependencies:
        if not isinstance(dep, Mapping):
            raise ValueError('invalid dependency')
        key = (_text(dep.get('purpose')), _text(dep.get('variant')))
        if key == slot or key in dict(parsed):
            raise ValueError('self or duplicate dependency')
        parsed.append((key, _text(dep.get('revision'))))
    independent = info.get('source_independent', False)
    if type(independent) is not bool or independent and parsed:
        raise ValueError('source-independent output cannot have source/artifact dependencies')
    return Output(slot, _text(info.get('source_revision')), _text(info.get('artifact_revision')),
                  tuple(parsed), independent)


def _required_output(rep: RepresentationRecord) -> Output:
    output = _output(rep)
    if output is None:
        raise ValueError('output must declare purpose/variant')
    return output


def _outputs(batch: PreparedResourceBatch) -> dict[Slot, tuple[RepresentationRecord, Output]]:
    result: dict[Slot, tuple[RepresentationRecord, Output]] = {}
    for rep in batch.representations:
        output = _output(rep)
        if output is not None:
            if output.slot in result:
                raise ValueError('multiple owners of active purpose/variant')
            result[output.slot] = rep, output
    return result


def _acyclic(outputs: dict[Slot, tuple[RepresentationRecord, Output]]) -> None:
    pending = {key: {dep for dep, _ in value[1].dependencies if dep in outputs}
               for key, value in outputs.items()}
    while pending:
        roots = {key for key, deps in pending.items() if not deps}
        if not roots:
            raise ValueError('cyclic immediate artifact dependencies')
        pending = {key: deps - roots for key, deps in pending.items() if key not in roots}


def invalid_dependencies(
    active: dict[Slot, tuple[RepresentationRecord, Output]], protected: frozenset[Slot] = frozenset(),
) -> set[Slot]:
    """One app dependency owner shared by enrichment and source reconciliation."""
    _acyclic(active)
    invalid: set[Slot] = set()
    while True:
        stale = {key for key, (_, output) in active.items() if key not in invalid and any(
            dependency in invalid or dependency not in active
            or active[dependency][1].artifact_revision != revision for dependency, revision in output.dependencies)}
        if not stale:
            return invalid
        if stale & protected:
            raise StaleEnrichment('immediate artifact revision changed')
        invalid.update(stale)


def independent_output(rep: RepresentationRecord) -> bool:
    output = _output(rep)
    independent = rep.metadata.get('source_independent', False)
    if type(independent) is not bool:
        raise ValueError('explicit source independence must be boolean')
    if independent and output is not None and output.dependencies:
        raise ValueError('independent output has immediate dependencies')
    return independent or output is not None and output.source_independent


def prune_graph(batch: PreparedResourceBatch) -> PreparedResourceBatch | None:
    """Drop stale derived outputs, then validate the actual remaining graph."""
    outputs = _outputs(batch)
    generation = batch.resource.metadata.get('source_revision')
    stale_source = {key for key, (rep, output) in outputs.items()
                    if not independent_output(rep) and output.source_revision != generation}
    active = {key: value for key, value in outputs.items() if key not in stale_source}
    stale = stale_source | invalid_dependencies(active)
    removed = {outputs[key][0].representation_id for key in stale}
    reps = tuple(r for r in batch.representations if r.representation_id not in removed)
    if not reps:
        return None
    units = tuple(u for u in batch.units if u.representation_id not in removed)
    ids = {u.unit_id for u in units}
    result = PreparedResourceBatch(batch.resource, reps, units, batch.spaces,
                                  tuple(v for v in batch.vectors if v.unit_id in ids), batch.facets)
    validate_prepared_batch(result)
    return result


def _source(snapshot: ResourceSnapshot) -> str:
    if not snapshot.state.live or snapshot.batch is None:
        raise StaleEnrichment('published source required')
    validate_prepared_batch(snapshot.batch)
    resource = snapshot.batch.resource
    if (resource.source_namespace, resource.locator) != (
            snapshot.state.source_namespace, snapshot.state.source_locator):
        raise StaleEnrichment('source binding differs from graph')
    # content_hash may identify prepared evidence, NOT the raw source.
    source = _text(resource.metadata.get('source_revision'))
    if any(output.source_revision != source and not independent_output(rep)
           for rep, output in _outputs(snapshot.batch).values()):
        raise StaleEnrichment('existing output belongs to an old source revision')
    return source


@dataclass(frozen=True)
class MergeResult:
    batch: PreparedResourceBatch
    added: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    invalidated: tuple[str, ...] = ()


def _projection(batch: PreparedResourceBatch, rid: str) -> tuple[object, ...]:
    reps = tuple(r for r in batch.representations if r.representation_id == rid)
    units = tuple(u for u in batch.units if u.representation_id == rid)
    unit_ids = {u.unit_id for u in units}
    vectors = tuple(v for v in batch.vectors if v.unit_id in unit_ids)
    return reps, units, vectors


def merge(current: ResourceSnapshot, addition: PreparedResourceBatch) -> MergeResult:
    """Replace declared slots; discard only their transitively stale dependents.

    This operation does not grant publication authority: callers must capture
    before preparation and use ``publish`` for freshness-checked CAS/remerge.
    Both input graphs and the final graph use the existing core validator.
    """
    source = _source(current)
    assert current.batch is not None
    old = current.batch
    validate_prepared_batch(addition)
    if addition.resource != old.resource or addition.facets:
        raise ValueError('addition must preserve resource metadata and use no facet mutations')
    before, incoming = _outputs(old), _outputs(addition)
    if len(incoming) != len(addition.representations):
        raise ValueError('each added representation must declare its slot')
    for _, output in incoming.values():
        if output.source_revision != source:
            raise StaleEnrichment('artifact source revision changed')
    changed = {slot for slot, (rep, _) in incoming.items()
               if slot not in before or _projection(addition, rep.representation_id)
               != _projection(old, before[slot][0].representation_id)}
    active = dict(before)
    active.update(incoming)
    _acyclic(active)
    invalid_slots = invalid_dependencies(active, frozenset(incoming))
    removed = {before[s][0].representation_id for s in changed | invalid_slots if s in before}
    kept_reps = tuple(r for r in old.representations if r.representation_id not in removed)
    new_reps = tuple(r for r in addition.representations if _required_output(r).slot in changed)
    new_ids = {r.representation_id for r in new_reps}
    kept_units = tuple(u for u in old.units if u.representation_id not in removed)
    new_units = tuple(u for u in addition.units if u.representation_id in new_ids)
    kept_unit_ids = {u.unit_id for u in kept_units}
    new_unit_ids = {u.unit_id for u in new_units}
    spaces = {s.space_id: s for s in old.spaces}
    for space in addition.spaces:
        if space.space_id in spaces and space != spaces[space.space_id]:
            raise ValueError('cannot redefine preserved embedding space')
        spaces.setdefault(space.space_id, space)
    batch = PreparedResourceBatch(
        old.resource, kept_reps + new_reps, kept_units + new_units, tuple(spaces.values()),
        tuple(v for v in old.vectors if v.unit_id in kept_unit_ids)
        + tuple(v for v in addition.vectors if v.unit_id in new_unit_ids), old.facets,
    )
    validate_prepared_batch(batch)
    return MergeResult(
        batch,
        tuple(r.representation_id for r in new_reps if _required_output(r).slot not in before),
        tuple(r.representation_id for r in old.representations
              if r.representation_id in removed and _required_output(r).slot not in invalid_slots),
        tuple(r.representation_id for r in kept_reps),
        tuple(r.representation_id for r in old.representations
              if r.representation_id in removed and _required_output(r).slot in invalid_slots),
    )
