"""COPY-ONLY integration proof pending explicit runtime custody transfer."""
from dataclasses import replace
from pathlib import Path

import pytest

from mdrack.config.models import MDRackConfig
from mdrack.public_api import MDRackEngine
from mdrack_core.domain import Locator
from mdrack_sqlite import SQLiteCatalog, SQLiteResourceStore
from tests.integration.test_search_read_sessions import batch


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['image', 'timed'])
async def test_held_normal_engine_search_owns_only_readonly_session(tmp_path: Path, monkeypatch, route: str) -> None:
    store = tmp_path / '.mdrack'
    store.mkdir()
    graph = batch()
    if route == 'timed':
        graph = replace(graph, resource=replace(graph.resource, resource_kind='audio'),
            representations=tuple(replace(r, representation_kind='timed_passage') for r in graph.representations),
            units=tuple(replace(u, unit_kind='time_segment', evidence_locator=Locator('time_segment', {
                'start_ms':0, 'end_ms':100, 'track':'audio'})) for u in graph.units))
    else:
        graph = replace(graph, resource=replace(graph.resource, resource_kind='image'),
            representations=tuple(replace(r, representation_kind='caption_text') for r in graph.representations),
            units=tuple(replace(u, unit_kind='whole_resource', evidence_locator=Locator('whole_image', {
                'source_ref':'image.png'})) for u in graph.units))
    with SQLiteCatalog.create_v2(store / 'catalog.sqlite3') as catalog:
        catalog.replace_resource(graph)
    readers = []
    original_open = SQLiteCatalog.open_readonly.__func__
    def open_reader(cls, *args, **kwargs):
        reader = original_open(cls, *args, **kwargs)
        readers.append(reader)
        return reader
    original_lexical = SQLiteResourceStore.search_lexical
    def lexical(self, *args, **kwargs):
        assert self.connection.in_transaction
        assert self.connection.execute('PRAGMA query_only').fetchone()[0] == 1
        return original_lexical(self, *args, **kwargs)
    monkeypatch.setattr(SQLiteCatalog, 'open_readonly', classmethod(open_reader))
    monkeypatch.setattr(SQLiteResourceStore, 'search_lexical', lexical)
    with MDRackEngine(root=tmp_path, config=MDRackConfig()) as engine:
        result = (engine.search_images_text('needle') if route == 'image'
                  else await engine.search_transcripts('needle', mode='text'))
        assert len(result.results) == 1
        assert engine._storage is None  # no cached writer needed for either search
    assert len(readers) == 1 and readers[0].closed
