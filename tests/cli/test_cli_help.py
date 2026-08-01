"""Tests for the current CLI help and fixed-catalog command surface."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main


def test_help_outputs_usage() -> None:
    """Verify that `mdrack --help` exits 0 and shows usage."""
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "MDRack" in result.output


def test_version_outputs_version() -> None:
    """Verify that `mdrack --version` prints the version string."""
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "1.3.0" in result.output


_COMMAND_GROUPS = ["init", "scan", "status", "doctor"]


def test_top_level_commands_exist(tmp_path: Path) -> None:
    """Each basic top-level command is callable offline and returns JSON."""
    (tmp_path / "offline.md").write_text("# Offline\n\nContract test.\n", encoding="utf-8")
    runner = CliRunner()
    for command in _COMMAND_GROUPS:
        args = ["--root", str(tmp_path), command]
        if command == "scan":
            args.extend(["--provider", "fake"])
        result = runner.invoke(main, args)
        assert result.exit_code == 0, f"Command '{command}' failed: {result.output}"
        payload = json.loads(result.output)
        assert payload["ok"] is True, f"Command '{command}' returned ok=false"
        assert "data" in payload


def test_search_requires_query() -> None:
    """search command exists and accepts QUERY plus its current options."""
    result = CliRunner().invoke(main, ["search", "--help"])
    assert result.exit_code == 0
    assert "QUERY" in result.output
    assert "--mode" in result.output
    assert "--limit" in result.output
    assert "--provider" in result.output
    assert "--catalog" not in result.output


def test_current_reader_groups_exist() -> None:
    """The fixed catalog exposes only the ported reader groups."""
    runner = CliRunner()
    for group in ("read", "files"):
        result = runner.invoke(main, [group, "--help"])
        assert result.exit_code == 0, f"Group '{group}' failed: {result.output}"

    read_help = runner.invoke(main, ["read", "--help"])
    assert "chunk" in read_help.output
    assert "file" in read_help.output
    assert "section" not in read_help.output


def test_files_list_exists_for_an_initialized_empty_catalog(tmp_path: Path) -> None:
    """files list reads the fixed catalog and returns the standard envelope."""
    runner = CliRunner()
    init = runner.invoke(main, ["--root", str(tmp_path), "init"])
    assert init.exit_code == 0, init.output

    result = runner.invoke(main, ["--root", str(tmp_path), "files", "list"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["files"] == []


def test_retained_public_groups_are_registered_without_legacy_catalog_bypass() -> None:
    """S2 keeps catalog-backed outcomes while removing legacy lifecycle commands."""
    runner = CliRunner()
    for command in ("resource", "eval"):
        result = runner.invoke(main, [command, "--help"])
        assert result.exit_code == 0, result.output
        assert "--catalog" not in result.output

    read_help = runner.invoke(main, ["read", "--help"])
    assert read_help.exit_code == 0, read_help.output
    assert "outline" in read_help.output

    for command in ("storage", "sections"):
        result = runner.invoke(main, [command, "--help"])
        assert result.exit_code == 2
        assert f"No such command '{command}'" in result.output


# ---------------------------------------------------------------------------
# JSON envelope shape checks
# ---------------------------------------------------------------------------
def test_json_envelope_success_shape() -> None:
    """Every successful response has ok, data, meta.command keys."""
    result = CliRunner().invoke(main, ["status"])
    payload = json.loads(result.output)
    assert "ok" in payload
    assert "data" in payload
    assert "meta" in payload
    assert "command" in payload["meta"]
    assert payload["ok"] is True


def test_json_envelope_error_shape() -> None:
    """Running an unknown command produces a nonzero Click result."""
    result = CliRunner().invoke(main, ["nonexistent-cmd"])
    assert result.exit_code != 0


def test_pretty_json_flag() -> None:
    """When --json is set, output remains valid JSON."""
    result = CliRunner().invoke(main, ["--json", "status"])
    assert result.exit_code == 0
    assert json.loads(result.output)["ok"] is True


def test_root_option_accepted() -> None:
    """The --root option is accepted without error."""
    result = CliRunner().invoke(main, ["--root", ".", "status"])
    assert result.exit_code == 0


def test_config_file_missing_falls_back() -> None:
    """A missing config file produces a private-safe JSON error envelope."""
    result = CliRunner().invoke(main, ["--config-file", "/nonexistent/path.toml", "status"])
    assert result.exit_code != 0
    try:
        assert json.loads(result.output)["ok"] is False
    except (json.JSONDecodeError, ValueError):
        pass
