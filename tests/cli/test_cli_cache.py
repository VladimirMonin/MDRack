"""CLI contracts for privacy-safe artifact cache diagnostics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.application.artifact_cache import ArtifactCache, ArtifactCacheKey
from mdrack.cli import main


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _key() -> ArtifactCacheKey:
    return ArtifactCacheKey(
        artifact_kind="transcript",
        source_fingerprint=_fingerprint("PRIVATE_SOURCE"),
        producer_fingerprint=_fingerprint("PRIVATE_PRODUCER"),
        model_fingerprint=_fingerprint("PRIVATE_MODEL"),
        prompt_fingerprint=_fingerprint("PRIVATE_PROMPT"),
        config_fingerprint=_fingerprint("PRIVATE_CONFIG"),
        preprocessing_fingerprint=_fingerprint("PRIVATE_PREPROCESS"),
    )


def _write_config(root: Path, cache_path: Path) -> Path:
    config = root / "config.toml"
    config.write_text(f'[cache]\ndirectory = "{cache_path}"\n', encoding="utf-8")
    return config


def _write_relative_config(root: Path, cache_directory: str, *, store: str = ".mdrack") -> Path:
    config = root / "config.toml"
    config.write_text(
        f'[paths]\nstore = "{store}"\n[cache]\ndirectory = "{cache_directory}"\n',
        encoding="utf-8",
    )
    return config


def test_cache_status_and_verify_expose_counts_not_private_values(tmp_path: Path) -> None:
    cache_path = tmp_path / "PRIVATE_CACHE_DIRECTORY"
    config = _write_config(tmp_path, cache_path)
    cache = ArtifactCache(cache_path)
    cache.store(_key(), b"PRIVATE_PAYLOAD")

    runner = CliRunner()
    status = runner.invoke(main, ["--root", str(tmp_path), "--config-file", str(config), "cache", "status"])
    verify = runner.invoke(main, ["--root", str(tmp_path), "--config-file", str(config), "cache", "verify"])

    assert status.exit_code == 0, status.output
    assert verify.exit_code == 0, verify.output
    status_data = json.loads(status.stdout)["data"]
    verify_data = json.loads(verify.stdout)["data"]
    assert status_data == {
        "enabled": True,
        "entry_count": 1,
        "payload_bytes": len(b"PRIVATE_PAYLOAD"),
    }
    assert verify_data == {
        "ok": True,
        "entry_count": 1,
        "valid_entries": 1,
        "corrupt_entries": 0,
        "payload_bytes": len(b"PRIVATE_PAYLOAD"),
    }
    captured = status.stdout + status.stderr + verify.stdout + verify.stderr
    for private in (str(cache_path), "PRIVATE_CACHE_DIRECTORY", "PRIVATE_PAYLOAD", "PRIVATE_SOURCE"):
        assert private not in captured


def test_cache_verify_reports_corruption_without_deleting_it(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache"
    config = _write_config(tmp_path, cache_path)
    cache = ArtifactCache(cache_path)
    key = _key()
    cache.store(key, b"valid")
    (cache.entry_path(key) / "payload.bin").write_bytes(b"invalid")

    result = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "--config-file", str(config), "cache", "verify"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["data"] == {
        "ok": False,
        "entry_count": 1,
        "valid_entries": 0,
        "corrupt_entries": 1,
        "payload_bytes": 0,
    }
    assert cache.entry_path(key).is_dir()


def test_cache_purge_requires_confirmation_and_does_not_mutate(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache"
    config = _write_config(tmp_path, cache_path)
    cache = ArtifactCache(cache_path)
    key = _key()
    cache.store(key, b"must-remain")

    result = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "--config-file", str(config), "cache", "purge"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == {
        "message": "Cache purge requires --confirm",
        "code": "CACHE_CONFIRMATION_REQUIRED",
    }
    assert cache.lookup(key).payload == b"must-remain"


@pytest.mark.parametrize("cache_directory", [".", ".mdrack"])
def test_cache_purge_rejects_project_or_store_root(tmp_path: Path, cache_directory: str) -> None:
    store = tmp_path / ".mdrack"
    store.mkdir()
    database = store / "knowledge.db"
    database.write_bytes(b"PRIVATE CATALOG")
    protected = tmp_path / "source.md"
    protected.write_bytes(b"PRIVATE SOURCE")
    config = _write_relative_config(tmp_path, cache_directory)

    result = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "--config-file", str(config), "cache", "purge", "--confirm"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "CACHE_PURGE_ERROR"
    assert database.read_bytes() == b"PRIVATE CATALOG"
    assert protected.read_bytes() == b"PRIVATE SOURCE"


def test_cache_purge_rejects_missing_forged_marker_and_foreign_content(tmp_path: Path) -> None:
    runner = CliRunner()
    for name, marker_payload, foreign in (
        ("missing", None, False),
        ("forged", b"not-the-cache-schema\n", False),
        ("foreign", b"mdrack.artifact-cache.v1\n", True),
    ):
        cache_path = tmp_path / name
        cache_path.mkdir()
        if marker_payload is not None:
            (cache_path / ".mdrack-artifact-cache-v1").write_bytes(marker_payload)
        sentinel = cache_path / "knowledge.db"
        if foreign:
            sentinel.write_bytes(b"PRIVATE CATALOG")
        config = _write_config(tmp_path, cache_path)

        result = runner.invoke(
            main,
            ["--root", str(tmp_path), "--config-file", str(config), "cache", "purge", "--confirm"],
        )

        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "CACHE_PURGE_ERROR"
        if foreign:
            assert sentinel.read_bytes() == b"PRIVATE CATALOG"


def test_cache_purge_removes_only_owned_entries_and_keeps_owned_root(tmp_path: Path) -> None:
    cache_path = tmp_path / "dedicated-cache"
    config = _write_config(tmp_path, cache_path)
    cache = ArtifactCache(cache_path)
    cache.store(_key(), b"private-payload")

    result = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "--config-file", str(config), "cache", "purge", "--confirm"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"] == {"purged": True, "removed_entries": 1}
    assert cache_path.is_dir()
    assert (cache_path / ".mdrack-artifact-cache-v1").read_text(encoding="ascii") == (
        "mdrack.artifact-cache.v1\n"
    )
    assert cache.status().entry_count == 0
