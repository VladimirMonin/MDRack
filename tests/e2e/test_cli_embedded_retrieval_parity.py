"""CLI and embedded retrieval parity over one fixed application catalog."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.config.models import MDRackConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.public_api import MDRackEngine


def _seed_fixed_catalog(runner: CliRunner, root: Path) -> None:
    (root / "parity.md").write_text("# Parity\n\nPython retrieval parity.", encoding="utf-8")
    initialized = runner.invoke(main, ["--root", str(root), "init"])
    scanned = runner.invoke(main, ["--root", str(root), "scan", "--provider", "fake"])
    assert initialized.exit_code == 0, initialized.output
    assert scanned.exit_code == 0, scanned.output


@pytest.mark.parametrize("mode", ["text", "semantic", "hybrid"])
def test_cli_and_embedded_results_are_byte_for_byte_equivalent(tmp_path: Path, mode: str) -> None:
    runner = CliRunner()
    _seed_fixed_catalog(runner, tmp_path)
    engine = MDRackEngine(
        root=tmp_path,
        config=MDRackConfig(),
        embedding_provider=FakeEmbeddingProvider(dimensions=1024),
    )

    if mode == "text":
        embedded = engine.search_text("Python", limit=10).to_dict()
    elif mode == "semantic":
        embedded = asyncio.run(engine.search_semantic("Python", limit=10)).to_dict()
    else:
        embedded = asyncio.run(engine.search_hybrid("Python", limit=10, reranker=None)).to_dict()
    cli = runner.invoke(
        main,
        ["--root", str(tmp_path), "search", "Python", "--mode", mode, "--provider", "fake", "--limit", "10"],
    )
    engine.close()

    assert cli.exit_code == 0, cli.output
    payload = json.loads(cli.output)
    assert payload["data"] == embedded
    assert embedded["results"][0]["heading_path"] == ["Parity"]
    assert embedded["results"][0]["heading_path"] == embedded["results"][0]["source_locator"]["heading_path"]
    assert (tmp_path / ".mdrack" / "catalog.sqlite3").is_file()
