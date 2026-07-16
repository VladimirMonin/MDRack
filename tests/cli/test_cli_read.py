"""Tests for the read CLI commands."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.config.loader import load_config
from mdrack.config.models import MDRackConfig
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "src" / "mdrack" / "storage" / "sqlite" / "migrations"


def _setup_db(tmp_path: Path) -> Path:
    """Create a DB with schema migrations applied in tmp_path."""
    store_dir = tmp_path / ".mdrack"
    store_dir.mkdir()
    db_path = store_dir / "knowledge.db"  # Match CLI expectation
    conn = get_connection(db_path)
    try:
        apply_migrations(conn, _MIGRATIONS_DIR)
    finally:
        conn.close()
    return db_path


def _setup_db_at_store(root: Path, store_name: str) -> Path:
    store_dir = root / store_name
    store_dir.mkdir()
    db_path = store_dir / "knowledge.db"
    conn = get_connection(db_path)
    try:
        apply_migrations(conn, _MIGRATIONS_DIR)
    finally:
        conn.close()
    return db_path


def _write_config(root: Path, store: str) -> None:
    config_dir = root / ".mdrack"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "config.toml").write_text(
        f"[paths]\nstore = \"{store}\"\n",
        encoding="utf-8",
    )


def _seed_data(conn: sqlite3.Connection) -> None:
    """Insert sample files, sections, chunks."""
    conn.execute(
        "INSERT INTO files (id, relative_path, title, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("file1", "docs/test1.md", "Test1", "hash1", "2026-01-15T10:00:00"),
    )
    conn.execute(
        "INSERT INTO files (id, relative_path, title, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("file2", "docs/test2.md", "Test2", "hash2", "2026-01-15T10:05:00"),
    )

    conn.execute(
        "INSERT INTO sections (id, file_id, title, level, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("section1", "file1", "Section 1", 1, 1, 10),
    )
    conn.execute(
        "INSERT INTO sections (id, file_id, title, level, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("section2", "file1", "Section 2", 2, 11, 20),
    )
    conn.execute(
        "INSERT INTO sections (id, file_id, title, level, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("section3", "file2", "Section A", 1, 1, 15),
    )

    conn.execute(
        "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index, "
        "heading_path, previous_chunk_id, next_chunk_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("chunk1", "file1", "section1", "Chunk 1 content", "text", 0, "Section 1", None, "chunk2"),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index, "
        "heading_path, previous_chunk_id, next_chunk_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("chunk2", "file1", "section1", "Chunk 2 content", "text", 1, "Section 1", "chunk1", "chunk3"),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index, "
        "heading_path, previous_chunk_id, next_chunk_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("chunk3", "file1", "section2", "Chunk 3 content", "text", 0, "Section 2", "chunk2", None),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index, "
        "heading_path, previous_chunk_id, next_chunk_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("chunk4", "file2", "section3", "Chunk 4 content", "text", 0, "Section A", None, None),
    )
    conn.commit()


def _make_config(tmp_path: Path) -> MDRackConfig:
    """Return a config rooted at tmp_path."""
    return load_config(
        cli_overrides={
            "paths.root": str(tmp_path),
            "paths.store": ".mdrack",
        }
    )


@pytest.fixture()
def seeded_db(tmp_path: Path) -> sqlite3.Connection:
    """Create a seeded database for testing."""
    db_path = _setup_db(tmp_path)
    conn = get_connection(db_path)
    _seed_data(conn)
    yield conn
    conn.close()


def test_read_chunk_returns_valid_json(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """`mdrack read chunk <chunk_id>` should return a valid JSON envelope."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "chunk", "chunk1"],
    )
    assert result.exit_code == 0, f"chunk read failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "data" in payload
    assert "meta" in payload
    assert "chunk" in payload["data"]
    assert payload["data"]["chunk"]["id"] == "chunk1"


def test_read_section_returns_valid_json(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """`mdrack read section <section_id>` should return a valid JSON envelope."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "section", "section1"],
    )
    assert result.exit_code == 0, f"section read failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "data" in payload
    assert "meta" in payload
    assert "section" in payload["data"]
    assert payload["data"]["section"]["id"] == "section1"
    assert "chunks" in payload["data"]
    assert len(payload["data"]["chunks"]) == 2  # chunk1 and chunk2


def test_read_file_returns_valid_json(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """`mdrack read file <file_id>` should return a valid JSON envelope."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "file", "file1"],
    )
    assert result.exit_code == 0, f"file read failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "data" in payload
    assert "meta" in payload
    assert "file" in payload["data"]
    assert payload["data"]["file"]["id"] == "file1"
    assert "sections" in payload["data"]
    assert len(payload["data"]["sections"]) == 2  # section1 and section2


def test_read_chunk_with_context_neighbors(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """`mdrack read chunk <chunk_id> --context neighbors` should return chunk + neighbors."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "chunk", "chunk2", "--context", "neighbors"],
    )
    assert result.exit_code == 0, f"chunk read with neighbors failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "data" in payload
    assert "neighbors" in payload["data"]
    neighbors = payload["data"]["neighbors"]
    assert len(neighbors) == 2  # previous (chunk1) and next (chunk3)
    assert neighbors[0]["id"] == "chunk1"
    assert neighbors[1]["id"] == "chunk3"


def test_read_chunk_with_context_neighbors_at_start(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """`mdrack read chunk` with --context neighbors at start should only have next neighbor."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "chunk", "chunk1", "--context", "neighbors"],
    )
    assert result.exit_code == 0, f"chunk read with neighbors failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    neighbors = payload["data"].get("neighbors", [])
    assert len(neighbors) == 1  # only next chunk (chunk2), no previous
    assert neighbors[0]["id"] == "chunk2"


def test_read_chunk_with_context_neighbors_at_end(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """`mdrack read chunk` with --context neighbors at end should only have previous neighbor."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "chunk", "chunk3", "--context", "neighbors"],
    )
    assert result.exit_code == 0, f"chunk read with neighbors failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    neighbors = payload["data"].get("neighbors", [])
    assert len(neighbors) == 1  # only previous chunk (chunk2)
    assert neighbors[0]["id"] == "chunk2"


def test_read_non_existent_chunk_returns_error(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """Reading a non-existent chunk should return an error envelope."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "chunk", "nonexistent"],
    )
    assert result.exit_code == 1, f"expected error but exit code was {result.exit_code}"
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "error" in payload
    assert payload["error"]["code"] == "NOT_FOUND"


def test_read_non_existent_section_returns_error(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """Reading a non-existent section should return an error envelope."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "section", "nonexistent"],
    )
    assert result.exit_code == 1, f"expected error but exit code was {result.exit_code}"
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "error" in payload
    assert payload["error"]["code"] == "NOT_FOUND"


def test_read_non_existent_file_returns_error(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """Reading a non-existent file should return an error envelope."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "file", "nonexistent"],
    )
    assert result.exit_code == 1, f"expected error but exit code was {result.exit_code}"
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "error" in payload
    assert payload["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    ("entity", "expected_message"),
    [
        ("chunk", "Chunk not found"),
        ("section", "Section not found"),
        ("file", "File not found"),
    ],
)
def test_read_not_found_does_not_reflect_private_identifier(
    seeded_db: sqlite3.Connection,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    entity: str,
    expected_message: str,
) -> None:
    sentinel = "PRIVATE_READ_ID_SENTINEL_/home/v/secret-note.md"
    caplog.set_level(logging.DEBUG)
    result = CliRunner().invoke(main, ["--root", str(tmp_path), "read", entity, sentinel])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["error"] == {"message": expected_message, "code": "NOT_FOUND"}
    observed = "\n".join((result.stdout, result.stderr, caplog.text))
    assert sentinel not in observed


def _documented_read_example(command: str, example: str) -> dict[str, object]:
    contracts_path = Path(__file__).resolve().parents[2] / "docs" / "cli-contracts.md"
    contracts = contracts_path.read_text(encoding="utf-8")
    command_section = contracts.split(f"`mdrack read {command}`", 1)[1].split("\n## ", 1)[0]
    example_section = command_section.split(f"### {example}", 1)[1]
    match = re.search(r"```json\n(.*?)\n```", example_section, flags=re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


def test_documented_read_examples_match_public_logical_id_contracts() -> None:
    locator_keys = {
        "root_id", "relative_path", "start_line", "end_line", "start_offset",
        "end_offset", "heading_path", "block_kind", "chunk_kind",
        "block_logical_id", "chunk_logical_id",
    }
    chunk_keys = {
        "id", "logical_id", "content", "content_type", "chunk_index",
        "heading_path", "embedding_text_hash", "source_locator",
    }
    section_keys = {
        "id", "logical_id", "title", "heading_path", "level", "start_line", "end_line",
    }
    file_keys = {
        "id", "logical_id", "root_id", "relative_path", "title", "source_hash",
        "indexed_at", "status", "parser_name", "parser_version",
        "chunk_strategy_name", "chunk_strategy_version",
    }

    chunk_payload = _documented_read_example("chunk", "Success (without context)")
    section_payload = _documented_read_example("section", "Success")
    file_payload = _documented_read_example("file", "Success")
    chunk = chunk_payload["data"]["chunk"]  # type: ignore[index]
    section = section_payload["data"]["section"]  # type: ignore[index]
    file_record = file_payload["data"]["file"]  # type: ignore[index]
    assert set(chunk) == chunk_keys
    assert set(chunk["source_locator"]) == locator_keys
    assert set(section) == section_keys
    assert set(file_record) == file_keys
    assert all(item["id"] == item["logical_id"] for item in (chunk, section, file_record))
    assert isinstance(chunk["heading_path"], list)
    assert isinstance(section["heading_path"], str | type(None))
    assert isinstance(chunk["source_locator"]["heading_path"], list)
    assert isinstance(chunk["source_locator"]["start_line"], int)
    assert isinstance(chunk["source_locator"]["start_offset"], int | type(None))
    forbidden_internal_keys = {
        "file_id", "section_id", "parent_id", "previous_chunk_id", "next_chunk_id",
    }
    assert forbidden_internal_keys.isdisjoint(chunk)
    assert forbidden_internal_keys.isdisjoint(section)
    assert forbidden_internal_keys.isdisjoint(file_record)

    for command, message in (
        ("chunk", "Chunk not found"),
        ("section", "Section not found"),
        ("file", "File not found"),
    ):
        error_payload = _documented_read_example(command, "Error")
        assert error_payload == {
            "ok": False,
            "error": {"message": message, "code": "NOT_FOUND"},
            "meta": {"command": f"read {command}"},
        }


def test_read_commands_use_root_relative_store_from_ctx(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _write_config(root, ".custom-store")

    db_path = _setup_db_at_store(root, ".custom-store")
    conn = get_connection(db_path)
    try:
        _seed_data(conn)
    finally:
        conn.close()

    external_cwd = tmp_path / "outside"
    external_cwd.mkdir()
    monkeypatch.chdir(external_cwd)

    runner = CliRunner()
    result = runner.invoke(main, ["--root", str(root), "read", "chunk", "chunk1"])

    assert result.exit_code == 0, f"read chunk failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["chunk"]["id"] == "chunk1"
