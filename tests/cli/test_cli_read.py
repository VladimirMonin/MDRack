"""Fixed-catalog tests for the ``mdrack read`` command group."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from mdrack.cli import main
from mdrack.config.models import MDRackConfig
from mdrack.public_api import MDRackEngine


def _invoke(runner: CliRunner, root: Path, *command: str) -> dict[str, Any]:
    result = runner.invoke(main, ["--root", str(root), *command])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    return payload["data"]


def _indexed_root(tmp_path: Path) -> tuple[Path, CliRunner, str, str]:
    root = tmp_path / "project"
    root.mkdir()
    (root / "reader.md").write_text(
        "# Reader contract\n\n"
        "First unique reader passage.\n\n"
        "## Middle\n\n"
        "Second unique reader passage.\n\n"
        "## End\n\n"
        "Third unique reader passage.\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    _invoke(runner, root, "init")
    _invoke(runner, root, "scan", "--provider", "fake")

    listed = _invoke(runner, root, "files", "list")
    records = listed["files"]
    assert isinstance(records, list) and len(records) == 1
    file_id = str(records[0]["logical_id"])
    search = _invoke(runner, root, "search", "Second unique reader passage", "--mode", "text", "--provider", "fake")
    results = search["results"]
    assert isinstance(results, list) and results
    return root, runner, file_id, str(results[0]["logical_id"])


def test_read_chunk_returns_a_public_core_projection(tmp_path: Path) -> None:
    root, runner, _, chunk_id = _indexed_root(tmp_path)

    data = _invoke(runner, root, "read", "chunk", chunk_id)
    chunk = data["chunk"]
    assert chunk["id"] == chunk["logical_id"] == chunk_id
    assert "Second unique reader passage" in chunk["content"]
    assert isinstance(chunk["heading_path"], list)
    assert "source_locator" in chunk


def test_read_chunk_neighbors_are_derived_from_the_same_representation(tmp_path: Path) -> None:
    root, runner, _, chunk_id = _indexed_root(tmp_path)

    data = _invoke(runner, root, "read", "chunk", chunk_id, "--context", "neighbors")
    neighbors = data["neighbors"]
    assert isinstance(neighbors, list)
    assert all(neighbor["logical_id"] != chunk_id for neighbor in neighbors)
    assert all(neighbor["source_locator"]["relative_path"] == "reader.md" for neighbor in neighbors)


def test_read_file_returns_the_same_public_document_projection(tmp_path: Path) -> None:
    root, runner, file_id, _ = _indexed_root(tmp_path)

    data = _invoke(runner, root, "read", "file", file_id)
    record = data["file"]
    assert record["id"] == record["logical_id"] == file_id
    assert record["relative_path"] == "reader.md"
    assert "sections" not in data


def test_read_outline_uses_file_and_heading_logical_ids(tmp_path: Path) -> None:
    root, runner, file_id, _ = _indexed_root(tmp_path)

    data = _invoke(runner, root, "read", "outline", file_id)

    assert data["file_logical_id"] == file_id
    headings = data["headings"]
    assert [heading["heading_path"] for heading in headings] == [
        ["Reader contract"],
        ["Reader contract", "Middle"],
        ["Reader contract", "End"],
    ]
    assert all(heading["logical_id"].startswith("heading_") for heading in headings)
    assert all("section_id" not in heading for heading in headings)



def test_engine_outline_matches_the_cli_projection(tmp_path: Path) -> None:
    root, runner, file_id, _ = _indexed_root(tmp_path)

    cli_outline = _invoke(runner, root, "read", "outline", file_id)
    engine = MDRackEngine(root=root, config=MDRackConfig())
    try:
        assert engine.get_file_outline(file_id) == cli_outline
    finally:
        engine.close()


def test_read_not_found_is_private_safe_for_supported_readers(tmp_path: Path) -> None:
    root, runner, _, _ = _indexed_root(tmp_path)
    sentinel = "PRIVATE_READ_ID_SENTINEL_/home/v/secret-note.md"

    for command, message in (
        ("chunk", "Chunk not found"),
        ("file", "File not found"),
        ("outline", "File not found"),
    ):
        result = runner.invoke(main, ["--root", str(root), "read", command, sentinel])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["error"] == {"message": message, "code": "NOT_FOUND"}
        assert sentinel not in result.output


def test_read_section_is_withdrawn_in_the_fixed_catalog_surface(tmp_path: Path) -> None:
    root, runner, _, _ = _indexed_root(tmp_path)

    result = runner.invoke(main, ["--root", str(root), "read", "section", "any-id"])

    assert result.exit_code == 2
    assert "No such command 'section'" in result.output


def test_read_contract_documents_only_supported_reader_shapes() -> None:
    contract = (Path(__file__).resolve().parents[2] / "docs" / "cli-contracts.md").read_text(encoding="utf-8")
    assert "`read chunk <logical-id>`" in contract
    assert "`files info` and `read file`" in contract
    assert "`read outline <file-logical-id>`" in contract
    assert "`read section`" in contract
    assert "withdrawn" in contract.lower()
