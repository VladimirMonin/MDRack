"""Integration tests for text search with provenance enrichment."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mdrack.search.text import TextSearchResult, text_search
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.fts import FTSQueryError, upsert_fts
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


def _seed(conn):
    """Insert two files, two sections, and four chunks."""
    conn.execute(
        """INSERT INTO files (id, relative_path, source_hash, indexed_at)
           VALUES ('f1', 'docs/intro.md', 'h1', datetime('now'))""",
    )
    conn.execute(
        """INSERT INTO files (id, relative_path, source_hash, indexed_at)
           VALUES ('f2', 'docs/api.md', 'h2', datetime('now'))""",
    )
    conn.execute(
        """INSERT INTO sections (id, file_id, title, heading_path, level, start_line, end_line)
           VALUES ('s1', 'f1', 'Welcome', 'Welcome', 1, 1, 10)""",
    )
    conn.execute(
        """INSERT INTO sections (id, file_id, title, heading_path, level, start_line, end_line)
           VALUES ('s2', 'f2', 'Endpoints', 'API > Endpoints', 2, 1, 20)""",
    )
    conn.execute(
        """INSERT INTO chunks
           (id, file_id, section_id, content, content_type, chunk_index, heading_path)
           VALUES ('c1', 'f1', 's1', 'Hello world from MDRack', 'text', 0, 'Welcome')""",
    )
    conn.execute(
        """INSERT INTO chunks
           (id, file_id, section_id, content, content_type, chunk_index, heading_path)
           VALUES ('c2', 'f1', 's1', 'Another welcome paragraph', 'text', 1, 'Welcome')""",
    )
    conn.execute(
        """INSERT INTO chunks
           (id, file_id, section_id, content, content_type, chunk_index, heading_path)
           VALUES ('c3', 'f2', 's2', 'SQLite FTS5 is powerful', 'code', 0, 'API > Endpoints')""",
    )
    conn.execute(
        """INSERT INTO chunks
           (id, file_id, section_id, content, content_type, chunk_index, heading_path)
           VALUES ('c4', 'f2', 's2', 'Python integration example', 'text', 1, 'API > Endpoints')""",
    )
    conn.commit()

    upsert_fts(conn, "c1", "Hello world from MDRack", "text", "Welcome")
    upsert_fts(conn, "c2", "Another welcome paragraph", "text", "Welcome")
    upsert_fts(conn, "c3", "SQLite FTS5 is powerful", "code", "API > Endpoints")
    upsert_fts(conn, "c4", "Python integration example", "text", "API > Endpoints")


class TestBasicSearch:
    def test_returns_results(self):
        conn, db_path = _fresh_db()
        try:
            _seed(conn)
            result = text_search(conn, "Hello")
            assert isinstance(result, TextSearchResult)
            assert result.query == "Hello"
            assert len(result.results) == 1
            assert result.results[0].chunk_id == "c1"
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_provenance_fields(self):
        conn, db_path = _fresh_db()
        try:
            _seed(conn)
            result = text_search(conn, "SQLite")
            assert len(result.results) == 1
            item = result.results[0]
            assert item.file_relative_path == "docs/api.md"
            assert item.section_title == "Endpoints"
            assert item.heading_path == "API > Endpoints"
            assert item.score != 0
            assert "SQLite" in item.snippet or "FTS5" in item.snippet
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_multiple_results(self):
        conn, db_path = _fresh_db()
        try:
            _seed(conn)
            result = text_search(conn, "welcome")
            assert len(result.results) == 2
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)


class TestNoResults:
    def test_no_results(self):
        conn, db_path = _fresh_db()
        try:
            _seed(conn)
            result = text_search(conn, "nonexistent_term_xyz")
            assert result.results == []
            assert result.total_count == 0
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)


class TestInvalidQuery:
    def test_invalid_fts_query(self):
        conn, db_path = _fresh_db()
        try:
            _seed(conn)
            with pytest.raises(FTSQueryError, match="Invalid FTS query"):
                text_search(conn, 'NEAR (invalid syntax')
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_empty_query(self):
        conn, db_path = _fresh_db()
        try:
            with pytest.raises(FTSQueryError, match="must not be empty"):
                text_search(conn, "")
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)


class TestPagination:
    def test_limit(self):
        conn, db_path = _fresh_db()
        try:
            _seed(conn)
            result = text_search(conn, "welcome", limit=1)
            assert len(result.results) == 1
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_offset(self):
        conn, db_path = _fresh_db()
        try:
            _seed(conn)
            all_result = text_search(conn, "welcome", limit=10)
            assert len(all_result.results) == 2

            page1 = text_search(conn, "welcome", limit=1, offset=0)
            page2 = text_search(conn, "welcome", limit=1, offset=1)
            assert len(page1.results) == 1
            assert len(page2.results) == 1
            assert page1.results[0].chunk_id != page2.results[0].chunk_id
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_offset_beyond_total(self):
        conn, db_path = _fresh_db()
        try:
            _seed(conn)
            result = text_search(conn, "welcome", limit=10, offset=100)
            assert result.results == []
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)


class TestProvenanceJoin:
    def test_results_include_file_and_section(self):
        conn, db_path = _fresh_db()
        try:
            _seed(conn)
            result = text_search(conn, "Python")
            assert len(result.results) == 1
            item = result.results[0]
            assert item.file_relative_path == "docs/api.md"
            assert item.section_title == "Endpoints"
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)
