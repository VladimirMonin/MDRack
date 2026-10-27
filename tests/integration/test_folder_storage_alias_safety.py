"""P05 F01 regression: derived store/cache must be physically outside selected sources.

Uses real Windows junctions where available and actual engine/registered-CLI
surfaces. Synthetic providers only; no model, network or native construction.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.application.folder_import import FolderImportError
from mdrack.cli import main
from mdrack.config.models import CacheConfig, MDRackConfig, PathsConfig
from mdrack.embeddings.synthetic import SyntheticPreparedProvider
from mdrack.public_api import MDRackEngine


class CountingProvider(SyntheticPreparedProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def encode(self, value: object, role: object) -> object:
        self.calls += 1
        return await super().encode(value, role)  # type: ignore[arg-type]


def _tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _make_junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction behavior is required")
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        timeout=15,
    )
    if completed.returncode != 0:
        pytest.skip("junction creation unavailable")
    assert link.is_junction()
    assert link.resolve() == target.resolve()


def _owner_config(owner: str, alias: bool, selected: Path, app: Path) -> tuple[MDRackConfig, str, str]:
    if alias:
        _make_junction(app / "source_alias", selected)
        if owner == "store":
            store_value, cache_value = "source_alias/.mdrack", "cache"
        else:
            store_value, cache_value = "store", "source_alias/cache"
    elif owner == "store":
        store_value, cache_value = (selected / ".mdrack").as_posix(), "cache"
    else:
        store_value, cache_value = "store", (selected / "cache").as_posix()
    return (
        MDRackConfig(
            paths=PathsConfig(store=store_value),
            cache=CacheConfig(directory=cache_value),
        ),
        store_value,
        cache_value,
    )


def _write_cli_config(app: Path, store_value: str, cache_value: str) -> Path:
    config_file = app / "config.toml"
    config_file.write_text(
        f'[paths]\nstore = "{store_value}"\n\n[cache]\ndirectory = "{cache_value}"\n',
        encoding="utf-8",
    )
    return config_file


def _assert_no_selected_derived(selected: Path, before: dict[str, str]) -> None:
    assert _tree(selected) == before
    assert not (selected / ".mdrack").exists()
    assert not (selected / "cache").exists()


@pytest.mark.parametrize("owner", ["store", "cache"])
@pytest.mark.parametrize("alias", [False, True], ids=["direct", "junction_alias"])
@pytest.mark.parametrize("surface", ["engine", "cli"])
def test_derived_owner_refused_before_write_and_encode(
    tmp_path: Path,
    owner: str,
    alias: bool,
    surface: str,
) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "notes.md").write_text("# Notes\n\nOWNER_SAFETY_TOKEN.\n", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    before = _tree(selected)
    config, store_value, cache_value = _owner_config(owner, alias, selected, app)
    reason = "source_overlaps_store" if owner == "store" else "source_overlaps_cache"

    if surface == "engine":
        provider = CountingProvider()
        with MDRackEngine(root=app, config=config, prepared_embedding_provider=provider) as engine:
            with pytest.raises(FolderImportError, match=reason):
                asyncio.run(engine.import_folders([("fixture", selected)]))
        assert provider.calls == 0
    else:
        config_file = _write_cli_config(app, store_value, cache_value)
        calls: list[object] = []

        async def forbidden_encode(self: object, value: object, role: object) -> object:
            del self
            calls.append((value, role))
            raise AssertionError("encode dispatched before admission")

        import mdrack.embeddings.synthetic as synthetic_module

        original_encode = synthetic_module.SyntheticPreparedProvider.encode
        synthetic_module.SyntheticPreparedProvider.encode = forbidden_encode  # type: ignore[method-assign]
        try:
            result = CliRunner().invoke(main, [
                "--root", str(app), "--config-file", str(config_file),
                "folder", "add", f"fixture={selected}", "--provider", "synthetic",
            ])
        finally:
            synthetic_module.SyntheticPreparedProvider.encode = original_encode  # type: ignore[method-assign]
        payload = json.loads(result.stdout)
        assert result.exit_code == 1, result.output
        assert payload["ok"] is False
        assert payload["error"]["details"]["reason_code"] == reason
        assert calls == []

    _assert_no_selected_derived(selected, before)


@pytest.mark.parametrize("surface", ["engine", "cli"])
def test_disjoint_store_and_cache_remain_allowed(tmp_path: Path, surface: str) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "notes.md").write_text("# Notes\n\nDISJOINT_TOKEN.\n", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    before = _tree(selected)
    config = MDRackConfig(
        paths=PathsConfig(store="store"),
        cache=CacheConfig(directory="cache"),
    )

    if surface == "engine":
        provider = CountingProvider()
        with MDRackEngine(root=app, config=config, prepared_embedding_provider=provider) as engine:
            result = asyncio.run(engine.import_folders([("fixture", selected)]))
        assert result.status == "success"
        assert result.counts["added"] == 1
        assert provider.calls == 1
    else:
        config_file = _write_cli_config(app, "store", "cache")
        result = CliRunner().invoke(main, [
            "--root", str(app), "--config-file", str(config_file),
            "folder", "add", f"fixture={selected}", "--provider", "synthetic",
        ])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["data"]["counts"]["added"] == 1

    assert _tree(selected) == before
    assert not (selected / ".mdrack").exists()
    assert not (selected / "cache").exists()
    assert (app / "store" / "catalog.sqlite3").is_file()
