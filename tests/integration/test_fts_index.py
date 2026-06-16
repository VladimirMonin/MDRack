"""Integration tests for FTS5 full-text search index."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.fts import (
    FTSQueryError,
    delete_fts,
    rebuild_fts,
    search_fts,
    upsert_fts,
)
from mdrack.storage.sqlite.migrations import apply_migrations

MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "mdrack" / "storage" / "sqlite" / "migrations"
)


def _fresh_db():
    """Create a temporary database with all migrations applied."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    conn = get_connection(db_path)
    apply_migrations(conn, MIGRATIONS_DIR)
    return conn, db_path


def _seed_chunks(conn):
    """Insert two rows into the chunks table for FTS testing."""
    conn.execute(
        """
        INSERT INTO files (id, relative_path, source_hash, indexed_at)
        VALUES ('f1', 'doc.md', 'abc', datetime('now'))
        """,
    )
    conn.execute(
        """
        INSERT INTO chunks (id, file_id, content, content_type, chunk_index, heading_path)
        VALUES ('c1', 'f1', 'Hello world from MDRack', 'text', 0, 'Intro > Welcome')
        """,
    )
    conn.execute(
        """
        INSERT INTO chunks (id, file_id, content, content_type, chunk_index, heading_path)
        VALUES ('c2', 'f1', 'SQLite FTS5 is powerful', 'code', 1, 'Intro > FTS')
        """,
    )
    conn.commit()


class TestUpsertAndSearch:
    def test_upsert_and_search(self):
        conn, db_path = _fresh_db()
        try:
            _seed_chunks(conn)
            upsert_fts(conn, "c1", "Hello world from MDRack", "text", "Intro > Welcome")
            upsert_fts(conn, "c2", "SQLite FTS5 is powerful", "code", "Intro > FTS")

            results = search_fts(conn, "Hello")
            assert len(results) == 1
            assert results[0]["chunk_id"] == "c1"
            assert "rank" in results[0]
            assert "snippet" in results[0]
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_upsert_replaces_existing(self):
        conn, db_path = _fresh_db()
        try:
            _seed_chunks(conn)
            upsert_fts(conn, "c1", "Hello world from MDRack", "text", "Intro > Welcome")
            upsert_fts(conn, "c1", "Updated content for MDRack", "text", "Intro > Updated")

            results = search_fts(conn, "Updated content")
            assert len(results) == 1
            assert results[0]["chunk_id"] == "c1"
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_search_no_results(self):
        conn, db_path = _fresh_db()
        try:
            _seed_chunks(conn)
            upsert_fts(conn, "c1", "Hello world from MDRack", "text", "Intro > Welcome")

            results = search_fts(conn, "nonexistent")
            assert results == []
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)


class TestDelete:
    def test_delete(self):
        conn, db_path = _fresh_db()
        try:
            _seed_chunks(conn)
            upsert_fts(conn, "c1", "Hello world from MDRack", "text", "Intro > Welcome")
            upsert_fts(conn, "c2", "SQLite FTS5 is powerful", "code", "Intro > FTS")

            delete_fts(conn, "c1")
            results = search_fts(conn, "Hello")
            assert results == []

            results = search_fts(conn, "SQLite")
            assert len(results) == 1
            assert results[0]["chunk_id"] == "c2"
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_delete_nonexistent(self):
        conn, db_path = _fresh_db()
        try:
            _seed_chunks(conn)
            upsert_fts(conn, "c1", "Hello world from MDRack", "text", "Intro > Welcome")

            delete_fts(conn, "c999")
            results = search_fts(conn, "Hello")
            assert len(results) == 1
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)


class TestRebuild:
    def test_rebuild(self):
        conn, db_path = _fresh_db()
        try:
            _seed_chunks(conn)
            upsert_fts(conn, "c1", "Hello world from MDRack", "text", "Intro > Welcome")

            delete_fts(conn, "c1")
            results = search_fts(conn, "Hello")
            assert results == []

            rebuild_fts(conn)
            results = search_fts(conn, "Hello")
            assert len(results) == 1
            assert results[0]["chunk_id"] == "c1"
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_rebuild_populates_from_chunks(self):
        conn, db_path = _fresh_db()
        try:
            _seed_chunks(conn)
            rebuild_fts(conn)

            results = search_fts(conn, "Hello")
            assert len(results) == 1
            assert results[0]["chunk_id"] == "c1"

            results = search_fts(conn, "SQLite")
            assert len(results) == 1
            assert results[0]["chunk_id"] == "c2"
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)


class TestInvalidQuery:
    def test_invalid_fts_query_raises(self):
        conn, db_path = _fresh_db()
        try:
            _seed_chunks(conn)
            upsert_fts(conn, "c1", "Hello world from MDRack", "text", "Intro > Welcome")

            with pytest.raises(FTSQueryError, match="Invalid FTS query"):
                search_fts(conn, 'NEAR (invalid syntax')
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_empty_query_raises(self):
        conn, db_path = _fresh_db()
        try:
            with pytest.raises(FTSQueryError, match="must not be empty"):
                search_fts(conn, "")
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)
