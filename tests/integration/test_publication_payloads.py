"""Exact upgrade data preservation and token change on real producer facades."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from mdrack.adapters.sqlite.canonical_catalog import ApplicationStoreError
from mdrack.config.models import MDRackConfig
from mdrack.public_api import MDRackEngine
from mdrack_core import EmbeddingSpaceRecord, Facet, ResourceFacet, VectorRecord
from mdrack_sqlite import SQLiteCatalog, SQLiteCatalogError
from mdrack_sqlite.resource_store import SQLiteResourceStore
from tests.integration.test_publication_v2 import graph, old_v2


def test_old_v2_upgrade_preserves_all_graph_payload_bytes(tmp_path: Path) -> None:
    directory = tmp_path/'store'
    directory.mkdir()
    path = directory/'catalog.sqlite3'
    old_v2(path)
    batch = graph('published searchable payload')
    batch = replace(batch,
                    resource=replace(batch.resource, metadata={'nested': {'unknown_source_revision': None}}),
                    spaces=(EmbeddingSpaceRecord('space', 2, 'dot', 'producer-fingerprint'),),
                    vectors=(VectorRecord('ur', 'space', (1.25, -2.5)),),
                    facets=(ResourceFacet('r', Facet('tag', 'value'), 'source', confidence=0.5),))
    tables = ('core_resources','core_representations','core_search_units','core_embedding_spaces',
              'core_unit_embeddings','core_facets','core_resource_facets','core_search_units_fts',
              'mdrack_vector_codecs','mdrack_vector_backends')
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA foreign_keys=ON')
    store = SQLiteResourceStore(connection)
    store.replace_resource(batch)
    before = {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY rowid')]
              for table in tables}
    connection.close()
    assert SQLiteCatalog.upgrade_v2(path, tmp_path/'backup.sqlite3', maintenance=True)
    with SQLiteCatalog.open(path) as c:
        after = {table: [tuple(row) for row in c.connection.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                 for table in tables}
        assert before == after
        assert c.read_snapshot('r').batch == batch
        assert c.read_snapshot('r').state.status == 'published'
    assert not SQLiteCatalog.upgrade_v2(path, tmp_path/'backup.sqlite3', maintenance=True)


# These corruptions preserve the declared schema and (except FTS duplication)
# can preserve all old aggregate counts. Verification must inspect semantics.
_DAMAGE = (
    "orphan_graph", "empty_representation", "empty_unit", "modality",
    "fts_content", "fts_identity", "fts_duplicate", "fts_missing", "fts_dense", "fts_null",
)


def _damage_graph(connection: sqlite3.Connection, damage: str) -> None:
    if damage == "orphan_graph":
        connection.execute("DELETE FROM core_search_units_fts")
        connection.execute("DELETE FROM core_representations")
    elif damage == "empty_representation":
        connection.execute("DELETE FROM core_search_units WHERE unit_id='dense-u'")
    elif damage == "empty_unit":
        connection.execute("DELETE FROM core_unit_embeddings")
    elif damage == "modality":
        connection.execute("UPDATE core_search_units SET modality='image' WHERE unit_id='dense-u'")
    elif damage == "fts_content":
        connection.execute("UPDATE core_search_units_fts SET content='unpublished forged text'")
    elif damage == "fts_identity":
        connection.execute("UPDATE core_search_units_fts SET unit_id='absent-unit'")
    elif damage == "fts_duplicate":
        connection.execute("INSERT INTO core_search_units_fts SELECT * FROM core_search_units_fts")
    elif damage == "fts_missing":
        connection.execute("DELETE FROM core_search_units_fts")
    elif damage == "fts_dense":
        connection.execute("INSERT INTO core_search_units_fts VALUES('dense-u','PRIVATE_ARTIFACT')")
    else:
        assert damage == "fts_null"
        connection.execute("UPDATE core_search_units_fts SET content=NULL")
    connection.commit()


def _rich_graph():
    batch = graph("alpha searchable")
    return replace(
        batch,
        representations=(*batch.representations, replace(
            batch.representations[0], representation_id="dense-p", text=None,
            metadata={"artifact_text": "PRIVATE_ARTIFACT"},
        )),
        units=(*batch.units, replace(
            batch.units[0], unit_id="dense-u", representation_id="dense-p", text=None,
        )),
        spaces=(EmbeddingSpaceRecord("space", 2, "dot", "fingerprint"),),
        vectors=(VectorRecord("dense-u", "space", (1.25, -2.5)),),
        facets=(ResourceFacet("r", Facet("tag", "value"), "source"),),
    )


@pytest.mark.parametrize("damage", _DAMAGE)
@pytest.mark.parametrize("entry", ["engine", "launcher"])
def test_semantically_damaged_old_v2_refused_before_backup(tmp_path: Path, damage: str, entry: str) -> None:
    root = tmp_path / "root"
    store = root / ".mdrack"
    store.mkdir(parents=True)
    path = store / "catalog.sqlite3"
    old_v2(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        SQLiteResourceStore(connection).replace_resource(_rich_graph())
        _damage_graph(connection, damage)
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
    neighbour = store / "neighbour.txt"
    neighbour.write_bytes(b"preserve-neighbour")
    before = path.read_bytes()
    backup = tmp_path / "backup.sqlite3"
    if entry == "engine":
        with pytest.raises(ApplicationStoreError) as error:
            MDRackEngine(root=root, config=MDRackConfig()).upgrade_catalog(backup, maintenance=True)
        assert str(error.value) == "catalog_upgrade_failed"
    else:
        launcher = Path(sys.executable).with_name("mdrack.exe" if os.name == "nt" else "mdrack")
        assert launcher.is_file()
        child = subprocess.run(
            [str(launcher), "--root", str(root), "rebuild", "upgrade", "--backup", str(backup), "--maintenance"],
            cwd=tmp_path, capture_output=True, timeout=30,
        )
        (tmp_path / "launcher.stdout").write_bytes(child.stdout)
        (tmp_path / "launcher.stderr").write_bytes(child.stderr)
        assert child.returncode == 1, child.stdout + child.stderr
        payload = json.loads(child.stdout)
        assert payload["ok"] is False and payload["error"]["code"] == "CATALOG_UPGRADE_FAILED"
        assert str(path).encode() not in child.stdout + child.stderr
    assert not backup.exists() and path.read_bytes() == before
    assert neighbour.read_bytes() == b"preserve-neighbour"


@pytest.mark.parametrize("damage", _DAMAGE)
def test_target_verification_rejects_invalid_graph_and_fts(tmp_path: Path, damage: str) -> None:
    path = tmp_path / "catalog.sqlite3"
    with SQLiteCatalog.create_v2(path) as catalog:
        catalog.replace_resource(_rich_graph())
        _damage_graph(catalog.connection, damage)
        with pytest.raises(SQLiteCatalogError):
            catalog.verify()
        assert not catalog.connection.in_transaction
    before = path.read_bytes()
    for opener in (SQLiteCatalog.open, SQLiteCatalog.open_readonly):
        with pytest.raises(SQLiteCatalogError):
            opener(path)
    assert path.read_bytes() == before


def test_semantic_checks_cover_source_backup_lock_and_target(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "store" / "catalog.sqlite3"
    path.parent.mkdir()
    old_v2(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        SQLiteResourceStore(connection).replace_resource(_rich_graph())
    observed = []
    original = SQLiteCatalog._verify_graph_snapshot
    def observe(catalog):
        version = catalog.connection.execute("SELECT schema_version FROM mdrack_sqlite_schema").fetchone()[0]
        observed.append((catalog.connection, version, catalog.connection.in_transaction))
        return original(catalog)
    monkeypatch.setattr(SQLiteCatalog, "_verify_graph_snapshot", observe)
    assert SQLiteCatalog.upgrade_v2(path, tmp_path / "backup.sqlite3", maintenance=True)
    assert [(version, transaction) for _, version, transaction in observed[:4]] == [
        ("0004", True), ("0004", False), ("0004", True), ("0005", True),
    ]
    assert observed[0][0] is not observed[1][0] and observed[1][0] is not observed[2][0]
    assert observed[2][0] is observed[3][0]  # target verified before the same writer commits


def test_corrupted_backup_is_rejected_before_source_writer(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "store" / "catalog.sqlite3"
    path.parent.mkdir()
    old_v2(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        SQLiteResourceStore(connection).replace_resource(_rich_graph())
    before = path.read_bytes()
    real_connect = sqlite3.connect
    connections = []
    class CorruptingBackup(sqlite3.Connection):
        def backup(self, target, **kwargs):
            super().backup(target, **kwargs)
            target.execute("UPDATE core_search_units_fts SET content='forged backup text'")
            target.commit()
    def connect(*args, **kwargs):
        connections.append(str(args[0]))
        return real_connect(*args, factory=CorruptingBackup, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(SQLiteCatalogError):
        SQLiteCatalog.upgrade_v2(path, tmp_path / "backup.sqlite3", maintenance=True)
    assert path.read_bytes() == before
    assert not any("mode=rw" in uri for uri in connections)


def test_fts_verification_and_repair_use_writer_text_eligibility(tmp_path: Path) -> None:
    batch = _rich_graph()
    batch = replace(batch, units=(batch.units[0], replace(batch.units[1], text="\t\n\u2003")))
    with SQLiteCatalog.create_v2(tmp_path / "catalog.sqlite3") as catalog:
        catalog.replace_resource(batch)
        before = catalog.read_snapshot("r")
        assert catalog.verify().fts_rows == 1
        assert catalog.rebuild_fts_index() == (1, 1)
        assert catalog.verify().fts_rows == 1
        assert catalog.read_snapshot("r") == before

