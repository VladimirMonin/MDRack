"""Original O01/O04: actual serialized sinks and two-size real app-v2 operations."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
from contextlib import contextmanager
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from uuid import UUID

import pytest

from mdrack._observability import AppEvent
from mdrack.application.media_search import MediaSearchRequest
from mdrack.config.models import MDRackConfig
from mdrack.domain.embedding_inputs import ImageInput, TextInput
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.prepared import LegacyTextEmbeddingAdapter
from mdrack.embeddings.runtime import embedding_profile_from_config
from mdrack.ingestion.images import ExtractedImageText
from mdrack_core import SearchScope
from tests.integration.test_native_image_v2 import PNG, ControlledProvider, ingest, make_engine

NAMES = ('mdrack.application.media_search', 'mdrack.ingestion.images',
         'mdrack_core.application.indexing', 'mdrack_core.application.retrieval')
FP = 'sha256:' + hashlib.sha256(b'synthetic-native-v1').hexdigest()


@contextmanager
def serialized():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter('%(message)s'))
    loggers = [logging.getLogger(name) for name in NAMES]
    levels = [log.level for log in loggers]
    for log in loggers:
        log.addHandler(handler)
        log.setLevel(logging.DEBUG)
    try:
        yield stream
        handler.flush()
    finally:
        for log, level in zip(loggers, levels, strict=True):
            log.removeHandler(handler)
            log.setLevel(level)
        handler.close()


def events(stream):
    payload = stream.getvalue().encode('utf-8')
    assert payload and b'PRIVATE_' not in payload
    return [(name, json.loads(body)) for name, body in (line.split(' ', 1) for line in payload.decode().splitlines())]


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['success', 'empty', 'partial', 'provider', 'cancel', 'unsupported'])
async def test_search_route_fields_actual_sink_and_recovery(tmp_path: Path, outcome: str) -> None:
    source = tmp_path / 'PRIVATE_SOURCE.png'
    source.write_bytes(PNG)
    provider = ControlledProvider()
    with make_engine(tmp_path, provider) as engine:
        await ingest(engine, source)
        request = MediaSearchRequest(ImageInput(PNG, 'image/png', 'PRIVATE_QUERY_ID'))
        if outcome in ('partial', 'provider', 'cancel'):
            request = replace(request, query=TextInput('PRIVATE_QUERY', 'PRIVATE_QUERY_ID'))
            provider.failure = 'cancel' if outcome == 'cancel' else 'raise'
            request = replace(request, allow_partial=outcome == 'partial')
        if outcome == 'empty':
            request = replace(request, scope=SearchScope(source_namespaces=('PRIVATE_EXCLUDED',)))
        if outcome == 'unsupported':
            legacy = FakeEmbeddingProvider(dimensions=8)
            engine.prepared_embedding_provider = LegacyTextEmbeddingAdapter(
                legacy, embedding_profile_from_config(MDRackConfig(), legacy))
        with serialized() as sink:
            if outcome in ('provider', 'cancel', 'unsupported'):
                with pytest.raises(asyncio.CancelledError if outcome == 'cancel' else Exception):
                    await engine.search_media(request)
            else:
                result = await engine.search_media(request)
                assert bool(result.results) == (outcome == 'success')
            emitted = events(sink)
        app = [body for name, body in emitted if name.startswith('media.search.')]
        start, terminal = app[0], app[-1]
        assert start['schema_version'] == terminal['schema_version'] == 1
        assert UUID(terminal['request_id']) and terminal['request_id'] == start['request_id']
        assert terminal['query_modality'] == ('text' if outcome in ('partial', 'provider', 'cancel') else 'image')
        assert terminal['encoding_role'] == 'query'
        assert terminal['profile_fingerprint'].startswith('sha256:')
        if outcome != 'unsupported':
            assert terminal['profile_fingerprint'] == FP
        assert terminal['mode'] == 'hybrid'
        assert terminal['target'] == 'resource' and terminal['attempt_count'] == 1
        assert terminal['source_bytes'] is None  # prepared caller input is not a raw-file measurement
        assert terminal['provider_call_count'] == int(outcome != 'unsupported')
        assert terminal['prepared_input_bytes'] == (0 if outcome == 'unsupported' else request.query.byte_size)
        assert terminal['publication_status'] == 'not_requested'
        for field in ('preparation_duration_ms', 'provider_duration_ms', 'read_duration_ms', 'duration_ms'):
            assert terminal[field] is None or terminal[field] >= 0
        if outcome in ('success', 'empty'):
            assert terminal['effective_mode'] == 'semantic' and terminal['applicable_branch_count'] == 1
            assert terminal['executed_branch_count'] == 1 and terminal['failed_branch_count'] == 0
        if outcome == 'partial':
            assert terminal['effective_mode'] == 'text' and terminal['failed_branch_count'] == 1
            assert terminal['executed_branch_count'] == 1
        if outcome in ('provider', 'cancel'):
            assert terminal['applicable_branch_count'] == 2
            assert terminal['executed_branch_count'] == 0
            assert terminal['failed_branch_count'] == int(outcome == 'provider')
        if outcome == 'unsupported':
            assert terminal['applicable_branch_count'] is None
            assert terminal['executed_branch_count'] is None and terminal['failed_branch_count'] is None
        if outcome in ('provider', 'cancel', 'unsupported'):
            assert terminal['reason'] in ('embedding_provider_error', 'cancelled', 'unsupported_embedding_route')
        for name, body in emitted:
            if name.startswith('core.search.') and 'request_id' in body:
                assert body['request_id'] == terminal['request_id']
        provider.failure = None
        engine.prepared_embedding_provider = provider
        assert (await engine.search_media(MediaSearchRequest(ImageInput(PNG, 'image/png', 'q')))).results


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['success', 'provider', 'changed', 'missing', 'unsupported'])
async def test_ingest_source_prepared_publication_stages_and_failure(tmp_path: Path, outcome: str) -> None:
    source = tmp_path / 'PRIVATE_SOURCE.png'
    source.write_bytes(PNG)
    provider = ControlledProvider()
    with make_engine(tmp_path, provider) as engine:
        original = await ingest(engine, source)
        old_graph = engine.export_resource_manifest('image')
        submitted = PNG
        if outcome in ('success', 'provider', 'changed'):
            submitted = PNG + b'PRIVATE_CHANGED_BEFORE'
            source.write_bytes(submitted)
        if outcome == 'provider':
            provider.failure = 'raise'
        if outcome == 'changed':
            provider.hook = lambda: source.write_bytes(PNG + b'PRIVATE_CHANGED_DURING')
        if outcome == 'missing':
            source.unlink()
        if outcome == 'unsupported':
            provider.capabilities = replace(provider.capabilities, inputs=())
        with serialized() as sink:
            if outcome == 'success':
                await ingest(engine, source)
            else:
                with pytest.raises(Exception):
                    await ingest(engine, source)
            app = [(name, body) for name, body in events(sink) if name.startswith('media.ingest.')]
        assert len(app) == 2
        start, terminal = app[0][1], app[-1][1]
        assert UUID(terminal['request_id']) and terminal['request_id'] == start['request_id']
        assert terminal['schema_version'] == 1 and terminal['query_modality'] == 'image'
        assert terminal['encoding_role'] == 'document' and terminal['profile_fingerprint'] == FP
        resolved = outcome not in ('missing', 'unsupported')
        assert terminal['source_bytes'] == (len(submitted) if resolved else None)
        assert terminal['prepared_input_bytes'] == (len(submitted) if resolved else 0)
        assert terminal['provider_call_count'] == int(resolved)
        assert terminal['extraction_call_count'] == 0
        assert terminal['publication_status'] == ('published' if outcome == 'success' else
                                                  'failed' if outcome == 'changed' else 'not_requested')
        for field in ('source_resolution_ms', 'preparation_duration_ms', 'provider_duration_ms',
                      'publication_duration_ms', 'duration_ms'):
            assert terminal[field] is None or terminal[field] >= 0
        if outcome != 'success':
            assert engine.export_resource_manifest('image') == old_graph
        assert engine.read_unit(original.unit_ids[0])['text'] is None


@pytest.mark.asyncio
async def test_two_size_native_batch_events_and_serialized_bytes_are_bounded(tmp_path: Path) -> None:
    samples = []
    for count in (1, 32):  # finite, valid prepared inputs; same profile/families/attempts
        root = tmp_path / str(count)
        root.mkdir()
        source = root / 'PRIVATE_SOURCE.png'
        source.write_bytes(PNG)
        provider = ControlledProvider()
        artifacts = tuple(ExtractedImageText('ocr_text', 'PRIVATE_TEXT', f'PRIVATE_PRODUCER_{i}') for i in range(count))
        with make_engine(root, provider) as engine:
            with serialized() as sink:
                handlers = [tuple(logging.getLogger(name).handlers) for name in NAMES]
                result = await engine.ingest_native_image(source, resource_id='image', source_namespace='host',
                    source_ref='PRIVATE_REF.png', derived_text=artifacts, derived_dense=True)
                assert len(result.unit_ids) == count + 2
                found = await engine.search_media(MediaSearchRequest(TextInput('PRIVATE_TEXT', 'q')))
                assert found.results
                assert handlers == [tuple(logging.getLogger(name).handlers) for name in NAMES]
                parsed = events(sink)
                payload = sink.getvalue().encode('utf-8')
                (root / 'serialized-events.log').write_bytes(payload)
                samples.append((payload, parsed))
        terminal = [body for name, body in parsed if name == 'media.ingest.completed'][0]
        assert terminal['prepared_input_bytes'] == len(PNG) + count * len('PRIVATE_TEXT'.encode())
        assert terminal['provider_call_count'] == count + 1
    small, large = samples
    assert [name for name, _ in small[1]] == [name for name, _ in large[1]]
    assert [(name, set(body)) for name, body in small[1]] == [(name, set(body)) for name, body in large[1]]
    # A fixed-schema record may grow only in numeric widths, not with units/content.
    # JSON finite numeric repr <=24 bytes here; UUID/fingerprint/category widths stay fixed.
    numeric_fields = sum(type(value) in (int, float) for _, body in large[1] for value in body.values())
    assert len(large[0]) <= len(small[0]) + numeric_fields * 24
    (tmp_path / 'size-receipt.json').write_text(json.dumps({
        'sizes': [1, 32], 'event_counts': [len(small[1]), len(large[1])],
        'serialized_bytes': [len(small[0]), len(large[0])], 'numeric_width_allowance': numeric_fields * 24,
    }), encoding='utf-8')
    assert [body for name, body in large[1] if name == 'media.search.completed'][0]['applicable_branch_count'] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['text', 'stored_unit'])
async def test_non_encoding_routes_have_real_zero_calls_and_correlated_role(tmp_path: Path, route: str) -> None:
    from mdrack.application.media_search import StoredUnitInput
    source = tmp_path / 'PRIVATE_SOURCE.png'
    source.write_bytes(PNG)
    provider = ControlledProvider()
    with make_engine(tmp_path, provider) as engine:
        original = await ingest(engine, source)
        query = TextInput('PRIVATE_QUERY', 'q') if route == 'text' else StoredUnitInput(original.unit_ids[0])
        request = MediaSearchRequest(query, mode='text' if route == 'text' else 'hybrid')
        calls = len(provider.calls)
        with serialized() as sink:
            await engine.search_media(request)
            terminal = [body for name, body in events(sink) if name == 'media.search.completed'][0]
        assert terminal['provider_call_count'] == terminal['prepared_input_bytes'] == 0
        assert terminal['encoding_role'] == ('query' if route == 'text' else 'similarity')
        assert terminal['query_modality'] == route and terminal['applicable_branch_count'] == 1
        assert len(provider.calls) == calls


@pytest.mark.asyncio
async def test_terminal_ingest_sink_io_failure_preserves_commit_and_reopen(tmp_path: Path) -> None:
    source = tmp_path / 'PRIVATE_SOURCE.png'
    source.write_bytes(PNG)
    class Broken(logging.Handler):
        def emit(self, record):
            if record.getMessage().startswith('media.ingest.completed'):
                raise OSError('PRIVATE_SINK_EXCEPTION')
    handler = Broken()
    log = logging.getLogger(NAMES[0])
    with make_engine(tmp_path) as engine:
        with serialized() as sink:
            log.addHandler(handler)
            try:
                result = await ingest(engine, source)
            finally:
                log.removeHandler(handler)
            assert [body for name, body in events(sink) if name == 'media.ingest.completed'][0][
                'publication_status'] == 'published'
    with make_engine(tmp_path) as engine:
        assert engine.read_unit(result.unit_ids[0])['text'] is None
        assert (await engine.search_media(MediaSearchRequest(ImageInput(PNG, 'image/png', 'q')))).results


def test_new_closed_fields_do_not_license_strings_or_foreign_enums() -> None:
    class Foreign(StrEnum):
        IMAGE = 'image'
    event = AppEvent('media.search.started', {'query_modality': Foreign.IMAGE, 'profile_fingerprint': 'PRIVATE_FP'})
    assert event.fields['query_modality'] == event.fields['profile_fingerprint'] == '[redacted]'
    with pytest.raises(ValueError):
        AppEvent('media.search.started', {'PRIVATE_UNKNOWN_FIELD': 'PRIVATE_BODY'})


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary,partial,successful,failed', [
    ('provider', False, 0, 1),
    ('both', True, 0, 2),
    ('lexical', False, 0, 1),  # vector never runs after strict fail-fast
    ('vector', False, 1, 1),  # lexical empty success survives later failure
    ('lexical', True, 1, 1),
    ('vector', True, 1, 1),
    ('malformed', True, 1, 1),  # core validation, not just a port return
    ('provider-and-storage', True, 0, 2),
])
async def test_known_branch_outcomes_survive_terminal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
    partial: bool, successful: int, failed: int,
) -> None:
    from mdrack_sqlite import SQLiteCatalog

    source = tmp_path / 'PRIVATE_SOURCE.png'
    source.write_bytes(PNG)
    provider = ControlledProvider()
    with make_engine(tmp_path, provider) as engine:
        await ingest(engine, source)
        if boundary in ('provider', 'provider-and-storage'):
            provider.failure = 'raise'
        def fail(*args, **kwargs):
            raise TimeoutError('PRIVATE_STORAGE_FAILURE')
        with monkeypatch.context() as patch:
            if boundary in ('both', 'lexical', 'provider-and-storage'):
                patch.setattr(SQLiteCatalog, 'search_lexical', fail)
            if boundary in ('both', 'vector'):
                patch.setattr(SQLiteCatalog, 'search_vector', fail)
            if boundary == 'malformed':
                patch.setattr(SQLiteCatalog, 'search_vector', lambda *args, **kwargs: [object()])
            with serialized() as sink:
                request = MediaSearchRequest(TextInput('PRIVATE_QUERY', 'PRIVATE_ID'), allow_partial=partial)
                raises = not partial or successful == 0
                if raises:
                    with pytest.raises(Exception):
                        await engine.search_media(request)
                else:
                    await engine.search_media(request)
                emitted = events(sink)
            raw = sink.getvalue().encode('utf-8')
            (tmp_path / 'serialized-events.log').write_bytes(raw)
            assert raw and b'PRIVATE_' not in raw
            name, body = [(name, body) for name, body in emitted if name.startswith('media.search.')][-1]
            assert name == ('media.search.failed' if raises else 'media.search.completed')
            assert body['applicable_branch_count'] == 2
            assert body['executed_branch_count'] == successful
            assert body['failed_branch_count'] == failed
            assert {b['request_id'] for _, b in emitted if 'request_id' in b} == {body['request_id']}
        provider.failure = None
        assert (await engine.search_media(MediaSearchRequest(ImageInput(PNG, 'image/png', 'q')))).results
