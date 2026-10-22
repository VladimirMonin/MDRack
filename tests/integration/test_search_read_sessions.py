"""Fresh file-v2 snapshot regressions with independent native SQLite connections."""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event

import httpx
import pytest

from mdrack.adapters.sqlite.canonical_catalog import ApplicationSearchReadSessions
from mdrack.application.resources import UnifiedTextSearchService
from mdrack.application.retrieval import InvalidTextSearchError, RetrievalService
from mdrack.config.models import MDRackConfig, SearchConfig
from mdrack.embeddings.runtime import embedding_profile_from_config
from mdrack.integrations.lmstudio.client import LMStudioProvider
from mdrack.public_api import MDRackEngine
from mdrack_core.domain import (
    EmbeddingSpaceRecord,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)
from mdrack_sqlite import SQLiteCatalog, SQLiteResourceStore


def batch() -> PreparedResourceBatch:
    return PreparedResourceBatch(
        ResourceRecord('resource', 'document', 'text/plain', 'synthetic', Locator('whole_resource', {}), 'hash'),
        [RepresentationRecord('representation', 'resource', 'retrieval_text', 'text', 'needle old')],
        [SearchUnitRecord('unit', 'resource', 'representation', 'text_chunk', 'text', 'needle old',
                          Locator('document_span', {
                              'root_id': 'synthetic', 'relative_path': 'note.md', 'start_line': 1, 'end_line': 1,
                              'block_logical_id': 'block', 'chunk_logical_id': 'unit', 'block_kind': 'paragraph',
                              'chunk_kind': 'text', 'heading_path': (),
                          }), 0)],
    )


def test_verification_counts_share_one_committed_state(tmp_path: Path) -> None:
    path = tmp_path / 'catalog.sqlite3'
    with SQLiteCatalog.create_v2(path) as catalog:
        catalog.replace_resource(batch())
    publish = Event()
    committed = Event()
    ready = Event()

    def writer() -> None:
        with SQLiteCatalog.open(path) as catalog:
            ready.set()
            assert publish.wait(10)
            catalog.delete_resource('resource')
        committed.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        assert ready.wait(10)
        with SQLiteCatalog.open_readonly(path) as reader:
            fired = False

            def trace(sql: str) -> None:
                nonlocal fired
                if not fired and sql == 'SELECT COUNT(*) FROM core_search_units_fts':
                    fired = True
                    publish.set()
                    assert committed.wait(10)

            reader.connection.set_trace_callback(trace)
            summary = reader.verify()
            reader.connection.set_trace_callback(None)
            assert fired
            assert summary.units == summary.fts_rows == 1
            assert reader.verify().units == 0
        future.result(timeout=10)


def test_owned_readers_are_independent_and_release_on_baseexception(tmp_path: Path) -> None:
    store = tmp_path / '.mdrack'
    store.mkdir()
    with SQLiteCatalog.create_v2(store / 'catalog.sqlite3') as catalog:
        catalog.replace_resource(batch())
    factory = ApplicationSearchReadSessions(tmp_path, MDRackConfig())
    with factory.open() as first:
        with factory.open() as second:
            assert first.catalog.connection is not second.catalog.connection
            assert first.catalog.readonly and second.catalog.readonly
            assert first.catalog.connection.in_transaction
        assert second.catalog.closed
        assert first.catalog.read_resource('resource') is not None
    assert first.catalog.closed
    for error in (RuntimeError, asyncio.CancelledError):
        with pytest.raises(error):
            with factory.open() as failed:
                raise error()
        assert failed.catalog.closed
    with factory.open() as next_session:
        assert next_session.catalog.read_resource('resource') is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['success', 'cancel', 'hydrate_error', 'hydrate_cancel'])
async def test_real_provider_http_precedes_owned_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    store = tmp_path / '.mdrack'
    store.mkdir()
    path = store / 'catalog.sqlite3'
    graph = replace(batch(), spaces=(EmbeddingSpaceRecord('space', 2, 'cosine', 'fingerprint'),),
                    vectors=(VectorRecord('unit', 'space', (1.0, 0.0)),))
    with SQLiteCatalog.create_v2(path) as catalog:
        catalog.replace_resource(graph)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    opened = []
    original_open = SQLiteCatalog.open_readonly.__func__

    def open_reader(cls, *args, **kwargs):
        reader = original_open(cls, *args, **kwargs)
        opened.append(reader)
        return reader

    monkeypatch.setattr(SQLiteCatalog, 'open_readonly', classmethod(open_reader))
    http_calls = []

    async def response(request: httpx.Request) -> httpx.Response:
        # Real provider serialization and HTTP client, only the wire edge is fake.
        assert not opened
        with closing(sqlite3.connect(path)) as independent:
            independent.execute('BEGIN IMMEDIATE')
            independent.rollback()
        http_calls.append(request.url.path)
        if outcome == 'cancel':
            raise asyncio.CancelledError()
        return httpx.Response(200, json={'data': [{'embedding': [1.0, 0.0], 'index': 0}]})

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(response), **kwargs),
    )
    provider = LMStudioProvider(endpoint='https://synthetic.invalid', model='fixture-model', dimensions=2)
    service = UnifiedTextSearchService(
        None, embedding_provider=provider, embedding_fingerprint='fingerprint',
        read_sessions=ApplicationSearchReadSessions(tmp_path, MDRackConfig()),
    )
    original_read = SQLiteCatalog.read_unit
    if outcome in {'hydrate_error', 'hydrate_cancel'}:
        def fail(self, unit_id):
            assert self.connection.in_transaction
            if outcome == 'hydrate_cancel':
                raise asyncio.CancelledError()
            raise RuntimeError('synthetic hydration failure')
        monkeypatch.setattr(SQLiteCatalog, 'read_unit', fail)
    if outcome == 'success':
        result = await service.search('needle', mode='hybrid')
        assert len(result.results) == 1
        assert {e.branch_id for e in result.results[0].evidence} == {'unified_text', 'unified_semantic'}
    else:
        with pytest.raises(asyncio.CancelledError if outcome in {'cancel', 'hydrate_cancel'} else RuntimeError):
            await service.search('needle', mode='hybrid')
    assert http_calls == ['/v1/embeddings']
    assert all(reader.closed for reader in opened)
    assert len(opened) == (0 if outcome == 'cancel' else 1)
    monkeypatch.setattr(SQLiteCatalog, 'read_unit', original_read)
    # Later independent search owns a fresh reader and succeeds after either error.
    assert len((await service.search('needle', mode='text')).results) == 1
    assert all(reader.closed for reader in opened)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['unified', 'metadata', 'text'])
async def test_normal_engine_search_is_old_then_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str,
) -> None:
    store = tmp_path / '.mdrack'
    store.mkdir()
    path = store / 'catalog.sqlite3'
    with SQLiteCatalog.create_v2(path) as catalog:
        catalog.replace_resource(batch())
    publish, committed, ready = Event(), Event(), Event()

    def writer() -> None:
        with SQLiteCatalog.open(path) as catalog:
            ready.set()
            assert publish.wait(10)
            catalog.delete_resource('resource')
            # An independent valid publication, not fake catalog behavior.
            catalog.replace_resource(PreparedResourceBatch(
                ResourceRecord('new-resource', 'document', 'text/plain', 'synthetic', Locator('whole_resource', {})),
                [RepresentationRecord('new-representation', 'new-resource', 'metadata_text', 'text', 'needle new')],
                [SearchUnitRecord('new-unit', 'new-resource', 'new-representation', 'whole_resource', 'text',
                                  'needle new', Locator('whole_resource', {}), 0)],
            ))
        committed.set()

    original = SQLiteResourceStore.search_lexical
    fired = False

    def lexical(self, branch, *, scope):
        nonlocal fired
        result = original(self, branch, scope=scope)
        if not fired:
            fired = True
            publish.set()
            assert committed.wait(10)
        return result

    monkeypatch.setattr(SQLiteResourceStore, 'search_lexical', lexical)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        assert ready.wait(10)
        with MDRackEngine(root=tmp_path, config=MDRackConfig()) as engine:
            async def query():
                if route == 'unified':
                    return (await engine.search_unified('needle', mode='text')).to_dict()['results']
                if route == 'metadata':
                    return engine.search_resources_text('needle').to_dict()['results']
                return engine.search_text('needle').to_dict()['results']

            old = await query()
            assert len(old) == 1
            assert old[0].get('resource_id', old[0].get('logical_id')) in {'resource', 'unit'}
            new = await query()
            if route == 'text':
                assert new == []  # New graph is metadata-only, not a Markdown chunk.
            else:
                assert len(new) == 1 and new[0]['resource_id'] == 'new-resource'
        future.result(timeout=10)


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['engine', 'factory'])
@pytest.mark.parametrize('query', ['', '   ', '\t\r\n'])
@pytest.mark.parametrize('wire_outcome', ['success', 'cancel', 'failure'])
@pytest.mark.parametrize('semantic_weight', [1.0, 0.0])
async def test_blank_hybrid_validates_before_provider_and_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str, query: str,
    wire_outcome: str, semantic_weight: float,
) -> None:
    """C01: actual provider/SQLite paths reject input before any external work."""
    provider_calls = []
    http_calls = []
    entered = []
    readers = []
    state = {'wire': wire_outcome}

    class ObservedProvider(LMStudioProvider):
        async def embed_query(self, text: str, profile: str = 'default') -> list[float]:
            provider_calls.append(text)
            return await super().embed_query(text, profile=profile)

    async def response(request: httpx.Request) -> httpx.Response:
        assert not entered
        http_calls.append(request.url.path)
        if state['wire'] == 'cancel':
            raise asyncio.CancelledError()
        if state['wire'] == 'failure':
            return httpx.Response(400, json={'error': 'synthetic provider failure'})
        return httpx.Response(200, json={'data': [{'embedding': [1.0, 0.0], 'index': 0}]})

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(response), **kwargs),
    )
    config = MDRackConfig(search=SearchConfig(semantic_weight=semantic_weight))
    provider = ObservedProvider(endpoint='https://synthetic.invalid', model='fixture-model', dimensions=2)
    engine = None
    try:
        fingerprint = embedding_profile_from_config(config, provider, 'default').fingerprint
        store = tmp_path / '.mdrack'
        store.mkdir()
        path = store / 'catalog.sqlite3'
        graph = replace(batch(), spaces=(EmbeddingSpaceRecord('space', 2, 'cosine', fingerprint),),
                        vectors=(VectorRecord('unit', 'space', (1.0, 0.0)),))
        with SQLiteCatalog.create_v2(path) as catalog:
            catalog.replace_resource(graph)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        original_open = ApplicationSearchReadSessions.open

        @contextmanager
        def observed_open(factory):
            entered.append(True)
            with original_open(factory) as session:
                readers.append(session.catalog)
                yield session

        monkeypatch.setattr(ApplicationSearchReadSessions, 'open', observed_open)
        if route == 'engine':
            engine = MDRackEngine(root=tmp_path, config=config, embedding_provider=provider)
            search = engine.search_hybrid
        else:
            service = RetrievalService(
                None, embedding_provider=provider, profile_fingerprint=fingerprint,
                semantic_weight=semantic_weight, read_sessions=ApplicationSearchReadSessions(tmp_path, config),
            )
            search = service.search_hybrid
        with pytest.raises(InvalidTextSearchError, match='invalid_text_query'):
            await search(query)
        assert provider_calls == []
        assert http_calls == []
        assert entered == []
        assert readers == []
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        # Valid-query cancellation must still propagate, not become validation.
        if semantic_weight > 0.0 and wire_outcome == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await search('needle')
            assert entered == [] and readers == []
            assert provider_calls == ['needle'] and http_calls == ['/v1/embeddings']
        elif semantic_weight > 0.0 and wire_outcome == 'failure':
            fallback = await search('needle')
            assert len(fallback.results) == 1 and fallback.results[0].logical_id == 'unit'
            assert fallback.degraded and fallback.degraded_reason == 'embedding_provider_error'
            assert provider_calls == ['needle'] and http_calls == ['/v1/embeddings']
            assert len(entered) == 1 and len(readers) == 1 and readers[0].closed
        provider_calls.clear()
        http_calls.clear()
        entered.clear()
        readers.clear()
        # Invalid input/failure/cancellation must not poison the next operation.
        state['wire'] = 'success'
        result = await search('needle')
        assert len(result.results) == 1 and result.results[0].logical_id == 'unit'
        assert not result.degraded
        assert provider_calls == (['needle'] if semantic_weight > 0.0 else [])
        assert http_calls == (['/v1/embeddings'] if semantic_weight > 0.0 else [])
        assert len(entered) == 1 and len(readers) == 1 and readers[0].closed
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    finally:
        if engine is not None:
            engine.close()
        await provider.close()
