"""Pure app evidence only: no SQLite, filesystem artifact store or models."""
from dataclasses import replace

import pytest

from mdrack.application import enrichment as app
from mdrack.application.enrichment import merge
from mdrack_core.domain.batches import PreparedResourceBatch
from mdrack_core.domain.publication import PublicationState, ResourceSnapshot
from mdrack_core.domain.resources import (
    Facet,
    Locator,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchUnitRecord,
)
from mdrack_core.domain.vectors import EmbeddingSpaceRecord, VectorRecord


def native():
    resource = ResourceRecord('image', 'image', 'image/png', 'fixture', Locator('file', {'ref': 'image.png'}),
                              content_hash='prepared-not-source',
                              metadata={'source_revision': 'raw-1', 'independent': {'x': [1, 2]}})
    rep = RepresentationRecord('native', 'image', 'visual', 'image', metadata={'producer': 'native-original'})
    unit = SearchUnitRecord('native-u', 'image', 'native', 'whole_resource', 'image', None, resource.locator, 0)
    batch = PreparedResourceBatch(resource, (rep,), (unit,), (EmbeddingSpaceRecord('visual', 2, 'cosine', 'v1'),),
                                  (VectorRecord('native-u', 'visual', (0.25, 0.75)),),
                                  (ResourceFacet('image', Facet('topic', 'diagram'), 'user'),))
    state = PublicationState('image', 'published', 'life-1', 'token-1', 'fixture', resource.locator)
    return ResourceSnapshot(state, batch)


def lexical(base, text='cobalt', rid='ocr', purpose='ocr:whole', variant='default', revision='ocr-1', dependencies=()):
    info = {'purpose': purpose, 'variant': variant, 'source_revision': 'raw-1', 'artifact_revision': revision,
            'recipe': 'recipe-1', 'producer': 'fixture', 'dependencies': list(dependencies)}
    rep = RepresentationRecord(rid, 'image', 'ocr_text', 'text', text, metadata={'enrichment_v1': info})
    unit = SearchUnitRecord(rid + '-u', 'image', rid, 'whole_resource', 'text', text, base.batch.resource.locator, 0)
    return PreparedResourceBatch(base.batch.resource, (rep,), (unit,))


def test_add_lexical_preserves_complete_independent_graph():
    base = native()
    addition = lexical(base)
    result = merge(base, addition)
    assert result.batch.representations == base.batch.representations + addition.representations
    assert result.batch.units == base.batch.units + addition.units
    assert result.batch.vectors == base.batch.vectors
    assert result.batch.vectors[0] is base.batch.vectors[0]
    assert result.batch.spaces == base.batch.spaces
    assert result.batch.facets == base.batch.facets
    assert result.batch.resource is base.batch.resource
    assert result.added == ('ocr',)
    assert result.preserved == ('native',)
    assert result.replaced == ()


def snapshot(base, batch, token='token-2'):
    return ResourceSnapshot(replace(base.state, state_token=token), batch)


def test_repeat_checks_actual_graph_postconditions():
    base = native()
    addition = lexical(base)
    first = merge(base, addition)
    again = merge(snapshot(base, first.batch), addition)
    assert again.batch == first.batch
    assert again.added == again.replaced == ()
    # Same artifact revision is not sufficient if requested lexical text is absent.
    dense = replace(addition, representations=(replace(addition.representations[0], text=None),),
                    units=(replace(addition.units[0], text=None),), spaces=base.batch.spaces,
                    vectors=(VectorRecord('ocr-u', 'visual', (1, 0)),))
    dense_graph = merge(base, dense).batch
    upgraded = merge(snapshot(base, dense_graph), addition)
    assert upgraded.batch.units[-1].text == 'cobalt'
    assert upgraded.batch.vectors == base.batch.vectors


def test_correction_recipe_and_producer_replace_same_slot_not_native():
    base = native()
    first = merge(base, lexical(base)).batch
    corrected = lexical(base, 'azure', rid='new-producer-id', revision='manual-2')
    info = dict(corrected.representations[0].metadata['enrichment_v1'])
    info.update(recipe='different-recipe', producer='manual-editor')
    corrected = replace(corrected, representations=(
        replace(corrected.representations[0], metadata={'enrichment_v1': info}),))
    result = merge(snapshot(base, first), corrected)
    assert [r.representation_id for r in result.batch.representations] == ['native', 'new-producer-id']
    assert [u.text for u in result.batch.units] == [None, 'azure']
    assert result.batch.vectors[0] is base.batch.vectors[0]
    assert result.replaced == ('ocr',)


def test_explicit_variants_are_independent():
    base = native()
    first = merge(base, lexical(base)).batch
    other = lexical(base, 'alternate', 'alternate-id', variant='alternative')
    result = merge(snapshot(base, first), other)
    assert [r.representation_id for r in result.batch.representations] == ['native', 'ocr', 'alternate-id']


def dep(purpose, revision, variant='default'):
    return {'purpose': purpose, 'variant': variant, 'revision': revision}


def test_dependency_invalidation_is_transitive_and_keeps_independent_metadata():
    base = native()
    old = merge(base, lexical(base)).batch
    joint = lexical(base, 'joint', 'joint', purpose='joint:whole', revision='joint-1',
                    dependencies=(dep('ocr:whole', 'ocr-1'),))
    old = merge(snapshot(base, old), joint).batch
    downstream = lexical(base, 'summary', 'summary', purpose='summary:whole',
                         dependencies=(dep('joint:whole', 'joint-1'),))
    old = merge(snapshot(base, old), downstream).batch
    result = merge(snapshot(base, old), lexical(base, 'corrected', revision='manual-2'))
    assert [r.representation_id for r in result.batch.representations] == ['native', 'ocr']
    assert result.invalidated == ('joint', 'summary')
    assert result.batch.facets == base.batch.facets
    assert result.batch.resource.metadata == base.batch.resource.metadata
    assert result.batch.vectors[0] is base.batch.vectors[0]


@pytest.mark.parametrize('damage', ['empty', 'dangling', 'duplicate-slot', 'stale-source', 'stale-artifact'])
def test_reject_invalid_or_stale_additions(damage):
    base = native()
    addition = lexical(base)
    if damage == 'empty':
        addition = replace(addition, units=())
    elif damage == 'dangling':
        addition = replace(addition, units=(replace(addition.units[0], representation_id='missing'),))
    elif damage == 'duplicate-slot':
        second = lexical(base, rid='second')
        addition = replace(addition, representations=addition.representations + second.representations,
                           units=addition.units + second.units)
    else:
        info = dict(addition.representations[0].metadata['enrichment_v1'])
        if damage == 'stale-source':
            info['source_revision'] = 'raw-old'
        else:
            info['dependencies'] = [dep('absent', 'old')]
        addition = replace(addition, representations=(
            replace(addition.representations[0], metadata={'enrichment_v1': info}),))
    with pytest.raises(ValueError):
        merge(base, addition)


def test_preserves_every_independent_record_and_metadata_not_just_vector_count():
    base = native()
    independent = lexical(base, 'separate readable note', 'note', purpose='note:track-2:range-10-20')
    independent = replace(independent, units=(replace(independent.units[0], metadata={'custom': {'keep': [1, 2]}}),))
    combined = snapshot(base, merge(base, independent).batch)
    result = merge(combined, lexical(base))
    assert result.batch.representations[:-1] == combined.batch.representations
    assert result.batch.units[:-1] == combined.batch.units
    assert all(a is b for a, b in zip(result.batch.representations, combined.batch.representations))
    assert all(a is b for a, b in zip(result.batch.units, combined.batch.units))
    assert result.batch.resource is combined.batch.resource
    assert result.batch.facets == combined.batch.facets
    assert result.batch.vectors == combined.batch.vectors
    assert result.batch.spaces == combined.batch.spaces
    assert result.batch.units[1].metadata['custom']['keep'] == (1, 2)


def test_corrected_ocr_removes_stale_dense_joint_vector_but_not_native():
    base = native()
    old = snapshot(base, merge(base, lexical(base)).batch)
    joint = lexical(base, rid='joint', purpose='joint:whole', revision='joint-1',
                    dependencies=(dep('ocr:whole', 'ocr-1'),))
    joint = replace(joint, representations=(replace(joint.representations[0], text=None),),
                    units=(replace(joint.units[0], text=None),), spaces=base.batch.spaces,
                    vectors=(VectorRecord('joint-u', 'visual', (0.5, 0.5)),))
    old = snapshot(old, merge(old, joint).batch)
    result = merge(old, lexical(base, 'manually fixed', revision='manual-2'))
    assert result.invalidated == ('joint',)
    assert [u.unit_id for u in result.batch.units] == ['native-u', 'ocr-u']
    assert result.batch.vectors == base.batch.vectors
    assert result.batch.vectors[0] is base.batch.vectors[0]


class MemoryPublication:
    """Controlled port, not a storage implementation claim."""
    def __init__(self, initial):
        self.current = initial
        self.calls = 0
        self.before_cas = None

    def read_snapshot(self, resource_id):
        assert resource_id == self.current.state.resource_id
        return self.current

    def compare_and_publish(self, batch, expected):
        from mdrack_core.application.validation import validate_prepared_batch
        from mdrack_core.domain.publication import PublicationResult
        validate_prepared_batch(batch)
        self.calls += 1
        if self.before_cas:
            callback, self.before_cas = self.before_cas, None
            callback()
        if expected.state != self.current.state:
            return PublicationResult(False, self.current.state)
        self.current = snapshot(self.current, batch, f'commit-{self.calls}')
        return PublicationResult(True, self.current.state)


def prepared(base, addition=None, targets=(('ocr:whole', 'default'),), inputs=()):
    captured = app.capture(base, targets=targets, inputs=inputs)
    return app.prepare(captured, addition or lexical(base))


def test_capture_before_prepare_and_two_independent_additions_survive_conflict():
    base = native()
    first = prepared(base)
    second = prepared(base, lexical(base, 'caption', 'caption', purpose='caption:whole'),
                      targets=(('caption:whole', 'default'),))
    port = MemoryPublication(base)
    port.before_cas = lambda: app.publish(port, first)
    result = app.publish(port, second, max_retries=1)
    assert result.status == 'published'
    assert result.attempts == 2
    assert [r.representation_id for r in port.current.batch.representations] == ['native', 'ocr', 'caption']
    assert port.current.batch.vectors[0] is base.batch.vectors[0]
    again = app.publish(port, second)
    assert again.status == 'already_published'
    assert again.attempts <= 1
    assert port.calls == 4  # outer conflict, inner commit, outer retry, replay stale-CAS


@pytest.mark.parametrize('change', ['source', 'recreate', 'deleted', 'manual', 'binding'])
def test_old_preparation_cannot_obtain_new_authority(change):
    base = native()
    job = prepared(base)
    port = MemoryPublication(base)
    current = base
    if change == 'source':
        current = snapshot(base, replace(base.batch, resource=replace(base.batch.resource,
                           metadata={'source_revision': 'raw-2'})))
    elif change == 'recreate':
        current = replace(base, state=replace(base.state, lifecycle_id='life-2', state_token='new-life'))
    elif change == 'deleted':
        current = ResourceSnapshot(replace(base.state, status='deleted', state_token='deleted'), None)
    elif change == 'binding':
        locator = Locator('file', {'ref': 'other.png'})
        current = snapshot(base, replace(base.batch, resource=replace(base.batch.resource, locator=locator)))
        current = replace(current, state=replace(current.state, source_locator=locator))
    else:
        current = snapshot(base, merge(base, lexical(base, 'manual correction', revision='manual-2')).batch)
    port.current = current
    with pytest.raises(app.StaleEnrichment):
        app.publish(port, job)
    assert port.current == current
    assert port.calls == 1


def test_changed_immediate_artifact_rejects_old_joint_even_with_same_raw_hash():
    base = native()
    old = snapshot(base, merge(base, lexical(base)).batch)
    joint = lexical(old, 'joint', 'joint', purpose='joint:whole', dependencies=(dep('ocr:whole', 'ocr-1'),))
    job = prepared(old, joint, targets=(('joint:whole', 'default'),), inputs=(('ocr:whole', 'default'),))
    corrected = merge(old, lexical(old, 'manual', revision='manual-2')).batch
    port = MemoryPublication(snapshot(old, corrected, 'manual-token'))
    with pytest.raises(app.StaleEnrichment):
        app.publish(port, job)
    assert port.current.batch == corrected
    assert port.current.batch.resource.content_hash == old.batch.resource.content_hash


def test_retry_is_bounded_and_no_repreparation():
    base = native()
    job = prepared(base)
    port = MemoryPublication(base)
    def conflict_forever():
        port.current = replace(port.current, state=replace(port.current.state, state_token=f'race-{port.calls}'))
        port.before_cas = conflict_forever
    port.before_cas = conflict_forever
    result = app.publish(port, job, max_retries=2)
    assert result.status == 'conflict'
    assert result.attempts == port.calls == 3
    assert port.current.batch == base.batch
    for invalid in (-1, 4, True):
        with pytest.raises(ValueError):
            app.publish(port, job, max_retries=invalid)


def test_commit_without_response_replay_checks_postconditions():
    base = native()
    job = prepared(base)
    port = MemoryPublication(base)
    original = port.compare_and_publish
    def lost(batch, expected):
        original(batch, expected)
        raise RuntimeError('simulated response loss')
    port.compare_and_publish = lost
    with pytest.raises(RuntimeError):
        app.publish(port, job)
    committed = port.current
    port.compare_and_publish = original
    assert app.publish(port, job).status == 'already_published'
    assert port.current == committed


def ready_ocr(base, text='prepared cobalt', retention='caller_owned', manual=True, **changes):
    import hashlib
    fields = dict(reference='opaque-fixture-reference', resource_id=base.state.resource_id,
                  lifecycle_id=base.state.lifecycle_id, source_namespace=base.state.source_namespace,
                  source_locator=base.state.source_locator, source_revision='raw-1', purpose='ocr:whole',
                  artifact_revision='manual-1', producer='fixture-editor', recipe='ready-ocr-v1',
                  text_sha256=hashlib.sha256(text.encode()).hexdigest(), retention=retention, manual=manual)
    fields.update(changes)
    return app.ReadyOCR(app.DerivedArtifactRef(**fields), text)


def test_ready_ocr_has_checked_provenance_and_no_model_dependency():
    base = native()
    captured = app.capture(base, targets=(('ocr:whole', 'default'),))
    artifact = ready_ocr(base)
    result = app.prepare_ocr(captured, artifact, lexical=True, dense=False)
    assert result.status == 'prepared'
    port = MemoryPublication(base)
    assert app.publish(port, result.publication).status == 'published'
    added = port.current.batch.representations[-1]
    assert added.text == 'prepared cobalt'
    assert added.metadata['enrichment_v1']['artifact_revision'] == 'manual-1'
    assert added.metadata['enrichment_v1']['artifact_reference'] == 'opaque-fixture-reference'
    assert port.current.batch.units[-1].text == 'prepared cobalt'
    assert port.current.batch.vectors == base.batch.vectors
    assert port.current.batch.vectors[0] is base.batch.vectors[0]


def test_read_only_retention_does_not_secretly_publish_lexical():
    base = native()
    captured = app.capture(base, targets=(('ocr:whole', 'default'),))
    artifact = ready_ocr(base)
    result = app.prepare_ocr(captured, artifact, lexical=False, dense=False, require_durable=True)
    assert result.publication is None
    assert result.artifact is artifact
    assert result.status == 'read_only'
    # The pure contract accepts an explicit caller custody pledge, not cache durability.
    with pytest.raises(app.ArtifactRetentionUnavailable):
        app.prepare_ocr(captured, ready_ocr(base, retention='cache', manual=False), lexical=False,
                        dense=False, require_durable=True)
    with pytest.raises(app.ArtifactRetentionUnavailable):
        ready_ocr(base, retention='cache')


@pytest.mark.parametrize('field,value', [('source_revision', 'old'), ('lifecycle_id', 'old-life'),
                                        ('artifact_revision', ''), ('producer', ''), ('recipe', ''),
                                        ('text_sha256', '0' * 64), ('purpose', 'other:range')])
def test_artifact_origin_revision_and_binding_are_verified(field, value):
    base = native()
    captured = app.capture(base, targets=(('ocr:whole', 'default'),))
    with pytest.raises(ValueError):
        artifact = ready_ocr(base, **{field: value})
        app.prepare_ocr(captured, artifact)


def test_whole_image_projector_does_not_invent_range_coverage():
    base = native()
    captured = app.capture(base, targets=(('ocr:page-2', 'default'),))
    with pytest.raises(ValueError):
        app.prepare_ocr(captured, ready_ocr(base, purpose='ocr:page-2'))


def test_no_text_is_not_an_empty_search_graph():
    base = native()
    captured = app.capture(base, targets=(('ocr:whole', 'default'),))
    result = app.prepare_ocr(captured, ready_ocr(base, text='   '))
    assert result.status == 'no_text'
    assert result.publication is None
    assert base.batch.vectors[0].vector == (0.25, 0.75)


def test_idempotent_preparation_must_check_current_lifecycle_not_captured_postconditions():
    base = native()
    current = snapshot(base, merge(base, lexical(base)).batch)
    job = prepared(current, lexical(current))
    port = MemoryPublication(ResourceSnapshot(replace(current.state, status='deleted', state_token='deleted'), None))
    with pytest.raises(app.StaleEnrichment):
        app.publish(port, job)
    assert port.calls == 0


def test_same_raw_hash_does_not_hide_a_changed_source_revision():
    base = native()
    metadata = dict(base.batch.resource.metadata)
    metadata['raw_source_sha256'] = 'a' * 64
    base = snapshot(base, replace(base.batch, resource=replace(base.batch.resource, metadata=metadata)))
    job = prepared(base)
    metadata['source_revision'] = 'source-generation-2'
    current = snapshot(base, replace(base.batch, resource=replace(base.batch.resource, metadata=metadata)), 'source-2')
    assert current.batch.resource.metadata['raw_source_sha256'] == base.batch.resource.metadata['raw_source_sha256']
    port = MemoryPublication(current)
    with pytest.raises(app.StaleEnrichment):
        app.publish(port, job)
    assert port.current == current


def test_new_capture_cannot_preserve_known_output_from_an_old_source_revision():
    base = native()
    old = snapshot(base, merge(base, lexical(base)).batch)
    resource = replace(old.batch.resource, metadata={'source_revision': 'raw-2'})
    inconsistent = snapshot(old, replace(old.batch, resource=resource))
    with pytest.raises(app.StaleEnrichment):
        app.capture(inconsistent, targets=(('caption:whole', 'default'),))


def test_cyclic_immediate_dependencies_cannot_form_a_valid_app_graph():
    base = native()
    a = lexical(base, rid='a', purpose='a:whole', revision='a-1', dependencies=(dep('b:whole', 'b-1'),))
    b = lexical(base, rid='b', purpose='b:whole', revision='b-1', dependencies=(dep('a:whole', 'a-1'),))
    addition = replace(a, representations=a.representations + b.representations, units=a.units + b.units)
    with pytest.raises(ValueError):
        merge(base, addition)
