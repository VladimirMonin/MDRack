"""Integration tests for the rebuild FTS CLI command."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations

MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "mdrack" / "storage" / "sqlite" / "migrations"
)


def _fresh_db(tmp_path: Path) -> Path:
    """Create a temporary database with all migrations applied."""
    store_dir = tmp_path / ".mdrack"
    store_dir.mkdir(parents=True, exist_ok=True)
    db_path = store_dir / "knowledge.db"
    conn = get_connection(db_path)
    apply_migrations(conn, MIGRATIONS_DIR)
    conn.close()
    return db_path


def _seed_chunks(db_path: Path, count: int = 3) -> None:
    """Insert test data into the chunks table."""
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO files (id, relative_path, source_hash, indexed_at)
        VALUES ('f1', 'doc.md', 'abc', datetime('now'))
        """,
    )
    for i in range(count):
        conn.execute(
            """
            INSERT INTO chunks (id, file_id, content, content_type, chunk_index, heading_path)
            VALUES (?, 'f1', ?, 'text', ?, ?)
            """,
            (f"c{i}", f"Chunk {i} content", i, f"Section {i}"),
        )
    conn.commit()
    conn.close()


class TestRebuildFtsCommand:
    def test_rebuild_fts_inserts_all_chunks(self, tmp_path: Path) -> None:
        """Rebuild FTS should index all chunks from the chunks table."""
        db_path = _fresh_db(tmp_path)
        _seed_chunks(db_path, count=3)

        runner = CliRunner()
        result = runner.invoke(main, ["--root", str(tmp_path), "rebuild", "fts"])

        assert result.exit_code == 0
        output = __import__("json").loads(result.output)
        assert output["ok"] is True
        assert output["data"]["fts_count"] == 3
        assert output["data"]["chunk_count"] == 3

    def test_rebuild_fts_is_idempotent(self, tmp_path: Path) -> None:
        """Running rebuild twice should produce the same FTS count."""
        db_path = _fresh_db(tmp_path)
        _seed_chunks(db_path, count=2)

        runner = CliRunner()
        result1 = runner.invoke(main, ["--root", str(tmp_path), "rebuild", "fts"])
        result2 = runner.invoke(main, ["--root", str(tmp_path), "rebuild", "fts"])

        assert result1.exit_code == 0
        assert result2.exit_code == 0
        out1 = __import__("json").loads(result1.output)
        out2 = __import__("json").loads(result2.output)
        assert out1["data"]["fts_count"] == out2["data"]["fts_count"]
        assert out1["data"]["fts_count"] == 2

    def test_rebuild_fts_on_empty_store(self, tmp_path: Path) -> None:
        """Rebuild FTS on a database with no chunks should return 0."""
        _fresh_db(tmp_path)

        runner = CliRunner()
        result = runner.invoke(main, ["--root", str(tmp_path), "rebuild", "fts"])

        assert result.exit_code == 0
        output = __import__("json").loads(result.output)
        assert output["ok"] is True
        assert output["data"]["fts_count"] == 0
        assert output["data"]["chunk_count"] == 0

    def test_rebuild_fts_preserves_chunk_data(self, tmp_path: Path) -> None:
        """Rebuild FTS must not alter the chunks table."""
        db_path = _fresh_db(tmp_path)
        _seed_chunks(db_path, count=2)

        conn = get_connection(db_path)
        chunks_before = [
            dict(row) for row in conn.execute("SELECT * FROM chunks ORDER BY id").fetchall()
        ]
        conn.close()

        runner = CliRunner()
        result = runner.invoke(main, ["--root", str(tmp_path), "rebuild", "fts"])
        assert result.exit_code == 0

        conn = get_connection(db_path)
        chunks_after = [
            dict(row) for row in conn.execute("SELECT * FROM chunks ORDER BY id").fetchall()
        ]
        conn.close()

        assert len(chunks_before) == len(chunks_after)
        for before, after in zip(chunks_before, chunks_after):
            assert before["id"] == after["id"]
            assert before["content"] == after["content"]
            assert before["content_type"] == after["content_type"]
            assert before["heading_path"] == after["heading_path"]
