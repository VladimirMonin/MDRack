"""Real synthetic-filesystem lifecycle and containment, not provider fakes."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mdrack.application import _artifact_cache_fs as fs
from mdrack.application.artifact_cache import ArtifactCache, ArtifactCacheKey


def key() -> ArtifactCacheKey:
    fingerprint = "sha256:" + hashlib.sha256(b"synthetic").hexdigest()
    return ArtifactCacheKey("transcript", *([fingerprint] * 6))


def test_binary_lifecycle(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    cache = ArtifactCache(root, max_entry_bytes=8)
    identity = key()
    payload = b"\r\n\x1a\0\xffabc"
    assert len(payload) == 8
    assert cache.lookup(identity).state == "miss"
    assert cache.store(identity, payload).state == "stored"
    reopened = ArtifactCache(root, max_entry_bytes=8)
    entry = reopened.entry_path(identity)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in entry.iterdir()}
    assert reopened.lookup(identity).payload == payload
    assert reopened.store(identity, b"different"[:8]).state == "exists"
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in entry.iterdir()} == before
    assert reopened.status().payload_bytes == 8
    assert reopened.verify().valid_entries == 1
    reopened.discard(identity)
    assert reopened.lookup(identity).state == "miss"
    assert reopened.store(identity, payload).state == "stored"
    assert reopened.purge(confirm=True) == 1
    assert [p.name for p in root.iterdir()] == [".mdrack-artifact-cache-v1"]


def link_directory(link: Path, target: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True, timeout=15)
        assert result.returncode == 0
    else:
        link.symlink_to(target, target_is_directory=True)


def unlink_directory(link: Path) -> None:
    if os.name == "nt":
        link.rmdir()
    else:
        link.unlink()


@pytest.mark.parametrize("operation", ["lookup", "store", "status", "verify", "discard", "purge"])
def test_real_shard_reparse_refusal(tmp_path: Path, operation: str) -> None:
    external = ArtifactCache(tmp_path / "outside")
    external.store(key(), b"OUTSIDE\r\n\x1a\0")
    cache = ArtifactCache(tmp_path / "cache")
    cache.store(key(), b"inside")
    shard = cache.entry_path(key()).parent
    shutil.rmtree(shard)
    link_directory(shard, external.entry_path(key()).parent)
    try:
        if operation == "lookup":
            assert cache.lookup(key()).state == "corrupt"
        elif operation == "store":
            with pytest.raises((ValueError, OSError)):
                cache.store(key(), b"must-not-escape")
        else:
            with pytest.raises((ValueError, OSError)):
                getattr(cache, operation)(key()) if operation == "discard" else getattr(cache, operation)(
                    **({"confirm": True} if operation == "purge" else {}))
        assert external.lookup(key(), recover_corrupt=False).payload == b"OUTSIDE\r\n\x1a\0"
    finally:
        unlink_directory(shard)


@pytest.mark.parametrize("boundary", ["root", "entry"])
def test_root_and_entry_junctions(tmp_path: Path, boundary: str) -> None:
    external = ArtifactCache(tmp_path / "outside")
    external.store(key(), b"OUTSIDE")
    cache = ArtifactCache(tmp_path / "cache")
    cache.store(key(), b"inside")
    link = cache.root if boundary == "root" else cache.entry_path(key())
    shutil.rmtree(link)
    link_directory(link, external.root if boundary == "root" else external.entry_path(key()))
    try:
        if boundary == "root":
            with pytest.raises((ValueError, OSError)):
                ArtifactCache(link).lookup(key())
        else:
            assert cache.verify().corrupt_entries == 1
            assert link.exists()
            assert cache.lookup(key()).state == "corrupt"
            assert not link.exists()
        assert external.lookup(key()).payload == b"OUTSIDE"
    finally:
        if link.exists():
            unlink_directory(link)


@pytest.mark.parametrize("filename", ["metadata.json", "payload.bin", ".mdrack-artifact-cache-v1"])
def test_real_file_symlinks(tmp_path: Path, filename: str) -> None:
    cache = ArtifactCache(tmp_path / "cache")
    cache.store(key(), b"inside")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"OUTSIDE")
    path = cache.root / filename if filename.startswith(".") else cache.entry_path(key()) / filename
    path.unlink()
    try:
        path.symlink_to(outside)
    except OSError as exc:
        if os.name == "nt" and exc.winerror == 1314:
            pytest.skip("file symlink privilege unavailable; junction cells run separately")
        raise
    if filename.startswith("."):
        with pytest.raises((ValueError, OSError)):
            cache.lookup(key())
    else:
        assert cache.lookup(key(), recover_corrupt=False).state == "corrupt"
        with pytest.raises(ValueError):
            cache.purge(confirm=True)
        assert cache.lookup(key()).state == "corrupt"
    assert outside.read_bytes() == b"OUTSIDE"


@pytest.mark.parametrize(
    "mutation", ["schema", "key", "digest", "size", "missing", "truncated", "oversized", "directory"]
)
def test_corruption_preservation_and_rebuild(tmp_path: Path, mutation: str) -> None:
    cache = ArtifactCache(tmp_path / "cache", max_entry_bytes=8)
    cache.store(key(), b"12345678")
    entry = cache.entry_path(key())
    payload, metadata = entry / "payload.bin", entry / "metadata.json"
    if mutation in {"schema", "key", "digest", "size"}:
        data = json.loads(metadata.read_bytes())
        data[{"schema": "schema", "key": "key", "digest": "payload_sha256", "size": "payload_bytes"}[mutation]] = (
            9 if mutation == "size" else {} if mutation == "key" else "bad")
        metadata.write_text(json.dumps(data), encoding="ascii")
    elif mutation == "missing":
        payload.unlink()
    elif mutation == "directory":
        payload.unlink()
        payload.mkdir()
    else:
        payload.write_bytes(b"x" if mutation == "truncated" else b"x" * 9)
    before = {p.name: p.read_bytes() for p in entry.iterdir() if p.is_file()}
    assert cache.verify().corrupt_entries == 1
    assert cache.lookup(key(), recover_corrupt=False).state == "corrupt"
    assert {p.name: p.read_bytes() for p in entry.iterdir() if p.is_file()} == before
    assert cache.lookup(key()).state == "corrupt"
    assert cache.store(key(), b"rebuilt").state == "stored"
    assert cache.lookup(key()).payload == b"rebuilt"


def test_bounds_and_metadata_window(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "cache", max_entry_bytes=8)
    with pytest.raises(ValueError):
        ArtifactCache(cache.root, max_entry_bytes=0)
    with pytest.raises(TypeError):
        cache.store(key(), "not bytes")
    with pytest.raises(ValueError):
        cache.store(key(), b"123456789")
    cache.store(key(), b"12345678")
    metadata = cache.entry_path(key()) / "metadata.json"
    metadata.write_bytes(b" " * 16385)
    assert cache.lookup(key(), recover_corrupt=False).state == "corrupt"


@pytest.mark.parametrize("action", ["read", "create", "delete"])
def test_replacement_after_real_acquire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    cache = ArtifactCache(tmp_path / "cache")
    cache.store(key(), b"ORIGINAL\r\n\x1a\0")
    outside = ArtifactCache(tmp_path / "outside")
    outside.store(key(), b"OUTSIDE")
    original = cache._open_root
    held = tmp_path / "held"
    replaced = False

    def acquire(*, create: bool):
        nonlocal replaced
        handle = original(create=create)
        if not replaced:
            replaced = True
            cache.root.rename(held)
            link_directory(cache.root, outside.root)
            # A genuinely leaky pathname reader sees a valid outside payload.
            assert (cache.entry_path(key()) / "payload.bin").read_bytes() == b"OUTSIDE"
        return handle

    monkeypatch.setattr(cache, "_open_root", acquire)
    try:
        if action == "read":
            assert cache.lookup(key()).payload == b"ORIGINAL\r\n\x1a\0"
        elif action == "create":
            # Work within the one acquired operation, not a second public call.
            handle = cache._open_root(create=True)
            try:
                fs.mkdir(handle, ".tmp-probe")
                child = fs.open_directory(handle, ".tmp-probe", kind="temporary entry")
                try:
                    fs.write_file(child, "payload.bin", b"PRIVATE")
                    fs.flush(child)
                finally:
                    fs.close(child)
                fs.publish(handle, ".tmp-probe", "published")
                assert "published" in fs.listdir(handle)
                fs.remove_tree(handle, "published")
            finally:
                fs.close(handle)
            assert not (outside.root / "published").exists()
        else:
            cache.discard(key())
            assert not (held / key().digest[:2] / key().digest).exists()
        assert outside.lookup(key()).payload == b"OUTSIDE"
    finally:
        unlink_directory(cache.root)


def test_collision_winner_and_partial_visibility(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = ArtifactCache(tmp_path / "cache")
    original = fs.publish
    calls = 0

    def publish(parent, source, target):
        nonlocal calls
        calls += 1
        assert cache.lookup(key(), recover_corrupt=False).state == "miss"
        with monkeypatch.context() as context:
            context.setattr(fs, "publish", original)
            assert ArtifactCache(cache.root).store(key(), b"WINNER").state == "stored"
        original(parent, source, target)

    monkeypatch.setattr(fs, "publish", publish)
    assert cache.store(key(), b"LOSER").state == "exists"
    assert calls == 1
    assert cache.lookup(key()).payload == b"WINNER"
    assert [p.name for p in cache.entry_path(key()).parent.iterdir()] == [key().digest]


@pytest.mark.parametrize("boundary", ["write", "file_flush", "flush", "publish"])
def test_failure_cleans_owned_temp_and_next_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    cache = ArtifactCache(tmp_path / "cache")
    # Establish marker/shard before injecting failures in entry preparation.
    cache.store(key(), b"first")
    cache.discard(key())
    acquired = []
    with monkeypatch.context() as context:
        if os.name == "nt":
            from mdrack.application import _artifact_cache_windows as native
            original_open = native._open

            def acquire(*args, **kwargs):
                handle = original_open(*args, **kwargs)
                acquired.append(handle)
                return handle

            context.setattr(native, "_open", acquire)

        def fail(*args, **kwargs):
            raise OSError("synthetic failure")
        if boundary == "write":
            context.setattr(cache, "_write_private_file_at", fail)
        elif boundary == "file_flush":
            context.setattr(os, "fsync", fail)
        elif boundary == "flush":
            from mdrack.application import artifact_cache
            context.setattr(artifact_cache, "flush", fail)
        else:
            context.setattr(fs, "publish", fail)
        with pytest.raises(OSError):
            cache.store(key(), b"second")
    assert all(handle.value is None for handle in acquired)
    assert not list(cache.root.rglob(".tmp-*"))
    assert cache.store(key(), b"next").state == "stored"
    assert cache.lookup(key()).payload == b"next"


@pytest.mark.parametrize("cleanup", [False, True])
def test_replacement_immediately_before_native_acquire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      cleanup: bool) -> None:
    cache = ArtifactCache(tmp_path / "cache")
    cache.store(key(), b"inside")
    outside = ArtifactCache(tmp_path / "outside")
    outside.store(key(), b"OUTSIDE")
    entry = cache.entry_path(key())
    saved = entry.with_name("saved")
    # Wrap the real native boundary. The swap occurs before its actual syscall.
    if os.name == "nt":
        from mdrack.application import _artifact_cache_windows as native
        original = native._open
    else:
        native = os
        original = os.open
    swapped = False

    def acquire(*args, **kwargs):
        nonlocal swapped
        name = args[1] if os.name == "nt" else args[0]
        if name == key().digest and not swapped:
            swapped = True
            entry.rename(saved)
            link_directory(entry, outside.entry_path(key()))
        return original(*args, **kwargs)

    try:
        with monkeypatch.context() as context:
            context.setattr(native, "_open" if os.name == "nt" else "open", acquire)
            if cleanup and os.name != "nt":
                # POSIX openat(O_NOFOLLOW) refuses the replacement; Windows
                # acquires the reparse object itself and deletes only that link.
                with pytest.raises(ValueError, match="real directory"):
                    cache.discard(key())
            elif cleanup:
                cache.discard(key())
            else:
                assert cache.lookup(key(), recover_corrupt=False).state == "corrupt"
        assert swapped
        assert outside.lookup(key()).payload == b"OUTSIDE"
        assert (saved / "payload.bin").read_bytes() == b"inside"
    finally:
        if entry.exists():
            unlink_directory(entry)


@pytest.mark.parametrize("level", ["root", "shard", "entry"])
def test_purge_foreign_structure_refuses_without_deletion(tmp_path: Path, level: str) -> None:
    cache = ArtifactCache(tmp_path / "cache")
    cache.store(key(), b"PRIVATE")
    parent = {"root": cache.root, "shard": cache.entry_path(key()).parent,
              "entry": cache.entry_path(key())}[level]
    sentinel = parent / "foreign.bin"
    sentinel.write_bytes(b"FOREIGN")
    with pytest.raises(ValueError):
        cache.purge(confirm=True)
    assert sentinel.read_bytes() == b"FOREIGN"
    assert (cache.entry_path(key()) / "payload.bin").read_bytes() == b"PRIVATE"


def test_exclusive_temp_collision_preserves_foreign_temp(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    from mdrack.application import artifact_cache
    cache = ArtifactCache(tmp_path / "cache")
    cache.store(key(), b"initial")
    cache.entry_path(key()).rename(cache.entry_path(key()).with_name("saved"))
    import uuid
    fixed = uuid.UUID(int=7)
    foreign = cache.entry_path(key()).parent / f".tmp-{fixed.hex}"
    foreign.mkdir()
    (foreign / "sentinel").write_bytes(b"OTHER WRITER")
    monkeypatch.setattr(artifact_cache.uuid, "uuid4", lambda: fixed)
    with pytest.raises(FileExistsError):
        cache.store(key(), b"must-fail")
    assert (foreign / "sentinel").read_bytes() == b"OTHER WRITER"


def test_cleanup_error_still_closes_shard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = ArtifactCache(tmp_path / "cache")
    cache.store(key(), b"initial")
    cache.discard(key())
    acquired = []
    if os.name == "nt":
        from mdrack.application import _artifact_cache_windows as native
        real_open = native._open
    else:
        native = os
        real_open = os.open

    def acquire(*args, **kwargs):
        handle = real_open(*args, **kwargs)
        acquired.append(handle)
        return handle

    def fail(*args, **kwargs):
        raise OSError("synthetic unavailable cleanup")

    with monkeypatch.context() as context:
        context.setattr(native, "_open" if os.name == "nt" else "open", acquire)
        context.setattr(cache, "_write_private_file_at", fail)
        context.setattr(cache, "_remove_tree_at", fail)
        with pytest.raises(OSError):
            cache.store(key(), b"not-stored")
    assert acquired
    if os.name == "nt":
        assert all(handle.value is None for handle in acquired)
    else:
        for descriptor in acquired:
            with pytest.raises(OSError):
                os.fstat(descriptor)
    assert list(cache.root.rglob(".tmp-*"))  # cleanup was honestly unavailable, no false success
    assert cache.store(key(), b"next").state == "stored"
    assert cache.lookup(key()).payload == b"next"


def test_post_publication_flush_failure_keeps_published_entry(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from mdrack.application import artifact_cache
    cache = ArtifactCache(tmp_path / "cache")
    original = artifact_cache.flush

    def flush(handle):
        if cache.entry_path(key()).exists():
            raise OSError("post-publication flush fault")
        original(handle)

    with monkeypatch.context() as context:
        context.setattr(artifact_cache, "flush", flush)
        with pytest.raises(OSError):
            cache.store(key(), b"PUBLISHED")
    assert cache.lookup(key()).payload == b"PUBLISHED"
    assert not list(cache.root.rglob(".tmp-*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX fchmod/fd ownership")
def test_posix_permission_validation_failure_closes_fd(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "cache"
    acquired = []
    real_open = os.open

    def acquire(*args, **kwargs):
        descriptor = real_open(*args, **kwargs)
        acquired.append(descriptor)
        return descriptor

    def fail(*args):
        raise OSError("synthetic fchmod fault")

    with monkeypatch.context() as context:
        context.setattr(os, "open", acquire)
        context.setattr(os, "fchmod", fail)
        with pytest.raises(OSError):
            fs.open_root(path, create=True)
    assert acquired
    for descriptor in acquired:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    handle = fs.open_root(path, create=False)
    fs.close(handle)


def test_status_does_not_hash_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = ArtifactCache(tmp_path / "cache")
    cache.store(key(), b"PRIVATE")
    original = cache._read_file_at

    def read(parent, name, *, limit):
        assert name != "payload.bin"
        return original(parent, name, limit=limit)

    monkeypatch.setattr(cache, "_read_file_at", read)
    assert cache.status().payload_bytes == 7
