"""Finite W2 planner variants over the actual app catalog, not a fake use case."""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import replace
from pathlib import Path

import pytest

from mdrack.application.media_search import MediaSearchRequest
from mdrack.application.textual_embedding_space import CanonicalTextEmbeddingSpace
from mdrack.config.models import MDRackConfig
from mdrack.domain.embedding_inputs import EncodingRole, ImageInput, TextInput
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.runtime import embedding_profile_from_config
from mdrack.public_api import MDRackEngine
from mdrack_core import SearchScope
from mdrack_sqlite import SQLiteCatalog
from tests.integration.test_native_image_v2 import PNG, ControlledProvider, ingest, make_engine


def test_image_to_actual_note_and_space_record_is_modality_independent(tmp_path: Path) -> None:
    legacy = FakeEmbeddingProvider(dimensions=8)
    provider = ControlledProvider()
    provider.space = CanonicalTextEmbeddingSpace(embedding_profile_from_config(MDRackConfig(), legacy)).record
    (tmp_path / 'note.md').write_text('# Note\n\nAn ordinary source note.', encoding='utf-8')
    source = tmp_path / 'a.png'
    source.write_bytes(PNG)
    with MDRackEngine(root=tmp_path, config=MDRackConfig(), embedding_provider=legacy,
                      prepared_embedding_provider=provider) as engine:
        engine.scan()
        result = asyncio.run(ingest(engine, source))
        found = asyncio.run(engine.search_media(MediaSearchRequest(ImageInput(PNG, 'image/png', 'q'),
                                                                   SearchScope(modalities=('text',)))))
        assert found.results
        assert found.results[0]['evidence'][0]['modality'] == 'text'
        assert found.results[0]['resource_id'] != result.resource_id
    with SQLiteCatalog.open_readonly(tmp_path / '.mdrack/catalog.sqlite3') as catalog:
        spaces = catalog.resolve_embedding_spaces(fingerprint=provider.space.fingerprint, dimensions=8)
        assert spaces == (provider.space,)


@pytest.mark.asyncio
async def test_strict_partial_all_failure_empty_and_recovery(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / 'a.png'
    source.write_bytes(PNG)
    provider = ControlledProvider()
    with make_engine(tmp_path, provider) as engine:
        await ingest(engine, source)
        request = MediaSearchRequest(TextInput('needle', 'q'))
        provider.failure = 'raise'
        with pytest.raises(Exception):
            await engine.search_media(request)
        partial = await engine.search_media(replace(request, allow_partial=True))
        assert partial.effective_mode == 'text' and partial.degradations
        assert not partial.results  # successful lexical branch can be empty
        with pytest.raises(Exception):
            await engine.search_media(replace(request, mode='semantic', allow_partial=True))
        provider.failure = None
        original = SQLiteCatalog.search_vector
        def fail_vector(*args, **kwargs):
            raise TimeoutError('PRIVATE_TIMEOUT_SENTINEL')
        monkeypatch.setattr(SQLiteCatalog, 'search_vector', fail_vector)
        with pytest.raises(Exception):
            await engine.search_media(request)
        partial = await engine.search_media(replace(request, allow_partial=True))
        assert partial.effective_mode == 'text' and partial.degradations
        with pytest.raises(Exception):
            await engine.search_media(replace(request, mode='semantic', allow_partial=True))
        monkeypatch.setattr(SQLiteCatalog, 'search_vector', original)
        empty = await engine.search_media(replace(request, scope=SearchScope(source_namespaces=('excluded',))))
        assert empty.results == () and not empty.degradations
        recovered = await engine.search_media(replace(request, mode='semantic'))
        assert recovered.results


@pytest.mark.asyncio
async def test_scope_before_limit_family_budgets_and_unit_target(tmp_path: Path, monkeypatch) -> None:
    provider = ControlledProvider()
    source = tmp_path / 'a.png'
    source.write_bytes(PNG)
    with make_engine(tmp_path, provider) as engine:
        await ingest(engine, source)
        await engine.ingest_native_image(source, resource_id='allowed', source_namespace='allowed', source_ref='b.png')
        seen = []
        original = SQLiteCatalog.search_vector
        def record(catalog, branch, *, scope):
            seen.append((branch, scope))
            return original(catalog, branch, scope=scope)
        monkeypatch.setattr(SQLiteCatalog, 'search_vector', record)
        request = MediaSearchRequest(ImageInput(PNG, 'image/png', 'q'),
                                     SearchScope(source_namespaces=('allowed',)), target='unit',
                                     limit=1, candidate_budget=7, vector_weight=0.25)
        found = await engine.search_media(request)
        assert len(found.results) == 1 and found.results[0]['resource_id'] == 'allowed'
        assert found.results[0]['unit_id']
        assert len(seen) == 1 and seen[0][0].candidate_limit == 7 and seen[0][0].weight == 0.25
        admitted = provider.capabilities.require(EncodingRole.QUERY, 'image').candidate_modalities
        assert seen[0][1] == replace(request.scope, modalities=admitted)


@pytest.mark.asyncio
async def test_provider_before_read_snapshot_hydration_inside_and_next_query(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / 'a.png'
    source.write_bytes(PNG)
    provider = ControlledProvider()
    with make_engine(tmp_path, provider) as engine:
        result = await ingest(engine, source)
        writer = engine._storage.resource_store
        def provider_hook():
            assert not writer.connection.in_transaction
        provider.hook = provider_hook
        original = SQLiteCatalog.read_unit
        fired = False
        def read_unit(catalog, unit_id):
            nonlocal fired
            if catalog.readonly and not fired:
                fired = True
                assert catalog.connection.in_transaction
                # Writer commit between retrieval and hydration cannot split this response.
                with SQLiteCatalog.open(tmp_path / '.mdrack/catalog.sqlite3') as other:
                    other.delete_resource('image')
            return original(catalog, unit_id)
        monkeypatch.setattr(SQLiteCatalog, 'read_unit', read_unit)
        request = MediaSearchRequest(ImageInput(PNG, 'image/png', 'q'))
        found = await engine.search_media(request)
        assert found.results[0]['evidence'][0]['unit_id'] == result.unit_ids[0]
        # A compatible space record remains after deletion, so next search is truly empty.
        assert not (await engine.search_media(request)).results
        assert fired


@pytest.mark.asyncio
async def test_serialized_privacy_and_sink_io_failure_keep_result(tmp_path: Path) -> None:
    source = tmp_path / 'a.png'
    source.write_bytes(PNG)
    provider = ControlledProvider()
    log = logging.getLogger('mdrack.application.media_search')
    sink = io.StringIO()
    handler = logging.StreamHandler(sink)
    old_level = log.level
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    try:
        with make_engine(tmp_path, provider) as engine:
            await ingest(engine, source)
            request = MediaSearchRequest(TextInput('PRIVATE_QUERY_SENTINEL', 'PRIVATE_ID_SENTINEL'))
            assert (await engine.search_media(request)).results
            provider.failure = 'raise'
            partial = await engine.search_media(replace(request, allow_partial=True))
            assert partial.degradations
            provider.failure = None
            class Broken(logging.Handler):
                def emit(self, record):
                    raise OSError('PRIVATE_SINK_SENTINEL')
            broken = Broken()
            log.addHandler(broken)
            try:
                assert (await engine.search_media(request)).results
            finally:
                log.removeHandler(broken)
            await engine.search_media(replace(request, scope=SearchScope(source_namespaces=('empty',))))
        handler.flush()
        serialized = sink.getvalue()
        assert 'result_count' in serialized and 'effective_mode' in serialized and 'duration_ms' in serialized
        assert 'PRIVATE_' not in serialized
    finally:
        log.removeHandler(handler)
        log.setLevel(old_level)


@pytest.mark.asyncio
async def test_incompatible_space_and_missing_similarity_do_not_reencode(tmp_path: Path) -> None:
    from mdrack.application.media_search import StoredUnitInput
    source = tmp_path / 'a.png'
    source.write_bytes(PNG)
    provider = ControlledProvider()
    with make_engine(tmp_path, provider) as engine:
        result = await ingest(engine, source)
        calls = len(provider.calls)
        with pytest.raises(ValueError, match='similarity_basis_unavailable'):
            await engine.search_media(MediaSearchRequest(StoredUnitInput('missing')))
        assert len(provider.calls) == calls
        provider.capabilities = replace(provider.capabilities, reuse_document_vector=False)
        with pytest.raises(ValueError, match='similarity_role_unsupported'):
            await engine.search_media(MediaSearchRequest(StoredUnitInput(result.unit_ids[0])))
        assert len(provider.calls) == calls
        provider.space = replace(provider.space, fingerprint='incompatible-same-dimensions')
        with pytest.raises(ValueError, match='embedding_space_unavailable'):
            await engine.search_media(MediaSearchRequest(ImageInput(PNG, 'image/png', 'q')))


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['provider', 'hydration'])
async def test_query_cancel_releases_and_next_query_succeeds(tmp_path: Path, monkeypatch, phase: str) -> None:
    source = tmp_path / 'a.png'
    source.write_bytes(PNG)
    provider = ControlledProvider()
    with make_engine(tmp_path, provider) as engine:
        await ingest(engine, source)
        request = MediaSearchRequest(ImageInput(PNG, 'image/png', 'q'))
        original = SQLiteCatalog.read_unit
        if phase == 'provider':
            provider.failure = 'cancel'
        else:
            def cancelled(*args, **kwargs):
                raise asyncio.CancelledError()
            monkeypatch.setattr(SQLiteCatalog, 'read_unit', cancelled)
        with pytest.raises(asyncio.CancelledError):
            await engine.search_media(request)
        provider.failure = None
        monkeypatch.setattr(SQLiteCatalog, 'read_unit', original)
        assert (await engine.search_media(request)).results


@pytest.mark.asyncio
async def test_invalid_prepared_signature_refuses_before_provider_or_database(tmp_path: Path) -> None:
    provider = ControlledProvider()
    with make_engine(tmp_path, provider) as engine:
        with pytest.raises(ValueError):
            await engine.search_media(MediaSearchRequest(ImageInput(b'not-image', 'image/png', 'q')))
    assert not provider.calls
    assert not (tmp_path / '.mdrack').exists()
