"""CLI text-search contracts over the canonical catalog lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main


def _index_fixture(root: Path) -> None:
    (root / "docs").mkdir()
    (root / "docs" / "python.md").write_text(
        "# Python Programming\n\nPython is a high-level programming language.\n",
        encoding="utf-8",
    )
    (root / "docs" / "javascript.md").write_text(
        "# JavaScript\n\nJavaScript is a scripting language for the web.\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(main, ["--root", str(root), "scan", "--provider", "fake"])
    assert result.exit_code == 0, result.output
    assert (root / ".mdrack" / "catalog.sqlite3").is_file()


def _search(root: Path, query: str) -> dict[str, object]:
    result = CliRunner().invoke(main, ["--root", str(root), "search", query, "--mode", "text"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_text_search_returns_valid_json(tmp_path: Path) -> None:
    _index_fixture(tmp_path)

    payload = _search(tmp_path, "Python")

    assert payload["ok"] is True
    data = payload["data"]
    assert isinstance(data, dict)
    assert data["mode"] == "text"
    assert data["total_count"] == 1
    assert data["results"]


def test_text_search_with_no_results(tmp_path: Path) -> None:
    _index_fixture(tmp_path)

    payload = _search(tmp_path, "NonexistentTermXYZ")

    assert payload["ok"] is True
    data = payload["data"]
    assert isinstance(data, dict)
    assert data["results"] == []
    assert data["total_count"] == 0


def test_text_search_output_format_preserves_portable_provenance(tmp_path: Path) -> None:
    _index_fixture(tmp_path)

    payload = _search(tmp_path, "Python")

    data = payload["data"]
    assert isinstance(data, dict)
    assert set(data) == {
        "query",
        "mode",
        "results",
        "total_count",
        "degraded",
        "degraded_reason",
    }
    assert data["query"] == "Python"
    results = data["results"]
    assert isinstance(results, list) and len(results) == 1
    item = results[0]
    assert isinstance(item, dict)
    assert item["logical_id"] == item["chunk_id"]
    assert item["file"] == "docs/python.md"
    assert item["snippet"] == item["content_preview"]
    locator = item["source_locator"]
    assert isinstance(locator, dict)
    assert locator["relative_path"] == "docs/python.md"
    assert "sqlite_id" not in item


def test_text_search_envelope_shape(tmp_path: Path) -> None:
    _index_fixture(tmp_path)

    payload = _search(tmp_path, "Python")

    assert set(payload) == {"ok", "data", "meta"}
    assert payload["ok"] is True
    meta = payload["meta"]
    assert isinstance(meta, dict)
    assert meta == {"command": "search"}


def test_search_internal_error_does_not_expose_query_or_endpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _index_fixture(tmp_path)
    private_query = "customer payroll secret"
    private_endpoint = "https://private.example.invalid/token"

    def fail_search(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError(f"query={private_query} endpoint={private_endpoint}")

    monkeypatch.setattr("mdrack.cli.commands.search._run_text_search", fail_search)
    result = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "search", private_query, "--mode", "text"],
    )

    assert private_query not in result.output
    assert private_endpoint not in result.output
    payload = json.loads(result.output)
    assert payload["error"] == {"message": "Search failed", "code": "INTERNAL_ERROR"}


def test_text_search_no_catalog(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "search", "Python", "--mode", "text"],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "not found" in payload["error"]["message"].lower()
