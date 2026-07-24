"""CLI contract coverage for explicit fresh compact candidate commands."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main


def test_storage_rebuild_fresh_and_verify_emit_stable_json(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text(
        "# Compact storage\n\nSource-only candidate rebuild.\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    rebuild = runner.invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "storage",
            "rebuild-fresh",
            "--provider",
            "fake",
            "--candidate-name",
            "fresh-cli",
        ],
    )

    assert rebuild.exit_code == 0, rebuild.output
    payload = json.loads(rebuild.output)
    assert payload["ok"] is True
    assert payload["meta"]["command"] == "storage rebuild-fresh"
    assert payload["data"] == {
        "generation_id": "fresh-cli",
        "state": "ready",
        "source_count": 1,
        "vector_codec": "float32",
        "vector_backend": "builtin",
    }

    verify = runner.invoke(main, ["--root", str(tmp_path), "storage", "verify", "fresh-cli"])
    assert verify.exit_code == 0, verify.output
    verified = json.loads(verify.output)
    assert verified["ok"] is True
    assert verified["meta"]["command"] == "storage verify"
    assert verified["data"]["generation_id"] == "fresh-cli"
    assert verified["data"]["state"] == "ready"
    assert verified["data"]["counts"]["resources"] == 1
