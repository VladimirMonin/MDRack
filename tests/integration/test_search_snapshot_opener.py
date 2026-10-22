"""The verified opener itself must not reject a torn FTS count observation."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from mdrack_sqlite import SQLiteCatalog
from tests.integration.test_search_read_sessions import batch


def test_readonly_open_verifies_one_state_then_releases_it(tmp_path: Path) -> None:
    path = tmp_path / 'catalog.sqlite3'
    with SQLiteCatalog.create_v2(path) as catalog:
        catalog.replace_resource(batch())
    ready, publish, committed = Event(), Event(), Event()

    def writer() -> None:
        with SQLiteCatalog.open(path) as catalog:
            ready.set()
            assert publish.wait(10)
            catalog.delete_resource('resource')
        committed.set()

    summaries = []
    fired = False

    class ObservedCatalog(SQLiteCatalog):
        def _verify_snapshot(self):
            def trace(sql):
                nonlocal fired
                if not fired and sql == 'SELECT COUNT(*) FROM core_search_units_fts':
                    fired = True
                    publish.set()
                    assert committed.wait(10)
            self.connection.set_trace_callback(trace)
            try:
                summary = super()._verify_snapshot()
                summaries.append(summary)
                return summary
            finally:
                self.connection.set_trace_callback(None)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        assert ready.wait(10)
        with ObservedCatalog.open_readonly(path) as catalog:
            assert fired and summaries[0].units == summaries[0].fts_rows == 1
            assert not catalog.connection.in_transaction
            # Preflight is deliberately not the subsequent search snapshot.
            assert catalog.read_resource('resource') is None
            assert catalog.verify().units == 0
        future.result(timeout=10)
