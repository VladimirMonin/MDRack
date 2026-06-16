"""Integration tests for repository query functions."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations
from mdrack.storage.sqlite.repositories import (
    count_chunks,
    count_embeddings,
    count_files,
    get_chunk,
    get_file,
    get_file_by_path,
    get_neighbors,
    get_section,
    list_files,
    list_sections,
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "src" / "mdrack" / "storage" / "sqlite" / "migrations"


@pytest.fixture()
def db_conn() -> sqlite3.Connection:
    """Create a fresh database with migrations applied."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = get_connection(Path(tmp.name))
    apply_migrations(conn, MIGRATIONS_DIR)
    yield conn
    conn.close()
    Path(tmp.name).unlink(missing_ok=True)


def _seed_data(conn: sqlite3.Connection) -> None:
    """Insert sample files, sections, chunks, and embeddings."""
    conn.execute(
        "INSERT INTO files (id, relative_path, title, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("f1", "docs/intro.md", "Intro", "abc123", "2026-01-15T10:00:00"),
    )
    conn.execute(
        "INSERT INTO files (id, relative_path, title, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("f2", "docs/guide.md", "Guide", "def456", "2026-01-15T10:05:00"),
    )
    conn.execute(
        "INSERT INTO files (id, relative_path, title, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("f3", "docs/setup.md", "Setup", "ghi789", "2026-01-15T10:10:00"),
    )

    conn.execute(
        "INSERT INTO sections (id, file_id, title, level, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("s1", "f1", "Introduction", 1, 1, 10),
    )
    conn.execute(
        "INSERT INTO sections (id, file_id, title, level, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("s2", "f1", "Getting Started", 2, 11, 20),
    )
    conn.execute(
        "INSERT INTO sections (id, file_id, title, level, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("s3", "f2", "Overview", 1, 1, 15),
    )

    conn.execute(
        "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index, "
        "heading_path, previous_chunk_id, next_chunk_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c1", "f1", "s1", "Hello world", "text", 0, "Introduction", None, "c2"),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index, "
        "heading_path, previous_chunk_id, next_chunk_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c2", "f1", "s1", "This is intro text", "text", 1, "Introduction", "c1", "c3"),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index, "
        "heading_path, previous_chunk_id, next_chunk_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c3", "f1", "s2", "Step one of the guide", "text", 2, "Getting Started", "c2", None),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index, "
        "heading_path, previous_chunk_id, next_chunk_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c4", "f2", "s3", "Guide overview content", "text", 0, "Overview", None, None),
    )

    conn.execute(
        "INSERT INTO embedding_profiles (name, model, dimensions) VALUES (?, ?, ?)",
        ("default", "test-model", 128),
    )
    conn.execute(
        "INSERT INTO chunk_embeddings (chunk_id, profile_name, embedding, embedded_at) "
        "VALUES (?, ?, ?, ?)",
        ("c1", "default", b"\x00" * 128, "2026-01-15T10:00:00"),
    )
    conn.execute(
        "INSERT INTO chunk_embeddings (chunk_id, profile_name, embedding, embedded_at) "
        "VALUES (?, ?, ?, ?)",
        ("c2", "default", b"\x01" * 128, "2026-01-15T10:00:00"),
    )
    conn.commit()


# --- list_files ---

def test_list_files_pagination(db_conn: sqlite3.Connection) -> None:
    _seed_data(db_conn)
    page1 = list_files(db_conn, offset=0, limit=2)
    assert len(page1) == 2
    assert page1[0]["relative_path"] == "docs/guide.md"
    assert page1[1]["relative_path"] == "docs/intro.md"

    page2 = list_files(db_conn, offset=2, limit=2)
    assert len(page2) == 1
    assert page2[0]["relative_path"] == "docs/setup.md"


def test_list_files_empty(db_conn: sqlite3.Connection) -> None:
    assert list_files(db_conn) == []


# --- get_file ---

def test_get_file_exists(db_conn: sqlite3.Connection) -> None:
    _seed_data(db_conn)
    f = get_file(db_conn, "f1")
    assert f is not None
    assert f["id"] == "f1"
    assert f["relative_path"] == "docs/intro.md"


def test_get_file_not_found(db_conn: sqlite3.Connection) -> None:
    assert get_file(db_conn, "nonexistent") is None


# --- get_file_by_path ---

def test_get_file_by_path_exists(db_conn: sqlite3.Connection) -> None:
    _seed_data(db_conn)
    f = get_file_by_path(db_conn, "docs/guide.md")
    assert f is not None
    assert f["id"] == "f2"


def test_get_file_by_path_not_found(db_conn: sqlite3.Connection) -> None:
    assert get_file_by_path(db_conn, "no/such/path.md") is None


# --- list_sections ---

def test_list_sections_ordered(db_conn: sqlite3.Connection) -> None:
    _seed_data(db_conn)
    sections = list_sections(db_conn, "f1")
    assert len(sections) == 2
    assert sections[0]["title"] == "Introduction"
    assert sections[1]["title"] == "Getting Started"
    assert sections[0]["start_line"] < sections[1]["start_line"]


def test_list_sections_empty(db_conn: sqlite3.Connection) -> None:
    assert list_sections(db_conn, "nonexistent") == []


# --- get_section ---

def test_get_section_exists(db_conn: sqlite3.Connection) -> None:
    _seed_data(db_conn)
    s = get_section(db_conn, "s3")
    assert s is not None
    assert s["title"] == "Overview"


def test_get_section_not_found(db_conn: sqlite3.Connection) -> None:
    assert get_section(db_conn, "nonexistent") is None


# --- get_chunk ---

def test_get_chunk_exists(db_conn: sqlite3.Connection) -> None:
    _seed_data(db_conn)
    c = get_chunk(db_conn, "c1")
    assert c is not None
    assert c["content"] == "Hello world"


def test_get_chunk_not_found(db_conn: sqlite3.Connection) -> None:
    assert get_chunk(db_conn, "nonexistent") is None


# --- get_neighbors ---

def test_get_neighbors_middle(db_conn: sqlite3.Connection) -> None:
    _seed_data(db_conn)
    neighbors = get_neighbors(db_conn, "c2", count=1)
    assert len(neighbors) == 2
    assert neighbors[0]["id"] == "c1"
    assert neighbors[1]["id"] == "c3"


def test_get_neighbors_first_chunk_no_previous(db_conn: sqlite3.Connection) -> None:
    _seed_data(db_conn)
    neighbors = get_neighbors(db_conn, "c1", count=2)
    assert len(neighbors) == 2
    assert neighbors[0]["id"] == "c2"
    assert neighbors[1]["id"] == "c3"


def test_get_neighbors_last_chunk_no_next(db_conn: sqlite3.Connection) -> None:
    _seed_data(db_conn)
    neighbors = get_neighbors(db_conn, "c3", count=2)
    assert len(neighbors) == 2
    assert neighbors[0]["id"] == "c1"
    assert neighbors[1]["id"] == "c2"


def test_get_neighbors_nonexistent(db_conn: sqlite3.Connection) -> None:
    assert get_neighbors(db_conn, "nonexistent") == []


# --- count functions ---

def test_count_files(db_conn: sqlite3.Connection) -> None:
    assert count_files(db_conn) == 0
    _seed_data(db_conn)
    assert count_files(db_conn) == 3


def test_count_chunks(db_conn: sqlite3.Connection) -> None:
    assert count_chunks(db_conn) == 0
    _seed_data(db_conn)
    assert count_chunks(db_conn) == 4


def test_count_embeddings(db_conn: sqlite3.Connection) -> None:
    assert count_embeddings(db_conn) == 0
    _seed_data(db_conn)
    assert count_embeddings(db_conn, "default") == 2
    assert count_embeddings(db_conn, "unknown") == 0
