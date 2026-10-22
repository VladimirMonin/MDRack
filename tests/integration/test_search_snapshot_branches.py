"""Branch, filtering, discovery and handle-lifetime probes on fresh file v2."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event

import pytest

from mdrack.adapters.sqlite.canonical_catalog import ApplicationSearchReadSessions
from mdrack.application.resources import ResourceQueryService, UnifiedTextSearchService
from mdrack.application.retrieval import RetrievalService
from mdrack.config.models import MDRackConfig
from mdrack.public_api import MDRackEngine
from mdrack_core.domain import CatalogExecutionError, EmbeddingSpaceRecord, ErrorCategory, VectorRecord
from mdrack_sqlite import SQLiteCatalog, SQLiteResourceStore
from tests.integration.test_search_read_sessions import batch


class QueryProvider:
    async def embed_query(self, text: str, profile: str = 'default') -> list[float]:
        return [1.0, 0.0]


def create(root: Path) -> Path:
    store = root / '.mdrack'
    store.mkdir()
    path = store / 'catalog.sqlite3'
    graph = replace(batch(), spaces=(EmbeddingSpaceRecord('space', 2, 'cosine', 'fingerprint'),),
                    units=tuple(replace(unit, unit_kind='whole_resource') for unit in batch().units),
                    vectors=(VectorRecord('unit', 'space', (1.0, 0.0)),))
    with SQLiteCatalog.create_v2(path) as catalog:
        catalog.replace_resource(graph)
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['spaces', 'lexical', 'hydrate'])
@pytest.mark.parametrize('scope', ['all', 'notes'])
async def test_space_branches_filter_and_hydration_are_one_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, scope: str,
) -> None:
    path = create(tmp_path)
    publish, committed, ready = Event(), Event(), Event()

    def writer() -> None:
        with SQLiteCatalog.open(path) as catalog:
            ready.set()
            assert publish.wait(10)
            old = batch()
            catalog.replace_resource(replace(old, resource=replace(old.resource, resource_kind='audio')))
        committed.set()

    owner = SQLiteResourceStore if phase == 'lexical' else SQLiteCatalog
    method = {'spaces': 'resolve_embedding_spaces', 'lexical': 'search_lexical', 'hydrate': 'read_resource'}[phase]
    original = getattr(owner, method)
    fired = False
    handles = []

    def call(self, *args, **kwargs):
        nonlocal fired
        if not fired:
            fired = True
            handles.append(self)
            # Publish after resolution/lexical but before hydration's actual read.
            if phase == 'hydrate':
                publish.set()
                assert committed.wait(10)
                return original(self, *args, **kwargs)
            result = original(self, *args, **kwargs)
            publish.set()
            assert committed.wait(10)
            return result
        return original(self, *args, **kwargs)

    service = UnifiedTextSearchService(
        None, embedding_provider=QueryProvider(), embedding_fingerprint='fingerprint',
        read_sessions=ApplicationSearchReadSessions(tmp_path, MDRackConfig()),
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        assert ready.wait(10)
        monkeypatch.setattr(owner, method, call)
        old = await service.search('needle', mode='hybrid', scope=scope)
        assert len(old.results) == 1 and old.results[0].resource_kind == 'document'
        assert {e.branch_id for e in old.results[0].evidence} == {'unified_text', 'unified_semantic'}
        assert old.results[0].rank == 1
        new = await service.search('needle', mode='hybrid', scope=scope)
        if scope == 'notes':
            assert new.results == ()
        else:
            assert len(new.results) == 1 and new.results[0].resource_kind == 'audio'
        future.result(timeout=10)


def test_existing_vector_basis_and_candidate_reads_share_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = create(tmp_path)
    publish, committed, ready = Event(), Event(), Event()

    def writer() -> None:
        with SQLiteCatalog.open(path) as catalog:
            ready.set()
            assert publish.wait(10)
            catalog.delete_resource('resource')
        committed.set()

    original = SQLiteCatalog.read_vector
    fired = False

    def read_vector(self, *args, **kwargs):
        nonlocal fired
        result = original(self, *args, **kwargs)
        if not fired:
            fired = True
            publish.set()
            assert committed.wait(10)
        return result

    service = ResourceQueryService(None, read_sessions=ApplicationSearchReadSessions(tmp_path, MDRackConfig()))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        assert ready.wait(10)
        monkeypatch.setattr(SQLiteCatalog, 'read_vector', read_vector)
        old = service.find_similar('unit', 'space', exclude_same_resource=False)
        assert len(old.results) == 1 and old.results[0].resource_id == 'resource'
        new = service.find_similar('unit', 'space', exclude_same_resource=False)
        assert new.results == () and new.degraded
        future.result(timeout=10)


def test_long_lived_facade_concurrent_operations_do_not_share_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    create(tmp_path)
    barrier = Barrier(2)
    connections = []
    readers = []
    original_open = SQLiteCatalog.open_readonly.__func__

    def open_reader(cls, *args, **kwargs):
        reader = original_open(cls, *args, **kwargs)
        readers.append(reader)
        return reader

    monkeypatch.setattr(SQLiteCatalog, 'open_readonly', classmethod(open_reader))
    original = SQLiteResourceStore.search_lexical

    def lexical(self, *args, **kwargs):
        connections.append(self.connection)
        barrier.wait(timeout=10)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SQLiteResourceStore, 'search_lexical', lexical)
    with MDRackEngine(root=tmp_path, config=MDRackConfig()) as engine:
        def query():
            return asyncio.run(engine.search_unified('needle', mode='text'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(query) for _ in range(2)]
            assert all(len(f.result(timeout=10).results) == 1 for f in futures)
    assert len(connections) == 2 and connections[0] is not connections[1]
    assert len(readers) == 2 and all(reader.closed for reader in readers)


@pytest.mark.asyncio
async def test_legacy_retrieval_vector_failure_fallback_uses_same_owned_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = create(tmp_path)
    with SQLiteCatalog.open(path) as catalog:
        catalog.replace_resource(replace(batch(), spaces=(EmbeddingSpaceRecord('space', 2, 'cosine', 'fingerprint'),),
                                         vectors=(VectorRecord('unit', 'space', (1.0, 0.0)),)))
    ready, publish, committed = Event(), Event(), Event()

    def writer():
        with SQLiteCatalog.open(path) as catalog:
            ready.set()
            assert publish.wait(10)
            catalog.delete_resource('resource')
        committed.set()

    def vector_failure(self, *args, **kwargs):
        assert self.connection.in_transaction
        publish.set()
        assert committed.wait(10)
        raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR)

    monkeypatch.setattr(SQLiteResourceStore, 'search_vector', vector_failure)
    service = RetrievalService(None, embedding_provider=QueryProvider(), profile_fingerprint='fingerprint',
                               read_sessions=ApplicationSearchReadSessions(tmp_path, MDRackConfig()))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        assert ready.wait(10)
        old = await service.search_hybrid('needle')
        assert len(old.results) == 1 and old.results[0].logical_id == 'unit'
        assert old.degraded
        assert service.search_text('needle').results == ()
        future.result(timeout=10)
