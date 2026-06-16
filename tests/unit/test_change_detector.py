"""Tests for the change detector."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mdrack.indexing.change_detector import compute_file_hash, detect_changes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_files_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE files (
            id TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL UNIQUE,
            title TEXT,
            source_hash TEXT NOT NULL,
            indexed_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        )
        """
    )
    conn.commit()


def _insert_file(
    conn: sqlite3.Connection,
    file_id: str,
    relative_path: str,
    source_hash: str,
    *,
    status: str = "active",
) -> None:
    conn.execute(
        "INSERT INTO files (id, relative_path, source_hash, indexed_at, status) "
        "VALUES (?, ?, ?, datetime('now'), ?)",
        (file_id, relative_path, source_hash, status),
    )
    conn.commit()


def _write(path: Path, content: str = "hello\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    _create_files_table(conn)
    return conn


# ---------------------------------------------------------------------------
# Hash computation
# ---------------------------------------------------------------------------

class TestComputeFileHash:
    def test_deterministic(self, tmp_path: Path) -> None:
        p = _write(tmp_path / "a.md", "content")
        h1 = compute_file_hash(p)
        h2 = compute_file_hash(p)
        assert h1 == h2

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        h1 = compute_file_hash(_write(tmp_path / "a.md", "alpha"))
        h2 = compute_file_hash(_write(tmp_path / "b.md", "beta"))
        assert h1 != h2

    def test_is_sha256(self, tmp_path: Path) -> None:
        h = compute_file_hash(_write(tmp_path / "a.md"))
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_file_not_found(self, tmp_path: Path) -> None:
        import pytest
        with pytest.raises(FileNotFoundError):
            compute_file_hash(tmp_path / "missing.md")


# ---------------------------------------------------------------------------
# New files detected
# ---------------------------------------------------------------------------

class TestNewFiles:
    def test_new_files_detected(self, tmp_path: Path) -> None:
        conn = _conn(tmp_path)
        _write(tmp_path / "new.md", "brand new")

        plan = detect_changes(conn, [Path("new.md")], tmp_path)

        assert plan.new_files == [Path("new.md")]
        assert plan.changed_files == []
        assert plan.unchanged_files == []
        assert plan.deleted_files == []


# ---------------------------------------------------------------------------
# Changed files detected
# ---------------------------------------------------------------------------

class TestChangedFiles:
    def test_changed_files_detected(self, tmp_path: Path) -> None:
        conn = _conn(tmp_path)
        original = _write(tmp_path / "doc.md", "version 1")
        h1 = compute_file_hash(original)
        _insert_file(conn, "f1", "doc.md", h1)

        original.write_text("version 2", encoding="utf-8")

        plan = detect_changes(conn, [Path("doc.md")], tmp_path)

        assert plan.changed_files == [Path("doc.md")]
        assert plan.new_files == []
        assert plan.unchanged_files == []
        assert plan.deleted_files == []


# ---------------------------------------------------------------------------
# Unchanged files detected
# ---------------------------------------------------------------------------

class TestUnchangedFiles:
    def test_unchanged_files_detected(self, tmp_path: Path) -> None:
        conn = _conn(tmp_path)
        p = _write(tmp_path / "doc.md", "stable")
        h = compute_file_hash(p)
        _insert_file(conn, "f1", "doc.md", h)

        plan = detect_changes(conn, [Path("doc.md")], tmp_path)

        assert plan.unchanged_files == [Path("doc.md")]
        assert plan.new_files == []
        assert plan.changed_files == []
        assert plan.deleted_files == []


# ---------------------------------------------------------------------------
# Deleted files detected
# ---------------------------------------------------------------------------

class TestDeletedFiles:
    def test_deleted_files_detected(self, tmp_path: Path) -> None:
        conn = _conn(tmp_path)
        _insert_file(conn, "f1", "gone.md", "deadbeef")

        plan = detect_changes(conn, [], tmp_path)

        assert plan.deleted_files == ["gone.md"]
        assert plan.new_files == []
        assert plan.changed_files == []
        assert plan.unchanged_files == []


# ---------------------------------------------------------------------------
# Empty scan
# ---------------------------------------------------------------------------

class TestEmptyScan:
    def test_empty_scan_no_files(self, tmp_path: Path) -> None:
        conn = _conn(tmp_path)

        plan = detect_changes(conn, [], tmp_path)

        assert plan.new_files == []
        assert plan.changed_files == []
        assert plan.unchanged_files == []
        assert plan.deleted_files == []

    def test_empty_db_no_files(self, tmp_path: Path) -> None:
        conn = _conn(tmp_path)
        _write(tmp_path / "a.md")

        plan = detect_changes(conn, [Path("a.md")], tmp_path)

        assert plan.new_files == [Path("a.md")]
        assert plan.unchanged_files == []
        assert plan.changed_files == []
        assert plan.deleted_files == []


# ---------------------------------------------------------------------------
# Idempotent scan
# ---------------------------------------------------------------------------

class TestIdempotentScan:
    def test_second_run_no_changes(self, tmp_path: Path) -> None:
        conn = _conn(tmp_path)

        _write(tmp_path / "a.md", "aaa")
        _write(tmp_path / "b.md", "bbb")
        files = [Path("a.md"), Path("b.md")]

        plan1 = detect_changes(conn, files, tmp_path)
        assert len(plan1.new_files) == 2

        for p in files:
            h = compute_file_hash(tmp_path / p)
            _insert_file(conn, str(p), p.as_posix(), h)

        plan2 = detect_changes(conn, files, tmp_path)
        assert plan2.new_files == []
        assert plan2.changed_files == []
        assert plan2.unchanged_files == [Path("a.md"), Path("b.md")]
        assert plan2.deleted_files == []


# ---------------------------------------------------------------------------
# Mixed scenario
# ---------------------------------------------------------------------------

class TestMixedScenario:
    def test_all_categories_in_one_scan(self, tmp_path: Path) -> None:
        conn = _conn(tmp_path)

        existing = _write(tmp_path / "keep.md", "original")
        gone = _write(tmp_path / "del.md", "bye")

        _insert_file(conn, "f1", "keep.md", compute_file_hash(existing))
        _insert_file(conn, "f2", "del.md", compute_file_hash(gone))

        existing.write_text("updated", encoding="utf-8")
        gone.unlink()
        _write(tmp_path / "fresh.md", "new")

        current = [Path("keep.md"), Path("fresh.md")]
        plan = detect_changes(conn, current, tmp_path)

        assert plan.changed_files == [Path("keep.md")]
        assert plan.new_files == [Path("fresh.md")]
        assert plan.deleted_files == ["del.md"]
        assert plan.unchanged_files == []
