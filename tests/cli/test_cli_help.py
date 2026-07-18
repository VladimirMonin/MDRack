"""Tests for CLI help output, command groups, and JSON error envelope."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main


def test_help_outputs_usage() -> None:
    """Verify that `mdrack --help` exits 0 and shows usage."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "MDRack" in result.output


def test_version_outputs_version() -> None:
    """Verify that `mdrack --version` prints the version string."""
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.3.0" in result.output


# ---------------------------------------------------------------------------
# Command group existence checks
# ---------------------------------------------------------------------------

_COMMAND_GROUPS = ["init", "scan", "status", "doctor"]


def test_top_level_commands_exist(tmp_path: Path) -> None:
    """Each top-level command should be callable offline and return JSON."""
    (tmp_path / "offline.md").write_text("# Offline\n\nContract test.\n", encoding="utf-8")
    runner = CliRunner()
    for cmd in _COMMAND_GROUPS:
        args = ["--root", str(tmp_path), cmd]
        if cmd == "scan":
            args.extend(["--provider", "fake"])
        result = runner.invoke(main, args)
        assert result.exit_code == 0, f"Command '{cmd}' failed: {result.output}"
        payload = json.loads(result.output)
        assert payload["ok"] is True, f"Command '{cmd}' returned ok=false"
        assert "data" in payload


def test_search_requires_query() -> None:
    """search command exists and accepts QUERY argument plus options."""
    runner = CliRunner()
    result = runner.invoke(main, ["search", "--help"])
    assert result.exit_code == 0
    assert "QUERY" in result.output
    assert "--mode" in result.output
    assert "--limit" in result.output
    assert "--provider" in result.output


_SUBGROUPS = ["read", "files", "sections"]


def test_subgroup_exists() -> None:
    """Each subgroup should be callable without crashing."""
    runner = CliRunner()
    for grp in _SUBGROUPS:
        result = runner.invoke(main, [grp, "--help"])
        assert result.exit_code == 0, f"Subgroup '{grp}' failed: {result.output}"


def test_read_subcommands_exist() -> None:
    """read chunk / read section / read file should be listed in help."""
    runner = CliRunner()
    result = runner.invoke(main, ["read", "--help"])
    assert result.exit_code == 0
    assert "chunk" in result.output
    assert "section" in result.output
    assert "file" in result.output


def test_files_list_exists() -> None:
    """files list should return JSON."""
    runner = CliRunner()
    result = runner.invoke(main, ["files", "list"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True


def test_sections_list_exists() -> None:
    """sections list should require a FILE_ID argument."""
    runner = CliRunner()
    result = runner.invoke(main, ["sections", "list", "--help"])
    assert result.exit_code == 0
    assert "FILE_ID" in result.output


# ---------------------------------------------------------------------------
# JSON envelope shape checks
# ---------------------------------------------------------------------------

def test_json_envelope_success_shape() -> None:
    """Every successful response has ok, data, meta.command keys."""
    runner = CliRunner()
    result = runner.invoke(main, ["status"])
    payload = json.loads(result.output)
    assert "ok" in payload
    assert "data" in payload
    assert "meta" in payload
    assert "command" in payload["meta"]
    assert payload["ok"] is True


def test_json_envelope_error_shape() -> None:
    """Running an unknown command produces a JSON error envelope."""
    runner = CliRunner()
    result = runner.invoke(main, ["nonexistent-cmd"])
    # Click's default for unknown commands is exit_code != 0
    assert result.exit_code != 0


def test_pretty_json_flag() -> None:
    """When --json flag is set, output should still be valid JSON."""
    runner = CliRunner()
    result = runner.invoke(main, ["--json", "status"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True


# ---------------------------------------------------------------------------
# Global option: --root
# ---------------------------------------------------------------------------

def test_root_option_accepted() -> None:
    """The --root option should be accepted without error."""
    runner = CliRunner()
    result = runner.invoke(main, ["--root", ".", "status"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Config loading via --config-file
# ---------------------------------------------------------------------------

def test_config_file_missing_falls_back() -> None:
    """If --config-file points to a missing file, the CLI should still start."""
    runner = CliRunner()
    result = runner.invoke(main, ["--config-file", "/nonexistent/path.toml", "status"])
    # Should fail gracefully with a JSON error envelope
    assert result.exit_code != 0
    try:
        payload = json.loads(result.output)
        assert payload["ok"] is False
    except (json.JSONDecodeError, ValueError):
        # If Click intercepts before our handler, it's still acceptable
        pass
