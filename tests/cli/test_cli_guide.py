"""Tests for the static, pre-configuration CLI guide."""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.cli.help_topics import render_topic

TOPICS = ("quickstart", "configuration", "search", "media")


def test_guide_index_lists_fixed_topics_without_loading_config(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "guide"],
    )

    assert result.exit_code == 0, result.output
    assert "Available guide topics" in result.output
    for topic in TOPICS:
        assert topic in result.output
    assert not (tmp_path / ".mdrack").exists()


def test_every_guide_topic_is_human_help(tmp_path: Path) -> None:
    runner = CliRunner()
    for topic in TOPICS:
        result = runner.invoke(main, ["--root", str(tmp_path), "guide", topic])
        assert result.exit_code == 0, f"{topic}: {result.output}"
        assert result.output.strip()
        assert not result.output.lstrip().startswith("{")


def test_guide_works_with_missing_or_malformed_config(tmp_path: Path) -> None:
    config_path = tmp_path / "broken.toml"
    config_path.write_text("[embedding\n", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "--config-file", str(config_path), "guide", "quickstart"],
    )

    assert result.exit_code == 0, result.output
    assert "fake" in result.output
    assert not (tmp_path / ".mdrack").exists()


def test_guide_recipe_commands_are_registered_and_current() -> None:
    runner = CliRunner()
    index = runner.invoke(main, ["guide"])
    assert index.exit_code == 0
    assert "sections" not in index.output
    assert "--catalog" not in index.output

    for command in ("scan", "search", "status", "init", "ingest", "image"):
        result = runner.invoke(main, [command, "--help"])
        assert result.exit_code == 0, f"{command}: {result.output}"


def test_guide_validates_complete_nested_click_paths() -> None:
    commands = dict(main.commands)
    ingest = commands["ingest"]
    assert isinstance(ingest, click.Group)
    commands["ingest"] = click.Group(
        name="ingest",
        commands={name: command for name, command in ingest.commands.items() if name != "video"},
    )

    with pytest.raises(RuntimeError, match="command path"):
        render_topic("media", commands)


def test_guide_topics_do_not_claim_raw_media_extraction() -> None:
    runner = CliRunner()
    media = runner.invoke(main, ["guide", "media"])
    assert media.exit_code == 0
    assert "does not transcribe raw audio" in media.output
    assert "pixel/visual or acoustic search" in media.output
