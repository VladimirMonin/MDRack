"""Native Windows ACL, acquired-handle lifetime and in-place reparse boundaries."""
from __future__ import annotations

import ctypes as c
import os
import subprocess
from ctypes import wintypes as w
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows API")


def _native():
    from mdrack.application import _artifact_cache_windows
    return _artifact_cache_windows


def test_native_layouts_and_signed_status_mapping() -> None:
    native = _native()
    pointer = c.sizeof(c.c_void_p)
    assert c.sizeof(w.LONG) == 4
    assert c.sizeof(native.Unicode) == (16 if pointer == 8 else 8)
    assert c.sizeof(native.Attributes) == (48 if pointer == 8 else 24)
    assert c.sizeof(native.IOStatus) == pointer * 2
    assert native.Rename.root.offset == pointer
    assert native.Rename.name.offset == (20 if pointer == 8 else 12)
    assert native.DirectoryInfo.name.offset == 104
    native._nt(0)
    native._nt(0x40000000)
    with pytest.raises(FileNotFoundError):
        native._nt(c.c_int32(0xC0000034).value)


def _acl(path: Path) -> tuple[bool, set[str]]:
    native = _native()
    handle = native._open(None, "\\??\\" + str(path), directory=path.is_dir(), private=False)
    owner, dacl, sd = c.c_void_p(), c.c_void_p(), c.c_void_p()
    try:
        assert native.a.GetSecurityInfo(handle.value, 1, 5, c.byref(owner), None,
                                       c.byref(dacl), None, c.byref(sd)) == 0
        control, revision = w.WORD(), w.DWORD()
        native._signature(native.a, "GetSecurityDescriptorControl",
                          [c.c_void_p, c.POINTER(w.WORD), c.POINTER(w.DWORD)], w.BOOL)
        assert native.a.GetSecurityDescriptorControl(sd, c.byref(control), c.byref(revision))
        native._signature(native.a, "ConvertSidToStringSidW", [c.c_void_p, c.POINTER(w.LPWSTR)], w.BOOL)
        names = set()
        for index in range(c.cast(dacl, c.POINTER(native.ACL)).contents.count):
            ace = c.c_void_p()
            assert native.a.GetAce(dacl, index, c.byref(ace))
            header = (w.BYTE * 4).from_address(ace.value)
            assert header[0] == 0  # actual new objects have allow-only ACEs
            text = w.LPWSTR()
            assert native.a.ConvertSidToStringSidW(ace.value + 8, c.byref(text))
            try:
                names.add(text.value)
            finally:
                native.k.LocalFree(c.cast(text, c.c_void_p))
        return bool(control.value & 0x1000), names
    finally:
        if sd.value:
            native.k.LocalFree(sd)
        native.close(handle)


def _broad(path: Path) -> None:
    result = subprocess.run(["icacls.exe", str(path), "/grant", "*S-1-1-0:(OI)(CI)F"],
                            capture_output=True, timeout=15)
    assert result.returncode == 0


def test_private_creation_under_broad_parent_and_existing_root_refusal(tmp_path: Path) -> None:
    from mdrack.application.artifact_cache import ArtifactCache, ArtifactCacheKey
    parent = tmp_path / "broad"
    parent.mkdir()
    _broad(parent)
    fingerprint = "sha256:" + "a" * 64
    key = ArtifactCacheKey("transcript", *([fingerprint] * 6))
    cache = ArtifactCache(parent / "private")
    cache.store(key, b"PRIVATE\r\n\x1a\0")
    for path in [cache.root, *cache.root.rglob("*")]:
        protected, sids = _acl(path)
        assert protected
        assert sids == {"S-1-5-18", "S-1-5-32-544", "S-1-3-4"}
    insecure = parent / "insecure"
    insecure.mkdir()
    assert "S-1-1-0" in _acl(insecure)[1]
    with pytest.raises(ValueError, match="ACL"):
        ArtifactCache(insecure).store(key, b"MUST NOT WRITE")
    assert list(insecure.iterdir()) == []


def test_validation_failure_closes_real_acquisition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    native = _native()
    root = native.open_root(tmp_path / "cache", create=True)
    native.close(root)
    original_close = native.close
    closed = []

    def close(handle):
        value = handle.value
        original_close(handle)
        if value is not None:
            closed.append(handle)

    def fail(handle):
        raise RuntimeError("post-acquire validation fault")

    with monkeypatch.context() as context:
        context.setattr(native, "close", close)
        context.setattr(native, "validate_private", fail)
        with pytest.raises(RuntimeError):
            native.open_root(tmp_path / "cache", create=False)
    assert closed
    assert all(handle.value is None for handle in closed)
    handle = native.open_root(tmp_path / "cache", create=False)
    native.close(handle)


def test_binary_transfer_failure_and_flush_close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    native = _native()
    root = native.open_root(tmp_path / "cache", create=True)
    try:
        original = native.msvcrt.open_osfhandle
        acquired = []
        real_open = native._open

        def acquire(*args, **kwargs):
            handle = real_open(*args, **kwargs)
            acquired.append(handle)
            return handle

        def fail(*args):
            raise OSError("synthetic CRT transfer")

        with monkeypatch.context() as context:
            context.setattr(native, "_open", acquire)
            context.setattr(native.msvcrt, "open_osfhandle", fail)
            with pytest.raises(OSError):
                native.write_file(root, "failed.bin", b"PRIVATE")
        assert acquired and all(h.value is None for h in acquired)
        assert native.msvcrt.open_osfhandle is original
        native.write_file(root, "next.bin", b"\r\n\x1a\0")
        assert native.read_file(root, "next.bin", limit=4) == b"\r\n\x1a\0"
    finally:
        native.close(root)


@pytest.mark.parametrize("at_syscall", [False, True])
def test_in_place_reparse_mutation_of_held_empty_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         at_syscall: bool) -> None:
    native = _native()
    root = native.open_root(tmp_path / "cache", create=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_bytes(b"OUTSIDE")
    # A mount-point reparse buffer, applied through the genuinely held directory.
    substitute = ("\\??\\" + str(outside)).encode("utf-16-le")
    display = str(outside).encode("utf-16-le")
    import struct
    paths = substitute + b"\0\0" + display + b"\0\0"
    body = struct.pack("<HHHH", 0, len(substitute), len(substitute) + 2, len(display)) + paths
    raw = struct.pack("<IHH", 0xA0000003, len(body), 0) + body
    buffer = c.create_string_buffer(raw)
    native._signature(native.k, "DeviceIoControl", [w.HANDLE, w.DWORD, c.c_void_p, w.DWORD,
                      c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p], w.BOOL)
    returned = w.DWORD()
    mutated = False

    def mutate():
        nonlocal mutated
        assert native.k.DeviceIoControl(root.value, 0x900A4, buffer, len(raw), None, 0,
                                        c.byref(returned), None), c.get_last_error()
        mutated = True
        assert (tmp_path / "cache" / "sentinel").read_bytes() == b"OUTSIDE"

    try:
        if at_syscall:
            original = native.n.NtCreateFile

            def syscall(*args):
                mutate()
                return original(*args)

            with monkeypatch.context() as context:
                context.setattr(native.n, "NtCreateFile", syscall)
                with pytest.raises((ValueError, OSError)):
                    native.read_file(root, "sentinel", limit=32)
        else:
            mutate()
            with pytest.raises(ValueError):
                native.listdir(root)
            with pytest.raises(ValueError):
                native.read_file(root, "sentinel", limit=32)
        assert mutated
        assert (outside / "sentinel").read_bytes() == b"OUTSIDE"
    finally:
        native.close(root)
        if mutated:
            (tmp_path / "cache").rmdir()
