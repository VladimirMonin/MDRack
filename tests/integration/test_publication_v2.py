"""File-backed current-v2 publication/lifecycle and explicit maintenance oracles."""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from mdrack_core.application.indexing import CoreIndexingService
from mdrack_core.domain import Locator, PreparedResourceBatch, RepresentationRecord, ResourceRecord, SearchUnitRecord
from mdrack_core.domain.publication import ExpectedPublication, PublicationState
from mdrack_sqlite import SQLiteCatalog, SQLiteCatalogError


def graph(text: str = "old", rid: str = "r") -> PreparedResourceBatch:
    return PreparedResourceBatch(
        ResourceRecord(rid, "document", "text/plain", "namespace", Locator("document", {"id": rid})),
        (RepresentationRecord("p" + rid, rid, "retrieval_text", "text", text),),
        (SearchUnitRecord("u" + rid, rid, "p" + rid, "whole_resource", "text", text, Locator("document", {}), 0),),
    )


def old_v2(path: Path) -> None:
    import mdrack_sqlite.migrations_v2 as migrations
    from mdrack_sqlite.contract_v2 import SQLITE_V2_MIGRATION_MANIFEST

    connection = sqlite3.connect(path)
    try:
        for i, (name, digest) in enumerate(SQLITE_V2_MIGRATION_MANIFEST[:5]):
            connection.executescript((migrations.get_v2_migrations_dir() / name).read_text("utf-8"))
            connection.execute(
                "INSERT INTO mdrack_sqlite_migrations(version,name,sha256) VALUES(?,?,?)", (f"{i:04d}", name, digest)
            )
            connection.commit()
        connection.execute(
            "INSERT INTO mdrack_sqlite_schema VALUES(1,?,?,?)",
            ("mdrack_sqlite_catalog_v2", "0004", "353facc85a46cfc30fea8511993e06aa49910b09d60fe7750cb1855df120dd98"),
        )
        connection.commit()
    finally:
        connection.close()


def test_lifecycle_all_writers_withdraw_delete_recreate(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    with SQLiteCatalog.create_v2(path) as c:
        never = c.read_snapshot("r")
        assert never.state == PublicationState("r", "never_created") and never.batch is None
        s = CoreIndexingService(c).index_if_current(graph(), ExpectedPublication(never.state, create=True)).state
        assert c.read_snapshot("r").batch == graph()
        c.replace_resource(graph("unconditional"))
        u = c.read_snapshot("r").state
        assert u.lifecycle_id == s.lifecycle_id and u.state_token != s.state_token
        assert not c.compare_and_publish(graph("stale"), ExpectedPublication(s)).applied
        w = c.withdraw_if_current("r", ExpectedPublication(u)).state
        assert w.status == "live_without_projections" and w.lifecycle_id == s.lifecycle_id
        assert c.read_resource("r") is None
        assert c.compare_and_publish(graph("return"), ExpectedPublication(w)).applied
        current = c.read_snapshot("r").state
        c.withdraw_if_current("r", ExpectedPublication(current))
        from mdrack.application.resource_catalog import delete_resource

        assert delete_resource(c, "r").deleted
        tombstone = c.read_snapshot("r").state
        assert tombstone.status == "deleted"
        assert not delete_resource(c, "r").deleted
        c.delete_resource("r")
        assert c.read_snapshot("r").state == tombstone
        c.replace_resource(graph())
        recreated = c.read_snapshot("r").state
        assert recreated.lifecycle_id != s.lifecycle_id
        assert not c.compare_and_publish(graph("late"), ExpectedPublication(s)).applied
        assert not c.compare_and_publish(graph("late"), ExpectedPublication(never.state, create=True)).applied
        c.verify()
    with SQLiteCatalog.open(path) as c:
        assert c.read_snapshot("r").state == recreated


@pytest.mark.parametrize(
    "point",
    [
        "after_begin",
        "after_delete",
        "after_representations",
        "after_units",
        "after_vectors",
        "after_facets",
        "after_fts",
        "after_state",
        "before_commit",
    ],
)
def test_failure_graph_and_token_rollback(tmp_path: Path, point: str) -> None:
    path = tmp_path / "catalog.sqlite3"
    with SQLiteCatalog.create_v2(path) as c:
        c.replace_resource(graph())
        before = c.read_snapshot("r")

        def fail(actual: str) -> None:
            if actual == point:
                raise RuntimeError("PRIVATE_EXCEPTION")

        c.set_failure_hook(fail)
        with pytest.raises(Exception):
            c.compare_and_publish(graph("new"), ExpectedPublication(before.state))
        c.set_failure_hook(None)
        assert not c.connection.in_transaction
    with SQLiteCatalog.open(path) as c:
        assert c.read_snapshot("r") == before
        c.verify()


def test_snapshot_and_two_connection_cas_barriers(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    with SQLiteCatalog.create_v2(path) as c:
        c.replace_resource(graph())
        before = c.read_snapshot("r")
    reached = threading.Event()
    proceed = threading.Event()
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            assert reached.wait(5)
            with SQLiteCatalog.open(path) as other:
                assert other.compare_and_publish(graph("new"), ExpectedPublication(before.state)).applied
        except BaseException as e:
            errors.append(e)
        finally:
            proceed.set()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        with SQLiteCatalog.open(path) as c:

            def barrier(point: str) -> None:
                if point == "after_snapshot_state":
                    reached.set()
                    assert proceed.wait(5)

            c.set_failure_hook(barrier)
            assert c.read_snapshot("r") == before
            c.set_failure_hook(None)
            after = c.read_snapshot("r")
            assert after.batch == graph("new") and after.state != before.state
            assert not c.compare_and_publish(graph("stale"), ExpectedPublication(before.state)).applied
            assert c.read_snapshot("r") == after
    finally:
        reached.set()
        thread.join(10)
    assert not thread.is_alive() and not errors


def test_validation_parity_external_transaction_and_fts(tmp_path: Path) -> None:
    with SQLiteCatalog.create_v2(tmp_path / "catalog.sqlite3") as c:
        c.replace_resource(graph())
        before = c.read_snapshot("r")
        bad = replace(graph(), units=())
        for operation in (
            lambda: CoreIndexingService(c).index(bad),
            lambda: CoreIndexingService(c).index_if_current(bad, ExpectedPublication(before.state)),
        ):
            with pytest.raises(Exception):
                operation()
            assert c.read_snapshot("r") == before
        c.connection.execute("BEGIN")
        for operation in (
            lambda: c.read_snapshot("r"),
            lambda: c.delete_resource("r"),
            lambda: c.compare_and_publish(graph(), ExpectedPublication(before.state)),
            lambda: c.rebuild_fts_index(),
        ):
            with pytest.raises(SQLiteCatalogError):
                operation()
            assert c.connection.in_transaction
        c.connection.rollback()
        assert c.rebuild_fts_index() == (1, 1)
        assert c.read_snapshot("r") == before


def test_explicit_upgrade_fresh_reopen_and_failure(tmp_path: Path) -> None:
    from mdrack_sqlite.resource_store import SQLiteResourceStore

    for failure in (True, False):
        directory = tmp_path / str(failure)
        directory.mkdir()
        path = directory / "catalog.sqlite3"
        old_v2(path)
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA foreign_keys=ON")
        store = SQLiteResourceStore(connection)
        store.replace_resource(graph())
        before = store.export_graph("r")
        connection.close()
        with pytest.raises(SQLiteCatalogError):
            SQLiteCatalog.open(path)

        def hook(point: str) -> None:
            if failure and point == "before_upgrade_commit":
                raise RuntimeError("PRIVATE_UPGRADE")

        backup = tmp_path / f"backup-{failure}.sqlite3"
        if failure:
            with pytest.raises(SQLiteCatalogError):
                SQLiteCatalog.upgrade_v2(path, backup, maintenance=True, failure_hook=hook)
            connection = sqlite3.connect(path)
            assert connection.execute("SELECT schema_version FROM mdrack_sqlite_schema").fetchone()[0] == "0004"
            assert (
                connection.execute("SELECT name FROM sqlite_master WHERE name='core_publication_states'").fetchone()
                is None
            )
            connection.close()
        else:
            assert SQLiteCatalog.upgrade_v2(path, backup, maintenance=True, failure_hook=hook)
            with SQLiteCatalog.open(path) as c:
                assert c.read_snapshot("r").batch == before
                assert c.read_snapshot("r").state.status == "published"
                c.verify()
            assert not SQLiteCatalog.upgrade_v2(path, tmp_path / "unused.sqlite3", maintenance=True)
            assert not (tmp_path / "unused.sqlite3").exists()
        assert backup.exists()


def test_unknown_signature_upgrade_no_write(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    old_v2(path)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE foreign_object(x)")
    connection.commit()
    connection.close()
    before = path.read_bytes()
    with pytest.raises(SQLiteCatalogError):
        SQLiteCatalog.upgrade_v2(path, tmp_path / "backup.sqlite3", maintenance=True)
    assert path.read_bytes() == before and not (tmp_path / "backup.sqlite3").exists()


def test_sink_failure_commit_token_and_repeat_oracle(tmp_path: Path) -> None:
    class Broken(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raise OSError("PRIVATE_SINK")

    logger = logging.getLogger("publication-control")
    handler = Broken()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    path = tmp_path / "catalog.sqlite3"
    try:
        with SQLiteCatalog.create_v2(path) as c:
            state = c.read_snapshot("r").state
            result = CoreIndexingService(c, logger=logger).index_if_current(
                graph(), ExpectedPublication(state, create=True)
            )
            assert result.applied
        with SQLiteCatalog.open(path) as c:
            assert c.read_snapshot("r").state == result.state
            assert c.read_snapshot("r").batch == graph()
            assert not c.compare_and_publish(graph(), ExpectedPublication(state, create=True)).applied
    finally:
        logger.removeHandler(handler)
