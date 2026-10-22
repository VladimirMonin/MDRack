"""Real child termination, guarded image CAS and immutable upgrade payloads."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from mdrack_core import ExpectedPublication
from mdrack_core.application.indexing import CoreIndexingService
from mdrack_sqlite import SQLiteCatalog
from tests.integration.test_publication_v2 import graph, old_v2


@pytest.mark.parametrize('point,version', [('before_upgrade_commit','0004'), ('after_upgrade_commit','0005')])
def test_real_upgrade_child_interruption(tmp_path: Path, point: str, version: str) -> None:
    directory = tmp_path/'store'
    directory.mkdir()
    path = directory/'catalog.sqlite3'
    old_v2(path)
    code = """
import os,sys
from mdrack_sqlite import SQLiteCatalog
point=sys.argv[3]
def hook(actual):
    if actual==point:os._exit(71)
SQLiteCatalog.upgrade_v2(sys.argv[1],sys.argv[2],maintenance=True,failure_hook=hook)
"""
    child = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(path),
                            str(tmp_path/'backup.sqlite3'), point], capture_output=True, timeout=30)
    assert child.returncode == 71, child.stderr
    with sqlite3.connect(path) as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert c.execute('PRAGMA foreign_key_check').fetchone() is None
        assert c.execute('SELECT schema_version FROM mdrack_sqlite_schema').fetchone()[0] == version
    if version == '0005':
        with SQLiteCatalog.open(path) as c:
            c.verify()
    else:
        from mdrack_sqlite.upgrade import _verify_old
        with sqlite3.connect(path) as c:
            c.execute('PRAGMA foreign_keys=ON')
            _verify_old(c)


def test_real_source_guard_conditional_publication(tmp_path: Path) -> None:
    from mdrack.ingestion.images import _GuardedWritePort, _image_signature_probe
    from mdrack.ingestion.raw_source_provenance import RawInputBudget, RawMediaKind, RawSourceError, capture_source
    from tests.integration.test_native_image_v2 import PNG
    path = tmp_path/'image.png'
    path.write_bytes(PNG)
    budget = RawInputBudget()
    snapshot = capture_source(path, 'image.png', RawMediaKind.IMAGE, budget=budget,
                              signature_probe=_image_signature_probe)
    try:
        with SQLiteCatalog.create_v2(tmp_path/'catalog.sqlite3') as c:
            c.replace_resource(graph())
            old = c.read_snapshot('r')
            guarded = _GuardedWritePort(c, snapshot, path, budget)
            expected = ExpectedPublication(old.state)
            assert CoreIndexingService(guarded).index_if_current(graph('guarded'), expected).applied
            before = c.read_snapshot('r')
            path.write_bytes(PNG+b'changed')
            with pytest.raises(Exception):
                CoreIndexingService(guarded).index_if_current(graph('stale source'), ExpectedPublication(before.state))
            assert isinstance(guarded.source_error, RawSourceError)
            assert c.read_snapshot('r') == before
    finally:
        snapshot.cleanup()
