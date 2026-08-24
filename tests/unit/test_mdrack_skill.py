"""Validation for the repository-shipped MDRack agent skill."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from mdrack.cli import main

SKILL_PATH = Path(__file__).parents[2] / "skills" / "mdrack" / "SKILL.md"
RECIPE = re.compile(r"^(?:uv run )?mdrack(?: .*)?$")
FORBIDDEN = ("--catalog", "catalog.sqlite3", "sqlite3", "mcp", "ocr")


def _recipe_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if RECIPE.fullmatch(line.strip())]


def _registered_path(line: str) -> tuple[str, ...]:
    tokens = shlex.split(line)
    if tokens[:3] == ["uv", "run", "mdrack"]:
        tokens = tokens[3:]
    elif tokens and tokens[0] == "mdrack":
        tokens = tokens[1:]
    commands: dict[str, Any] = dict(main.commands)
    path: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not path and token in {"--root", "--config-file"}:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        command = commands.get(token)
        if command is None:
            break
        path.append(token)
        commands = getattr(command, "commands", {})
        if not commands:
            break
        index += 1
    return tuple(path) or (("<root>",) if tokens else ())


def test_skill_has_frontmatter_triggers_and_no_forbidden_surface() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    frontmatter, body = text[4:].split("\n---\n", 1)
    assert re.search(r"^name:\s+mdrack$", frontmatter, re.MULTILINE)
    assert re.search(r"^description:\s+", frontmatter, re.MULTILINE)
    assert "Triggers:" in frontmatter
    lowered = body.lower()
    for term in FORBIDDEN:
        assert term not in lowered
    assert "skills/mdrack/" not in body
    assert len(text.splitlines()) <= 300


def test_every_literal_mdrack_recipe_resolves_through_click() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")
    paths = [_registered_path(line) for line in _recipe_lines(text)]
    assert paths
    assert all(paths), paths

    runner = CliRunner()
    for path in sorted(set(paths)):
        args = [] if path == ("<root>",) else [*path, "--help"]
        result = runner.invoke(main, args)
        assert result.exit_code == 0, f"{' '.join(path)}: {result.output}"


def test_skill_documents_the_authorized_raw_media_boundaries() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "ingest audio SOURCE_PATH" in text
    assert "--allow-external-stt" in text
    assert "ingest raw-video SOURCE_PATH" in text
    assert "--allow-external-video-extractor" in text


def test_skill_pins_the_verified_uv_checkout_route() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "uv 0.11.15" in text
    assert "uv --version" in text
    assert "uv sync --all-extras --frozen" in text
    assert "do not refresh `uv.lock`" in text


def test_skill_documents_the_external_voiceover_stt_boundary() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "Voiceover Pipeline" in text
    assert "voiceover list asr-providers --json" in text
    assert (
        "voiceover doctor --with-asr --asr-provider nemotron-local "
        "--asr-device cpu --asr-compute auto --json"
    ) in text
    assert "nvidia/nemotron-3.5-asr-streaming-0.6b" in text
    assert "mdrack.timed-transcript.v1" in text
    assert "shell-free stdin wrapper" in text
    assert "local/offline" in text
    assert "Qwen" in text
