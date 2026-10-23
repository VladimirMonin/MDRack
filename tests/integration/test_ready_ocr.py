"""Ready OCR public local-component witnesses; no inference or external data."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.config.models import MDRackConfig
from mdrack.public_api import MDRackEngine
from mdrack_core import (
    EmbeddingSpaceRecord,
    Facet,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)


def existing(root: Path):
    root.mkdir(exist_ok=True)
    assert CliRunner().invoke(main, ['--root', str(root), 'init']).exit_code == 0
    image = root / 'image.png'
    image.write_bytes(b'\x89PNG\r\n\x1a\nsynthetic-ready-native')
    locator = Locator('local_image', {'source_ref': 'image.png'})
    resource = ResourceRecord('image', 'image', 'image/png', 'fixture', locator, 'prepared-not-source',
                              metadata={'source_revision': 'source-generation-1', 'independent': {'keep': [1, 2]},
                                        'provenance': {'raw_source_sha256':
                                                       'sha256:' + hashlib.sha256(image.read_bytes()).hexdigest()}})
    reps = (RepresentationRecord('native', 'image', 'visual', 'image', metadata={'original': 'native'}),
            RepresentationRecord('note', 'image', 'caption_text', 'text', 'independent caption',
                                 metadata={'independent': {'producer': 'retained'}}))
    units = (SearchUnitRecord('native-u', 'image', 'native', 'whole_resource', 'image', None, locator, 0),
             SearchUnitRecord('note-u', 'image', 'note', 'whole_resource', 'text', 'independent caption', locator, 0,
                              metadata={'original': 'unit'}))
    batch = PreparedResourceBatch(resource, reps, units, (EmbeddingSpaceRecord('native-space', 2, 'dot', 'native-v1'),),
                                  (VectorRecord('native-u', 'native-space', (-0.0, 0.75)),
                                   VectorRecord('note-u', 'native-space', (0.625, -0.125))),
                                  (ResourceFacet('image', Facet('topic', 'synthetic'), 'user'),))
    engine = MDRackEngine(root=root, config=MDRackConfig())
    engine.storage.resource_store.replace_resource(batch)
    return engine, engine.storage.resource_store.read_snapshot('image').batch, image


def artifact(root, snapshot, *, text='cobaltmanual', revision='manual-1', producer='manual-editor',
             recipe='ready-v1', name='ready.json'):
    payload = {'contract': 'mdrack.ready-ocr', 'version': 1, 'reference': 'opaque-manual-artifact',
               'resource_id': 'image', 'lifecycle_id': snapshot.state.lifecycle_id,
               'source_namespace': 'fixture',
               'source_locator': {'kind': 'local_image', 'payload': {'source_ref': 'image.png'}},
               'source_revision': snapshot.batch.resource.metadata['source_revision'],
               'artifact_revision': revision, 'producer': producer, 'recipe': recipe, 'purpose': 'ocr:whole',
               'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'text': text, 'manual': True}
    path = root / name
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


def found(engine, text):
    return engine.search_images_text(text).to_dict()['results']


def test_public_engine_and_registered_cli_real_fts_preserve_native(tmp_path):
    engine, before, _ = existing(tmp_path)
    try:
        path = artifact(tmp_path, engine.storage.resource_store.read_snapshot('image'))
        result = engine.enrich_ready_ocr('image', path, source_path=tmp_path / 'image.png')
        assert result['publication_status'] == 'published'
        assert result['source_validation'] == 'selected_source_and_catalog'
        current = engine.storage.resource_store.read_snapshot('image').batch
        assert current.resource == before.resource
        kept_reps = {r.representation_id: r for r in current.representations
                     if r.representation_id in {'native', 'note'}}
        assert kept_reps == {r.representation_id: r for r in before.representations}
        assert {u.unit_id: u for u in current.units if u.unit_id in {'native-u', 'note-u'}} == {
            u.unit_id: u for u in before.units}
        assert current.vectors == before.vectors
        import struct
        assert [struct.pack('<2d', *v.vector) for v in current.vectors] == [
            struct.pack('<2d', *v.vector) for v in before.vectors]
        assert current.facets == before.facets
        assert current.spaces == before.spaces
        assert [x['resource_id'] for x in found(engine, 'cobaltmanual')] == ['image']
    finally:
        engine.close()
    cli = CliRunner().invoke(main, ['--root', str(tmp_path), 'resource', 'enrich-ocr', 'image', str(path),
                                    '--source-image', str(tmp_path / 'image.png')])
    assert cli.exit_code == 0, cli.output
    assert json.loads(cli.output)['data']['publication_status'] == 'already_published'


def test_normal_image_ingest_rescan_reads_source_and_preserves_enrichment_without_models(tmp_path):
    import asyncio
    engine, _, image = existing(tmp_path)
    try:
        path = artifact(tmp_path, engine.storage.resource_store.read_snapshot('image'))
        engine.enrich_ready_ocr('image', path)
        before = engine.storage.resource_store.read_snapshot('image')
        result = asyncio.run(engine.ingest_image(image, resource_id='image', source_namespace='fixture',
                                                source_ref='image.png'))
        after = engine.storage.resource_store.read_snapshot('image')
        assert after.batch == before.batch
        import struct
        assert [struct.pack('<2d', *v.vector) for v in after.batch.vectors] == [
            struct.pack('<2d', *v.vector) for v in before.batch.vectors]
        assert after.state.lifecycle_id == before.state.lifecycle_id
        assert result.resource_id == 'image'
        assert found(engine, 'cobaltmanual')
        image.write_bytes(b'\x89PNG\r\n\x1a\nchanged-source')
        asyncio.run(engine.ingest_image(image, resource_id='image', source_namespace='fixture',
                                        source_ref='image.png'))
        changed = engine.storage.resource_store.read_snapshot('image')
        assert changed.state.status == 'live_without_projections'
        assert changed.state.lifecycle_id == after.state.lifecycle_id
        assert changed.state.state_token != after.state.state_token
        assert changed.batch is None
        assert not found(engine, 'cobaltmanual')
        with pytest.raises(ValueError):
            engine.enrich_ready_ocr('image', path)
    finally:
        engine.close()


def test_correction_replaces_active_fts_and_invalidates_only_dependent_dense(tmp_path):
    from mdrack.application.enrichment import capture, prepare, publish
    engine, native, _ = existing(tmp_path)
    try:
        store = engine.storage.resource_store
        old_path = artifact(tmp_path, store.read_snapshot('image'))
        old_bytes = old_path.read_bytes()
        engine.enrich_ready_ocr('image', old_path)
        before = store.read_snapshot('image')
        metadata = {'enrichment_v1': {'purpose': 'joint:whole', 'variant': 'default',
                    'source_revision': 'source-generation-1', 'artifact_revision': 'joint-1',
                    'producer': 'fixture-joint', 'recipe': 'joint-v1',
                    'dependencies': [{'purpose': 'ocr:whole', 'variant': 'default', 'revision': 'manual-1'}]}}
        joint = PreparedResourceBatch(before.batch.resource,
                    (RepresentationRecord('joint', 'image', 'visual', 'image', metadata=metadata),),
                    (SearchUnitRecord('joint-u', 'image', 'joint', 'whole_resource', 'image', None,
                                      native.resource.locator, 0),), native.spaces,
                    (VectorRecord('joint-u', 'native-space', (0.25, 0.5)),))
        captured = capture(before, targets=(('joint:whole', 'default'),), inputs=(('ocr:whole', 'default'),))
        delayed_joint = prepare(captured, joint)
        assert publish(store, delayed_joint).status == 'published'
        corrected = artifact(tmp_path, store.read_snapshot('image'), text='azurecorrected', revision='manual-2',
                             producer='different-editor', recipe='different-recipe', name='corrected.json')
        assert engine.enrich_ready_ocr('image', corrected)['publication_status'] == 'published'
        current = store.read_snapshot('image')
        assert not found(engine, 'cobaltmanual')
        assert found(engine, 'azurecorrected')
        assert [v for v in current.batch.vectors] == list(native.vectors)
        assert 'joint' not in {r.representation_id for r in current.batch.representations}
        assert len([r for r in current.batch.representations if r.representation_kind == 'ocr_text']) == 1
        assert current.batch.facets == native.facets
        assert current.batch.resource == native.resource
        with pytest.raises(ValueError):
            publish(store, delayed_joint)
        assert store.read_snapshot('image') == current
        repeat = engine.enrich_ready_ocr('image', corrected)
        assert repeat['publication_status'] == 'already_published'
        assert store.read_snapshot('image') == current
        assert old_path.read_bytes() == old_bytes  # corrected attempt must not overwrite its predecessor
    finally:
        engine.close()


def test_two_independent_public_additions_file_connections_bounded_cas_no_reprepare(tmp_path, monkeypatch):
    from mdrack.application.ready_ocr import ReadyOCRService
    first, native, _ = existing(tmp_path)
    second = MDRackEngine(root=tmp_path, config=MDRackConfig())
    try:
        initial = first.storage.resource_store.read_snapshot('image')
        a = first.prepare_ready_ocr('image', artifact(tmp_path, initial, name='one.json'))
        b = second.prepare_ready_ocr('image', artifact(tmp_path, initial, text='independentvariant', name='two.json'),
                                     variant='intentional-alternative')
        def forbidden(*args, **kwargs):
            pytest.fail('publication repeated artifact preparation')
        monkeypatch.setattr(ReadyOCRService, 'prepare', forbidden)
        assert first.publish_ready_ocr(a)['publication_status'] == 'published'
        result = second.publish_ready_ocr(b, max_retries=1)
        assert result['publication_status'] == 'published'
        assert result['attempt_count'] == 2
        current = second.storage.resource_store.read_snapshot('image').batch
        variants = {r.metadata['enrichment_v1']['variant'] for r in current.representations
                    if 'enrichment_v1' in r.metadata}
        assert variants == {'default', 'intentional-alternative'}
        assert found(second, 'cobaltmanual') and found(first, 'independentvariant')
        assert current.vectors == native.vectors
        assert current.facets == native.facets
        assert current.resource == native.resource
    finally:
        second.close()
        first.close()


def test_parallel_public_additions_use_barrier_and_thread_owned_file_connections(tmp_path, monkeypatch):
    import threading

    from mdrack.application.ready_ocr import ReadyOCRService
    seed, native, _ = existing(tmp_path)
    try:
        initial = seed.storage.resource_store.read_snapshot('image')
        one = artifact(tmp_path, initial, name='parallel-one.json')
        two = artifact(tmp_path, initial, text='parallelvariant', name='parallel-two.json')
    finally:
        seed.close()
    barrier = threading.Barrier(2)
    results, errors, preparations = [], [], []
    original_prepare = ReadyOCRService.prepare
    def counted(self, *args, **kwargs):
        preparations.append(threading.get_ident())
        return original_prepare(self, *args, **kwargs)
    monkeypatch.setattr(ReadyOCRService, 'prepare', counted)
    def work(path, variant):
        try:
            with MDRackEngine(root=tmp_path, config=MDRackConfig()) as engine:
                operation = engine.prepare_ready_ocr('image', path, variant=variant)
                barrier.wait(5)
                results.append(engine.publish_ready_ocr(operation, max_retries=1))
        except BaseException as error:
            errors.append(error)
    threads = [threading.Thread(target=work, args=(one, 'default')),
               threading.Thread(target=work, args=(two, 'intentional-alternative'))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert all(not thread.is_alive() for thread in threads)
    assert not errors
    assert len(preparations) == 2 and len(set(preparations)) == 2
    assert sorted(r['attempt_count'] for r in results) == [1, 2]
    assert all(r['publication_status'] == 'published' for r in results)
    with MDRackEngine(root=tmp_path, config=MDRackConfig()) as reopened:
        current = reopened.storage.resource_store.read_snapshot('image')
        assert current.batch.vectors == native.vectors
        assert current.batch.facets == native.facets
        assert current.batch.resource == native.resource
        assert found(reopened, 'cobaltmanual') and found(reopened, 'parallelvariant')
        assert {r.metadata['enrichment_v1']['variant'] for r in current.batch.representations
                if 'enrichment_v1' in r.metadata} == {'default', 'intentional-alternative'}


@pytest.mark.parametrize('change', ['source-same-raw-hash', 'manual', 'delete-recreate'])
def test_public_delayed_result_rejects_changed_authority(tmp_path, change):
    engine, native, _ = existing(tmp_path)
    try:
        store = engine.storage.resource_store
        op = engine.prepare_ready_ocr('image', artifact(tmp_path, store.read_snapshot('image')))
        if change == 'source-same-raw-hash':
            metadata = dict(native.resource.metadata)
            metadata['source_revision'] = 'source-generation-2'
            store.replace_resource(replace(native, resource=replace(native.resource, metadata=metadata)))
        elif change == 'manual':
            engine.enrich_ready_ocr('image', artifact(tmp_path, store.read_snapshot('image'), text='newmanual',
                                                     revision='manual-2', name='manual2.json'))
        else:
            engine.delete_resource('image')
            store.replace_resource(native)
        current = store.read_snapshot('image')
        with pytest.raises(ValueError):
            engine.publish_ready_ocr(op)
        assert store.read_snapshot('image') == current
        assert not found(engine, 'cobaltmanual')
        assert current.batch.vectors == native.vectors
    finally:
        engine.close()


def test_selected_source_change_after_preparation_refuses_without_model_or_temp_leak(tmp_path):
    engine, native, image = existing(tmp_path)
    try:
        store = engine.storage.resource_store
        initial = store.read_snapshot('image')
        path = artifact(tmp_path, initial)
        op = engine.prepare_ready_ocr('image', path, source_path=image)
        image.write_bytes(b'\x89PNG\r\n\x1a\nchanged-original')
        with pytest.raises(ValueError):
            engine.publish_ready_ocr(op)
        assert store.read_snapshot('image') == initial
        assert not found(engine, 'cobaltmanual')
        assert store.read_snapshot('image').batch.vectors == native.vectors
    finally:
        engine.close()


def test_real_file_conflicts_exhaust_bounded_retry_without_duplicating_graph(tmp_path, monkeypatch):
    engine, native, _ = existing(tmp_path)
    contender = MDRackEngine(root=tmp_path, config=MDRackConfig())
    try:
        store = engine.storage.resource_store
        op = engine.prepare_ready_ocr('image', artifact(tmp_path, store.read_snapshot('image')))
        original = store.compare_and_publish
        calls = 0
        def raced(batch, expected):
            nonlocal calls
            calls += 1
            contender.storage.resource_store.replace_resource(native)
            return original(batch, expected)
        monkeypatch.setattr(store, 'compare_and_publish', raced)
        result = engine.publish_ready_ocr(op, max_retries=2)
        assert result['publication_status'] == 'conflict'
        assert result['attempt_count'] == calls == 3
        assert store.read_snapshot('image').batch == native
        assert not found(engine, 'cobaltmanual')
    finally:
        contender.close()
        engine.close()


@pytest.mark.parametrize('damage', ['empty', 'malformed', 'unknown-field', 'digest', 'cache-pledge', 'source-binding'])
def test_ready_file_invalid_inputs_refuse_without_changing_graph(tmp_path, damage):
    engine, _, _ = existing(tmp_path)
    try:
        store = engine.storage.resource_store
        initial = store.read_snapshot('image')
        path = artifact(tmp_path, initial)
        if damage == 'empty':
            path.write_bytes(b'')
        elif damage == 'malformed':
            path.write_bytes(b'{PRIVATE_ARTIFACT_CONTENT')
        else:
            data = json.loads(path.read_text())
            if damage == 'unknown-field':
                data['unknown'] = 'PRIVATE_ARTIFACT_CONTENT'
            elif damage == 'digest':
                data['text_sha256'] = '0' * 64
            elif damage == 'cache-pledge':
                data['retention'] = 'host_durable'
            else:
                data['source_revision'] = 'other-generation'
            path.write_text(json.dumps(data))
        with pytest.raises(ValueError):
            engine.enrich_ready_ocr('image', path)
        assert store.read_snapshot('image') == initial
        assert not found(engine, 'cobaltmanual')
    finally:
        engine.close()


def test_public_binding_and_no_text_preserve_graph_and_repeat_variant(tmp_path):
    engine, _, _ = existing(tmp_path)
    try:
        initial = engine.storage.resource_store.read_snapshot('image')
        binding = engine.ready_ocr_binding('image')
        assert binding['lifecycle_id'] == initial.state.lifecycle_id
        assert binding['source_revision'] == 'source-generation-1'
        assert 'state_token' not in binding
        path = artifact(tmp_path, initial, text='   ')
        result = engine.enrich_ready_ocr('image', path)
        assert result['artifact_status'] == 'no_text'
        assert result['publication_status'] == 'not_requested'
        assert engine.storage.resource_store.read_snapshot('image') == initial
    finally:
        engine.close()
    cli = CliRunner().invoke(main, ['--root', str(tmp_path), 'resource', 'ocr-binding', 'image'])
    assert cli.exit_code == 0
    assert json.loads(cli.output)['data'] == binding


def test_capture_precedes_artifact_io_and_changed_source_during_io_cannot_publish(tmp_path, monkeypatch):
    import mdrack.application.ready_ocr as ready
    engine, native, _ = existing(tmp_path)
    try:
        store = engine.storage.resource_store
        path = artifact(tmp_path, store.read_snapshot('image'))
        original = ready._read
        read_count = 0
        def racing_read(selected):
            nonlocal read_count
            read_count += 1
            data = original(selected)
            if read_count == 1:
                metadata = dict(native.resource.metadata)
                metadata['source_revision'] = 'generation-during-io'
                store.replace_resource(replace(native, resource=replace(native.resource, metadata=metadata)))
            return data
        monkeypatch.setattr(ready, '_read', racing_read)
        with pytest.raises(ValueError):
            engine.enrich_ready_ocr('image', path)
        assert not found(engine, 'cobaltmanual')
        assert store.read_snapshot('image').batch.vectors == native.vectors
    finally:
        engine.close()


def test_nonempty_safe_log_bytes_and_sink_failure_do_not_change_commit(tmp_path):
    import io
    import logging
    engine, _, _ = existing(tmp_path)
    logger = logging.getLogger('mdrack.application.ready_ocr')
    stream = io.StringIO()
    safe = logging.StreamHandler(stream)
    safe.setFormatter(logging.Formatter('%(message)s'))
    class Broken(logging.Handler):
        def emit(self, record):
            raise OSError('PRIVATE_SINK_FAILURE')
    broken = Broken()
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(safe)
    logger.addHandler(broken)
    try:
        path = artifact(tmp_path, engine.storage.resource_store.read_snapshot('image'))
        result = engine.enrich_ready_ocr('image', path)
        assert result['publication_status'] == 'published'
        safe.flush()
        output = stream.getvalue()
        assert 'enrichment.completed' in output and '"provider_call_count":0' in output
        assert '"extraction_call_count":0' in output and '"publication_status":"published"' in output
        assert 'cobaltmanual' not in output and str(path) not in output and 'PRIVATE_' not in output
        assert found(engine, 'cobaltmanual')
        path.unlink()  # published SQLite text and native vector do not depend on the caller file
        assert found(engine, 'cobaltmanual')
    finally:
        logger.removeHandler(broken)
        logger.removeHandler(safe)
        logger.setLevel(old_level)
        engine.close()
    reopened = MDRackEngine(root=tmp_path, config=MDRackConfig())
    try:
        assert found(reopened, 'cobaltmanual')
    finally:
        reopened.close()


def test_legacy_missing_source_generation_refuses_without_mutating_readable_graph(tmp_path):
    engine, native, _ = existing(tmp_path)
    try:
        store = engine.storage.resource_store
        initial = store.read_snapshot('image')
        path = artifact(tmp_path, initial)
        metadata = dict(native.resource.metadata)
        del metadata['source_revision']
        store.replace_resource(replace(native, resource=replace(native.resource, metadata=metadata)))
        legacy = store.read_snapshot('image')
        with pytest.raises(ValueError):
            engine.enrich_ready_ocr('image', path)
        assert store.read_snapshot('image') == legacy
        assert found(engine, 'independent')
        assert legacy.batch.vectors == native.vectors
    finally:
        engine.close()


def test_actual_artifact_custody_read_only_refusal_and_cleanup_privacy(tmp_path, caplog):
    import logging
    engine, native, _ = existing(tmp_path)
    try:
        store = engine.storage.resource_store
        initial = store.read_snapshot('image')
        path = artifact(tmp_path, initial)
        with caplog.at_level(logging.INFO):
            result = engine.enrich_ready_ocr('image', path, lexical=False)
        assert result['publication_status'] == 'not_requested'
        assert result['retention'] == 'caller_owned_verified'
        assert store.read_snapshot('image') == initial
        assert not found(engine, 'cobaltmanual')
        with pytest.raises(ValueError):
            engine.enrich_ready_ocr('image', path, lexical=False, require_durable=True)
        operation = engine.prepare_ready_ocr('image', path)
        path.unlink()
        with pytest.raises(ValueError):
            engine.publish_ready_ocr(operation)
        assert store.read_snapshot('image') == initial
        assert store.read_snapshot('image').batch.vectors == native.vectors
        assert 'cobaltmanual' not in caplog.text
        assert str(tmp_path) not in caplog.text
        failed = CliRunner().invoke(main, ['--root', str(tmp_path), 'resource', 'enrich-ocr', 'image', str(path)])
        assert failed.exit_code == 1
        payload = json.loads(failed.output)
        assert payload['error']['code'] == 'READY_OCR_ERROR'
        assert str(path) not in failed.output and 'cobaltmanual' not in failed.output
        # Refusal closes its own engine and leaves the next ordinary operation useful.
        new = artifact(tmp_path, initial, name='new.json')
        assert engine.enrich_ready_ocr('image', new)['publication_status'] == 'published'
        assert found(engine, 'cobaltmanual')
    finally:
        engine.close()


def legacy_automatic(root, snapshot, *, text, revision, name='automatic.json'):
    path = artifact(root, snapshot, text=text, revision=revision, producer='automatic-fixture', name=name)
    value = json.loads(path.read_text(encoding='utf-8'))
    value['manual'] = False
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


def dependent_ocr_vector(engine):
    """Ready vector is an external-edge control, not pixel/model evidence."""
    from mdrack.application.enrichment import capture, prepare, publish
    store = engine.storage.resource_store
    before = store.read_snapshot('image')
    ocr = next(r for r in before.batch.representations if r.representation_kind == 'ocr_text')
    info = ocr.metadata['enrichment_v1']
    metadata = {'enrichment_v1': {
        'purpose': 'joint:whole', 'variant': 'default',
        'source_revision': info['source_revision'], 'artifact_revision': 'joint-1',
        'producer': 'ready-vector-control', 'recipe': 'fixture-only',
        'dependencies': [{'purpose': 'ocr:whole', 'variant': 'default',
                          'revision': info['artifact_revision']}]}}
    addition = PreparedResourceBatch(
        before.batch.resource,
        (RepresentationRecord('dependent', 'image', 'visual', 'image', metadata=metadata),),
        (SearchUnitRecord('dependent-u', 'image', 'dependent', 'whole_resource', 'image', None,
                          before.batch.resource.locator, 0),),
        (EmbeddingSpaceRecord('dependency-control', 2, 'dot', 'ready-v1'),),
        (VectorRecord('dependent-u', 'dependency-control', (0.25, 0.5)),))
    captured = capture(before, targets=(('joint:whole', 'default'),), inputs=(('ocr:whole', 'default'),))
    assert publish(store, prepare(captured, addition)).status == 'published'
    return store.read_snapshot('image')


def eligible_dependent(engine):
    from mdrack_core.application.retrieval import RetrievalService
    from mdrack_core.domain import SearchRequest, SearchScope, VectorBranch
    request = SearchRequest((), (VectorBranch('ready', 'dependency-control', (1.0, 1.0),
                                             expected_fingerprint='ready-v1'),), SearchScope(), 'unit', 20)
    result = RetrievalService(engine.storage.resource_store).search(request)
    return [candidate.unit_id for item in result.items for candidate in item.evidence]


def assert_legacy_state(engine, snapshot, *, text, absent, dependent, manual):
    import struct
    current = engine.storage.resource_store.read_snapshot('image')
    assert current == snapshot
    ocr = next(r for r in current.batch.representations if r.representation_kind == 'ocr_text')
    info = ocr.metadata['enrichment_v1']
    assert ocr.text == text and info['manual'] is manual
    assert info['text_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    unit = next(u for u in current.batch.units if u.representation_id == ocr.representation_id)
    assert engine.storage.resource_store.read_unit(unit.unit_id).text == text
    assert engine.read_unit(unit.unit_id)['text'] == text
    assert found(engine, text) and not found(engine, absent)
    vector = engine.storage.resource_store.read_vector('dependent-u', 'dependency-control')
    assert eligible_dependent(engine) == (['dependent-u'] if dependent else [])
    if dependent:
        assert struct.pack('<2d', *vector.vector) == struct.pack('<2d', 0.25, 0.5)
        rep = next(r for r in current.batch.representations if r.representation_id == 'dependent')
        dependency = rep.metadata['enrichment_v1']['dependencies'][0]
        assert dependency['revision'] == info['artifact_revision']
        assert rep.metadata['enrichment_v1']['source_revision'] == info['source_revision']
    else:
        assert vector is None and engine.read_unit('dependent-u') is None


@pytest.mark.parametrize('entry', ['engine', 'cli'])
def test_legacy_same_revision_changed_ocr_invalidates_existing_dependent_vector(tmp_path, entry):
    engine, native, image = existing(tmp_path)
    try:
        path = legacy_automatic(tmp_path, engine.storage.resource_store.read_snapshot('image'),
                                text='oldcobalt', revision='unchanged-ocr-revision')
        assert engine.enrich_ready_ocr('image', path)['publication_status'] == 'published'
        before = dependent_ocr_vector(engine)
        assert_legacy_state(engine, before, text='oldcobalt', absent='newazure', dependent=True, manual=False)
        # Same input must preserve the vector, graph and publication token.
        assert engine.enrich_ready_ocr('image', path)['publication_status'] == 'already_published'
        assert engine.storage.resource_store.read_snapshot('image') == before
        changed = legacy_automatic(tmp_path, before, text='newazure', revision='unchanged-ocr-revision',
                                   name='changed.json')
        if entry == 'engine':
            assert engine.enrich_ready_ocr('image', changed)['publication_status'] == 'published'
        else:
            result = CliRunner().invoke(main, ['--root', str(tmp_path), 'resource', 'enrich-ocr',
                                               'image', str(changed), '--source-image', str(image)])
            assert result.exit_code == 0, result.output
            assert json.loads(result.output)['data']['publication_status'] == 'published'
        current = engine.storage.resource_store.read_snapshot('image')
        assert current.batch.resource == before.batch.resource == native.resource
        assert current.state.lifecycle_id == before.state.lifecycle_id
        assert current.state.state_token != before.state.state_token
        assert current.batch.vectors == native.vectors
        assert_legacy_state(engine, current, text='newazure', absent='oldcobalt', dependent=False, manual=False)
    finally:
        engine.close()
    with MDRackEngine(root=tmp_path, config=MDRackConfig()) as reopened:
        assert_legacy_state(reopened, current, text='newazure', absent='oldcobalt', dependent=False, manual=False)


@pytest.mark.parametrize('entry', ['engine', 'cli', 'image-cli'])
def test_legacy_automatic_after_manual_edit_refuses_and_preserves_dependent_vector(tmp_path, entry):
    engine, native, image = existing(tmp_path)
    try:
        automatic = legacy_automatic(tmp_path, engine.storage.resource_store.read_snapshot('image'),
                                    text='oldautomatic', revision='auto-1')
        engine.enrich_ready_ocr('image', automatic)
        manual = artifact(tmp_path, engine.storage.resource_store.read_snapshot('image'), text='humanedit',
                          revision='manual-2', name='human.json')
        assert engine.enrich_ready_ocr('image', manual)['publication_status'] == 'published'
        before = dependent_ocr_vector(engine)
        assert_legacy_state(engine, before, text='humanedit', absent='newautomatic', dependent=True, manual=True)
        assert engine.enrich_ready_ocr('image', manual)['publication_status'] == 'already_published'
        automatic = legacy_automatic(tmp_path, before, text='newautomatic', revision='auto-3', name='late.json')
        if entry == 'engine':
            with pytest.raises(ValueError):
                engine.enrich_ready_ocr('image', automatic)
        else:
            args = (['resource', 'enrich-ocr', 'image', str(automatic), '--source-image', str(image)]
                    if entry == 'cli' else ['image', 'ingest', str(image), '--resource-id', 'image',
                                           '--source-namespace', 'fixture', '--source-ref', 'image.png',
                                           '--ocr', 'newautomatic', '--provider', 'fake'])
            result = CliRunner().invoke(main, ['--root', str(tmp_path), *args])
            assert result.exit_code == 1, result.output
            assert json.loads(result.output)['error']['code'] == (
                'READY_OCR_ERROR' if entry == 'cli' else 'IMAGE_INGEST_ERROR')
        assert before.batch.resource == native.resource
        assert_legacy_state(engine, before, text='humanedit', absent='newautomatic', dependent=True, manual=True)
    finally:
        engine.close()
    with MDRackEngine(root=tmp_path, config=MDRackConfig()) as reopened:
        assert_legacy_state(reopened, before, text='humanedit', absent='newautomatic', dependent=True, manual=True)
