"""Windows-only, handle-relative cache I/O (stdlib ctypes, never pathname fallback)."""
from __future__ import annotations

import ctypes as c
import msvcrt
import ntpath
import os
import stat
from ctypes import wintypes as w
from pathlib import Path
from typing import BinaryIO, cast

k = c.WinDLL("kernel32", use_last_error=True)
n = c.WinDLL("ntdll")
a = c.WinDLL("advapi32", use_last_error=True)


class Unicode(c.Structure):
    _fields_ = [("length", w.USHORT), ("maximum", w.USHORT), ("buffer", w.LPWSTR)]


class Attributes(c.Structure):
    _fields_ = [("length", w.ULONG), ("root", w.HANDLE), ("name", c.POINTER(Unicode)),
                ("flags", w.ULONG), ("security", c.c_void_p), ("qos", c.c_void_p)]


class StatusUnion(c.Union):
    _fields_ = [("status", w.LONG), ("pointer", c.c_void_p)]


class IOStatus(c.Structure):
    _fields_ = [("u", StatusUnion), ("information", c.c_size_t)]


class Rename(c.Structure):
    _fields_ = [("replace", w.BYTE), ("root", w.HANDLE), ("length", w.ULONG), ("name", w.WCHAR * 1)]


class DirectoryInfo(c.Structure):
    _fields_ = [("next", w.DWORD), ("index", w.DWORD), ("times_sizes", c.c_longlong * 6),
                ("attrs", w.DWORD), ("name_length", w.DWORD), ("ea_size", w.DWORD),
                ("short_length", c.c_byte), ("short_name", w.WCHAR * 12),
                ("file_id", c.c_longlong), ("name", w.WCHAR * 1)]


class StandardInfo(c.Structure):
    _fields_ = [("allocation", c.c_longlong), ("size", c.c_longlong), ("links", w.DWORD),
                ("delete_pending", w.BYTE), ("directory", w.BYTE)]


class ACL(c.Structure):
    _fields_ = [("revision", w.BYTE), ("reserved", w.BYTE), ("size", w.WORD),
                ("count", w.WORD), ("reserved2", w.WORD)]


def _signature(dll: object, name: str, args: list[object], result: object) -> None:
    fn = getattr(dll, name)
    fn.argtypes, fn.restype = args, result


_signature(n, "NtCreateFile", [c.POINTER(w.HANDLE), w.DWORD, c.POINTER(Attributes), c.POINTER(IOStatus),
                              c.c_void_p, w.ULONG, w.ULONG, w.ULONG, w.ULONG, c.c_void_p, w.ULONG], w.LONG)
_signature(n, "NtSetInformationFile", [w.HANDLE, c.POINTER(IOStatus), c.c_void_p, w.ULONG, c.c_int], w.LONG)
_signature(n, "RtlNtStatusToDosError", [w.LONG], w.ULONG)
_signature(k, "CloseHandle", [w.HANDLE], w.BOOL)
_signature(k, "FlushFileBuffers", [w.HANDLE], w.BOOL)
_signature(k, "GetFileInformationByHandleEx", [w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL)
_signature(k, "LocalFree", [c.c_void_p], c.c_void_p)
_signature(a, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
           [w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), c.POINTER(w.DWORD)], w.BOOL)
_signature(a, "GetSecurityInfo", [w.HANDLE, c.c_int, w.DWORD, c.POINTER(c.c_void_p), c.c_void_p,
                                  c.POINTER(c.c_void_p), c.c_void_p, c.POINTER(c.c_void_p)], w.DWORD)
_signature(a, "GetAce", [c.c_void_p, w.DWORD, c.POINTER(c.c_void_p)], w.BOOL)
_signature(a, "ConvertStringSidToSidW", [w.LPCWSTR, c.POINTER(c.c_void_p)], w.BOOL)
_signature(a, "EqualSid", [c.c_void_p, c.c_void_p], w.BOOL)


class Handle:
    """Distinct from a CRT fd; transfers to a stream explicitly, once."""
    def __init__(self, value: int) -> None:
        self.value: int | None = value


def _bool(ok: int) -> None:
    if not ok:
        raise c.WinError(c.get_last_error())


def _nt(status: int) -> None:
    if status < 0:
        raise c.WinError(n.RtlNtStatusToDosError(status))


def close(handle: Handle) -> None:
    if handle.value is not None:
        value, handle.value = handle.value, None
        _bool(k.CloseHandle(value))


def _component(name: str) -> None:
    if not name or name in {".", ".."} or any(ch in name for ch in "/\\:\0"):
        raise ValueError("artifact cache component invalid")


def _tag(handle: Handle) -> int:
    tag = (w.DWORD * 2)()
    _bool(k.GetFileInformationByHandleEx(handle.value, 9, tag, c.sizeof(tag)))
    return tag[0]


def validate_private(handle: Handle) -> None:
    """Inspect the acquired object's DACL, not chmod/stat emulation."""
    owner, dacl, sd = c.c_void_p(), c.c_void_p(), c.c_void_p()
    error = a.GetSecurityInfo(handle.value, 1, 5, c.byref(owner), None, c.byref(dacl), None, c.byref(sd))
    if error:
        raise c.WinError(error)
    sids: list[c.c_void_p] = []
    try:
        if not dacl.value:
            raise ValueError("artifact cache ACL is not private")
        for text in ("S-1-5-18", "S-1-5-32-544", "S-1-3-4"):
            sid = c.c_void_p()
            _bool(a.ConvertStringSidToSidW(text, c.byref(sid)))
            sids.append(sid)
        acl = c.cast(dacl, c.POINTER(ACL)).contents
        for index in range(acl.count):
            ace = c.c_void_p()
            _bool(a.GetAce(dacl, index, c.byref(ace)))
            header = (w.BYTE * 4).from_address(ace.value)
            ace_type, flags = header[0], header[1]
            if ace_type == 1:  # deny ACE cannot grant access
                continue
            if ace_type != 0:
                raise ValueError("artifact cache ACL type unsupported")
            if flags & 8:  # inherit-only does not grant on this object
                continue
            sid_ptr = ace.value + 8
            if not any(a.EqualSid(sid_ptr, allowed) for allowed in [owner, *sids]):
                raise ValueError("artifact cache ACL is not private")
    finally:
        for sid in sids:
            k.LocalFree(sid)
        k.LocalFree(sd)


def _real(handle: Handle, *, directory: bool, private: bool = True) -> None:
    attrs = _tag(handle)
    if attrs & 0x400 or bool(attrs & 0x10) != directory:
        raise ValueError("artifact cache object must be real and no-follow")
    if private:
        validate_private(handle)


def _open(parent: Handle | None, name: str, *, directory: bool | None = True,
          disposition: int = 1, delete: bool = False, write: bool = False,
          validate: bool = True, private: bool = True) -> Handle:
    if parent is not None:
        _component(name)
        _real(parent, directory=True, private=False)
    text = c.create_unicode_buffer(name)
    size = len(name.encode("utf-16-le"))
    if size > 65532:
        raise ValueError("artifact cache name too long")
    string = Unicode(size, size + 2, c.cast(text, w.LPWSTR))
    sd = c.c_void_p()
    if disposition != 1:
        _bool(a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)", 1, c.byref(sd), None))
    try:
        attrs = Attributes(c.sizeof(Attributes), parent.value if parent else None, c.pointer(string),
                           0x40, sd, None)
        raw, ios = w.HANDLE(), IOStatus()
        access = 0x80000000 | 0x100000 | 0x20000 | (0x40000000 if write else 0) | (0x10000 if delete else 0)
        options = 0x200000 | 0x20 | (1 if directory is True else 0x40 if directory is False else 0)
        _nt(n.NtCreateFile(c.byref(raw), access, c.byref(attrs), c.byref(ios), None,
                           0x80, 7, disposition, options, None, 0))
        handle = Handle(raw.value)
        try:
            if validate and directory is not None:
                _real(handle, directory=directory, private=private)
            return handle
        except BaseException:
            close(handle)
            raise
    finally:
        if sd.value:
            k.LocalFree(sd)


def open_root(path: Path, *, create: bool) -> Handle | None:
    lexical = os.path.abspath(path)
    if lexical.startswith(("\\\\?\\", "\\\\.\\")):
        raise ValueError("artifact cache device root unsupported")
    drive, tail = ntpath.splitdrive(lexical)
    if not drive or not tail.startswith("\\"):
        raise ValueError("artifact cache root invalid")
    prefix = "\\??\\UNC\\" + drive[2:] if drive.startswith("\\\\") else "\\??\\" + drive
    # Walk every ancestor without dereferencing reparse points. Ancestors are
    # not cache-owned; only newly created components and the final root are private.
    parent = _open(None, prefix + "\\", private=False)
    try:
        parts = [p for p in tail.split("\\") if p]
        for index, part in enumerate(parts):
            final = index == len(parts) - 1
            try:
                child = _open(parent, part, disposition=3 if create else 1, write=final,
                              private=final)
            except FileNotFoundError:
                return None
            close(parent)
            parent = child
        if not parts:
            raise ValueError("artifact cache volume root forbidden")
        result, parent = parent, None
        return result
    finally:
        if parent is not None:
            close(parent)


def open_directory(parent: Handle, name: str, *, kind: str) -> Handle:
    try:
        return _open(parent, name, write=True)
    except ValueError as exc:
        raise ValueError(f"artifact cache {kind} must be a private real directory") from exc
    except OSError as exc:
        if exc.winerror in {267, 1920, 4390, 4392}:
            raise ValueError(f"artifact cache {kind} must be a real directory") from exc
        raise


def mkdir(parent: Handle, name: str) -> None:
    close(_open(parent, name, disposition=2, write=True))


def flush(handle: Handle) -> None:
    _real(handle, directory=True, private=False)
    _bool(k.FlushFileBuffers(handle.value))


def listdir(handle: Handle) -> list[str]:
    _real(handle, directory=True, private=False)
    names: list[str] = []
    restart = True
    while True:
        buffer = c.create_string_buffer(65536)
        ok = k.GetFileInformationByHandleEx(handle.value, 11 if restart else 10, buffer, len(buffer))
        restart = False
        if not ok:
            if c.get_last_error() == 18:
                return names
            raise c.WinError(c.get_last_error())
        offset = 0
        while True:
            if offset + c.sizeof(DirectoryInfo) > len(buffer):
                raise ValueError("artifact cache directory response invalid")
            row = DirectoryInfo.from_buffer(buffer, offset)
            start = offset + DirectoryInfo.name.offset
            end = start + row.name_length
            if row.name_length % 2 or end > len(buffer):
                raise ValueError("artifact cache directory name invalid")
            name = buffer.raw[start:end].decode("utf-16-le")
            if name not in {".", ".."}:
                _component(name)
                names.append(name)
            if not row.next:
                break
            if row.next % 8 or row.next < DirectoryInfo.name.offset or offset + row.next < end:
                raise ValueError("artifact cache directory offset invalid")
            offset += row.next


def _info(handle: Handle) -> os.stat_result:
    attrs = _tag(handle)
    info = StandardInfo()
    _bool(k.GetFileInformationByHandleEx(handle.value, 1, c.byref(info), c.sizeof(info)))
    mode = stat.S_IFLNK if attrs & 0x400 else stat.S_IFDIR if attrs & 0x10 else stat.S_IFREG
    return os.stat_result((mode, 0, 0, info.links, 0, 0, info.size, 0, 0, 0))


def file_info(parent: Handle, name: str) -> os.stat_result:
    handle = _open(parent, name, directory=None, validate=False)
    try:
        return _info(handle)
    finally:
        close(handle)


def regular_file_info(parent: Handle, name: str) -> os.stat_result:
    handle = _open(parent, name, directory=False)
    try:
        return _info(handle)
    finally:
        close(handle)


def _stream(handle: Handle, mode: str) -> BinaryIO:
    flags = os.O_BINARY | (os.O_WRONLY if mode == "wb" else os.O_RDONLY)
    try:
        fd = msvcrt.open_osfhandle(handle.value, flags)
    except BaseException:
        close(handle)
        raise
    handle.value = None  # CRT now owns it, including fdopen failure
    try:
        return cast(BinaryIO, os.fdopen(fd, mode))
    except BaseException:
        os.close(fd)
        raise


def read_file(parent: Handle, name: str, *, limit: int) -> bytes:
    handle = _open(parent, name, directory=False)
    try:
        info = _info(handle)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("artifact cache file is not a bounded regular file")
        with _stream(handle, "rb") as stream:
            return stream.read(limit + 1)
    finally:
        close(handle)


def write_file(parent: Handle, name: str, payload: bytes) -> None:
    handle = _open(parent, name, directory=False, disposition=2, write=True)
    try:
        with _stream(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        close(handle)


def publish(parent: Handle, source: str, target: str) -> None:
    _component(target)
    handle = _open(parent, source, delete=True, write=True)
    try:
        raw = target.encode("utf-16-le")
        buffer = c.create_string_buffer(c.sizeof(Rename) + len(raw))
        row = Rename.from_buffer(buffer)
        row.replace, row.root, row.length = 0, parent.value, len(raw)
        c.memmove(c.addressof(buffer) + Rename.name.offset, raw, len(raw))
        ios = IOStatus()
        _nt(n.NtSetInformationFile(handle.value, c.byref(ios), buffer, len(buffer), 10))
    finally:
        close(handle)


def _delete(handle: Handle) -> None:
    flag, ios = w.BYTE(1), IOStatus()
    _nt(n.NtSetInformationFile(handle.value, c.byref(ios), c.byref(flag), c.sizeof(flag), 13))


def remove_tree(parent: Handle, name: str) -> None:
    handle = _open(parent, name, directory=None, delete=True, validate=False)
    try:
        attrs = _tag(handle)
        if attrs & 0x10 and not attrs & 0x400:
            for child in listdir(handle):
                remove_tree(handle, child)
        _delete(handle)
    finally:
        close(handle)


def remove_empty(parent: Handle, child: Handle, name: str) -> None:
    # Delete the acquired empty shard, never reacquire a replacement by name.
    if listdir(child):
        return
    handle = _open(parent, name, delete=True)
    try:
        if not listdir(handle):
            _delete(handle)
    finally:
        close(handle)
