"""Unit contracts for the standalone immutable artifact cache."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from mdrack.application.artifact_cache import ArtifactCache, ArtifactCacheKey


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _key(**overrides: str) -> ArtifactCacheKey:
    values = {
        "artifact_kind": "frame_caption",
        "source_fingerprint": _fingerprint("source"),
        "producer_fingerprint": _fingerprint("producer"),
        "model_fingerprint": _fingerprint("model"),
        "prompt_fingerprint": _fingerprint("prompt"),
        "config_fingerprint": _fingerprint("config"),
        "preprocessing_fingerprint": _fingerprint("preprocess"),
    }
    values.update(overrides)
    return ArtifactCacheKey(**values)


def test_key_is_complete_opaque_and_every_component_causes_drift() -> None:
    original = _key()
    fields = (
        "source_fingerprint",
        "producer_fingerprint",
        "model_fingerprint",
        "prompt_fingerprint",
        "config_fingerprint",
        "preprocessing_fingerprint",
    )

    assert len({replace(original, **{field: _fingerprint(field)}).digest for field in fields}) == len(fields)
    assert all(replace(original, **{field: _fingerprint(field)}).digest != original.digest for field in fields)
    with pytest.raises(ValueError, match="opaque sha256"):
        _key(source_fingerprint="PRIVATE_SOURCE_TEXT")


def test_cache_is_immutable_and_hit_does_not_rewrite_entry(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "artifacts")
    key = _key()

    assert cache.lookup(key).state == "miss"
    stored = cache.store(key, b"first-private-artifact")
    entry_path = cache.entry_path(key)
    metadata_mtime = (entry_path / "metadata.json").stat().st_mtime_ns
    existing = cache.store(key, b"different-private-artifact")

    assert stored.state == "stored"
    assert existing.state == "exists"
    assert cache.lookup(key).payload == b"first-private-artifact"
    assert (entry_path / "metadata.json").stat().st_mtime_ns == metadata_mtime
    assert entry_path.stat().st_mode & 0o077 == 0
    assert (entry_path / "payload.bin").stat().st_mode & 0o077 == 0


def test_corruption_is_discarded_and_can_be_rebuilt(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "artifacts")
    key = _key()
    cache.store(key, b"valid")
    (cache.entry_path(key) / "payload.bin").write_bytes(b"corrupt")

    verification = cache.verify()
    assert verification.valid_entries == 0
    assert verification.corrupt_entries == 1
    assert cache.entry_path(key).is_dir()

    lookup = cache.lookup(key)
    assert lookup.state == "corrupt"
    assert lookup.payload is None
    assert not cache.entry_path(key).exists()

    assert cache.store(key, b"rebuilt").state == "stored"
    assert cache.lookup(key).payload == b"rebuilt"
    assert cache.verify().ok is True


def test_partial_cache_treats_only_missing_identity_as_miss(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "artifacts")
    present = _key(source_fingerprint=_fingerprint("present"))
    missing = _key(source_fingerprint=_fingerprint("missing"))
    cache.store(present, b"present")

    assert cache.lookup(present).state == "hit"
    assert cache.lookup(missing).state == "miss"
    report = cache.status()
    assert report.entry_count == 1
    assert report.payload_bytes == len(b"present")


def test_lookup_does_not_follow_symlinked_shard(tmp_path: Path) -> None:
    key = _key()
    external = ArtifactCache(tmp_path / "external")
    external.store(key, b"outside-private-payload")
    guarded_root = tmp_path / "guarded"
    guarded_root.mkdir()
    (guarded_root / ".mdrack-artifact-cache-v1").write_text(
        "mdrack.artifact-cache.v1\n", encoding="ascii"
    )
    (guarded_root / key.digest[:2]).symlink_to(
        external.entry_path(key).parent, target_is_directory=True
    )
    lookup = ArtifactCache(guarded_root).lookup(key)

    assert lookup.state == "corrupt"
    assert lookup.payload is None
    assert external.lookup(key, recover_corrupt=False).payload == b"outside-private-payload"


def test_store_does_not_follow_symlinked_shard(tmp_path: Path) -> None:
    key = _key()
    external_shard = tmp_path / "external-shard"
    external_shard.mkdir()
    guarded_root = tmp_path / "guarded"
    guarded_root.mkdir()
    (guarded_root / ".mdrack-artifact-cache-v1").write_text(
        "mdrack.artifact-cache.v1\n", encoding="ascii"
    )
    (guarded_root / key.digest[:2]).symlink_to(external_shard, target_is_directory=True)

    with pytest.raises(ValueError, match="shard"):
        ArtifactCache(guarded_root).store(key, b"must-not-escape")
    assert list(external_shard.iterdir()) == []


def test_corrupt_recovery_does_not_remove_through_symlinked_shard(tmp_path: Path) -> None:
    key = _key()
    external = ArtifactCache(tmp_path / "external")
    external.store(key, b"valid")
    (external.entry_path(key) / "payload.bin").write_bytes(b"corrupt")
    guarded_root = tmp_path / "guarded"
    guarded_root.mkdir()
    (guarded_root / ".mdrack-artifact-cache-v1").write_text(
        "mdrack.artifact-cache.v1\n", encoding="ascii"
    )
    (guarded_root / key.digest[:2]).symlink_to(
        external.entry_path(key).parent, target_is_directory=True
    )

    result = ArtifactCache(guarded_root).lookup(key)

    assert result.state == "corrupt"
    assert external.entry_path(key).is_dir()
    assert (external.entry_path(key) / "payload.bin").read_bytes() == b"corrupt"
