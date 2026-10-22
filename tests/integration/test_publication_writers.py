"""Actual writer/facade, replay and serialization controls on current app v2."""

from __future__ import annotations

import io
import json
import logging
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from mdrack_core import EmbeddingSpaceRecord, ExpectedPublication, VectorRecord
from mdrack_core.application.indexing import CoreIndexingService
from mdrack_sqlite import SQLiteCatalog
from tests.integration.test_publication_v2 import graph, old_v2


def test_parallel_publish_exactly_one_and_explicit_retry(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    with SQLiteCatalog.create_v2(path) as c:
        c.replace_resource(graph())
        state = c.read_snapshot("r").state
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def work(text: str) -> None:
        try:
            with SQLiteCatalog.open(path) as c:
                barrier.wait(5)
                results.append(c.compare_and_publish(graph(text), ExpectedPublication(state)))
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=work, args=(text,)) for text in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert all(not t.is_alive() for t in threads) and not errors
    assert sorted(r.applied for r in results) == [False, True]
    with SQLiteCatalog.open(path) as c:
        current = c.read_snapshot("r")
        assert current.state == next(r.state for r in results if r.applied)
        # Storage re-publication only, not claiming W4 purpose-aware merge.
        assert c.compare_and_publish(graph("explicit retry"), ExpectedPublication(current.state)).applied


def test_import_export_is_graph_not_authority_and_fts_dense_only(tmp_path: Path) -> None:
    from mdrack.application.manifest import PreparedResourceFacade
    from mdrack.application.resource_catalog import PreparedResourceExportService

    with SQLiteCatalog.create_v2(tmp_path / "a.sqlite3") as a, SQLiteCatalog.create_v2(tmp_path / "b.sqlite3") as b:
        dense = replace(
            graph(),
            representations=(replace(graph().representations[0], text=None),),
            units=(replace(graph().units[0], text=None),),
            spaces=(EmbeddingSpaceRecord("s", 2, "dot", "fingerprint"),),
            vectors=(VectorRecord("ur", "s", (1.0, 2.0)),),
        )
        a.replace_resource(dense)
        state = a.read_snapshot("r").state
        payload = PreparedResourceExportService(a).export_bytes("r")
        assert state.state_token.encode() not in payload
        assert state.lifecycle_id.encode() not in payload
        facade = PreparedResourceFacade(b)
        facade.import_manifest(payload)
        local = b.read_snapshot("r")
        assert local.batch == dense and local.state.lifecycle_id != state.lifecycle_id
        facade.import_manifest(payload)
        assert b.read_snapshot("r").state.state_token != local.state.state_token
        before = b.read_snapshot("r")
        assert b.rebuild_fts_index() == (0, 0)
        assert b.read_snapshot("r") == before


def test_app_composition_retains_handle_and_withdrawn_markdown_delete(tmp_path: Path) -> None:
    from mdrack.application.compatibility import create_application_storage
    from mdrack.config.models import MDRackConfig as Config

    config = Config()
    storage = create_application_storage(tmp_path, config, create=True)
    try:
        assert storage.resource_store is storage.catalog
        batch = graph()
        batch = replace(
            batch,
            resource=replace(
                batch.resource,
                metadata={
                    "relative_path": "note.md",
                    "ingestion": {"adapter": "markdown"},
                },
            ),
        )
        storage.resource_store.replace_resource(batch)
        state = storage.resource_store.read_snapshot("r").state
        storage.resource_store.withdraw_if_current("r", ExpectedPublication(state))
        storage.delete_file("note.md")
        assert storage.resource_store.read_snapshot("r").state.status == "deleted"
    finally:
        catalog = storage.catalog
        storage.close()
    assert catalog.closed


def test_cancel_rollback_and_successful_next_operation(tmp_path: Path) -> None:
    import asyncio

    with SQLiteCatalog.create_v2(tmp_path / "catalog.sqlite3") as c:
        c.replace_resource(graph())
        before = c.read_snapshot("r")

        def cancel(point: str) -> None:
            if point == "after_state":
                raise asyncio.CancelledError

        c.set_failure_hook(cancel)
        with pytest.raises(asyncio.CancelledError):
            c.compare_and_publish(graph("new"), ExpectedPublication(before.state))
        c.set_failure_hook(None)
        assert c.read_snapshot("r") == before and not c.connection.in_transaction
        assert c.compare_and_publish(graph("next"), ExpectedPublication(before.state)).applied


def test_actual_serialized_publication_outcomes_and_no_tokens(tmp_path: Path) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("publication-byte-oracle")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        with SQLiteCatalog.create_v2(tmp_path / "catalog.sqlite3") as c:
            state = c.read_snapshot("r").state
            service = CoreIndexingService(c, logger=logger)
            expected = ExpectedPublication(state, create=True)
            result = service.index_if_current(graph("PRIVATE_CONTENT"), expected)
            assert result.applied
            assert not service.index_if_current(graph("PRIVATE_CONTENT"), expected).applied
            handler.flush()
            data = stream.getvalue().encode()
            (tmp_path / "serialized.log").write_bytes(data)
            assert data and b"PRIVATE_CONTENT" not in data
            assert result.state.state_token.encode() not in data
            assert result.state.lifecycle_id.encode() not in data
            assert b'"publication_applied":true' in data.replace(b" ", b"")
            assert b'"publication_applied":false' in data.replace(b" ", b"")
            assert b'"vector_count":0' in data.replace(b" ", b"")
    finally:
        logger.removeHandler(handler)


def test_upgrade_engine_and_cli_maintenance(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from mdrack.cli import main
    from mdrack.config.models import MDRackConfig as Config
    from mdrack.public_api import MDRackEngine

    root = tmp_path / "root"
    store = root / ".mdrack"
    store.mkdir(parents=True)
    old_v2(store / "catalog.sqlite3")
    engine = MDRackEngine(root=root, config=Config())
    assert engine.upgrade_catalog(tmp_path / "engine-backup.sqlite3", maintenance=True)
    # Real registered Click route, idempotent target, no alternate DB.
    result = CliRunner().invoke(
        main, ["--root", str(root), "rebuild", "upgrade", "--backup", str(tmp_path / "unused.sqlite3"), "--maintenance"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["upgraded"] is False
    assert not (tmp_path / "unused.sqlite3").exists()
    with sqlite3.connect(store / "catalog.sqlite3") as c:
        assert c.execute("SELECT schema_version FROM mdrack_sqlite_schema").fetchone()[0] == "0005"


def test_commit_ack_lost_reopen_and_repeat(tmp_path: Path, monkeypatch) -> None:
    from mdrack_core import CatalogExecutionError
    path = tmp_path / "catalog.sqlite3"
    committed = []
    with SQLiteCatalog.create_v2(path) as c:
        expected = ExpectedPublication(c.read_snapshot("r").state, create=True)
        publish = c.compare_and_publish
        def lose_ack(batch, state):
            result = publish(batch, state)
            committed.append(result)
            raise TimeoutError("PRIVATE_RESPONSE_LOSS")
        with monkeypatch.context() as patch:
            patch.setattr(c, "compare_and_publish", lose_ack)
            with pytest.raises(CatalogExecutionError):
                CoreIndexingService(c).index_if_current(graph(), expected)
        assert committed[0].applied and not c.connection.in_transaction
    with SQLiteCatalog.open(path) as c:
        before = c.read_snapshot("r")
        assert before.state == committed[0].state and before.batch == graph()
        assert not c.compare_and_publish(graph(), expected).applied
        assert c.read_snapshot("r") == before


def test_repeated_publication_and_withdrawal_advance_token_not_lifecycle(tmp_path: Path) -> None:
    with SQLiteCatalog.create_v2(tmp_path / "catalog.sqlite3") as c:
        c.replace_resource(graph())
        initial = current = c.read_snapshot("r").state
        seen = {initial.state_token}
        for _ in range(3):
            current = c.compare_and_publish(graph(), ExpectedPublication(current)).state
            assert current.lifecycle_id == initial.lifecycle_id and current.state_token not in seen
            seen.add(current.state_token)
        for _ in range(3):
            current = c.withdraw_if_current("r", ExpectedPublication(current)).state
            assert current.lifecycle_id == initial.lifecycle_id and current.state_token not in seen
            assert c.read_snapshot("r").batch is None
            seen.add(current.state_token)
        assert c.delete_if_current("r", ExpectedPublication(current)).applied


@pytest.mark.parametrize(
    "method", ["delete_resource", "delete_live_resource", "delete_if_current", "withdraw_if_current"]
)
@pytest.mark.parametrize("failure", ["catalog", "runtime", "cancel"])
def test_every_removal_failure_rolls_back_graph_and_state(tmp_path: Path, method: str, failure: str) -> None:
    import asyncio

    from mdrack_core import CatalogExecutionError, ErrorCategory
    with SQLiteCatalog.create_v2(tmp_path / "catalog.sqlite3") as c:
        c.replace_resource(graph())
        before = c.read_snapshot("r")
        def fail(point: str) -> None:
            if point == "after_state":
                if failure == "catalog":
                    raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR)
                if failure == "cancel":
                    raise asyncio.CancelledError
                raise RuntimeError("PRIVATE_DELETE_FAILURE")
        c.set_failure_hook(fail)
        with pytest.raises(BaseException):
            operation = getattr(c, method)
            if method.endswith("if_current"):
                operation("r", ExpectedPublication(before.state))
            else:
                operation("r")
        c.set_failure_hook(None)
        assert not c.connection.in_transaction and c.read_snapshot("r") == before
        c.verify()
