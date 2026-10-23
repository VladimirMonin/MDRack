"""Private Win32 helper lifetime: suspended -> owned job -> resume -> reap.

Public Win32 process APIs only. Retain the primary thread handle directly;
no subprocess monkeypatch, global registration or dependency modification.
"""
from __future__ import annotations

import _winapi
import ctypes
import msvcrt
import os
import subprocess
import time
from ctypes import wintypes as T
from typing import Any

_K = ctypes.WinDLL("kernel32", use_last_error=True)


class _Basic(ctypes.Structure):
    _fields_ = [("ProcessTime", ctypes.c_longlong), ("JobTime", ctypes.c_longlong), ("Flags", T.DWORD),
                ("MinimumWorkingSet", ctypes.c_size_t), ("MaximumWorkingSet", ctypes.c_size_t),
                ("ActiveProcessLimit", T.DWORD), ("Affinity", ctypes.c_size_t),
                ("PriorityClass", T.DWORD), ("SchedulingClass", T.DWORD)]


class _IO(ctypes.Structure):
    _fields_ = [(x, ctypes.c_ulonglong) for x in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes",
                                                "WriteBytes", "OtherBytes")]


class _Extended(ctypes.Structure):
    _fields_ = [("Basic", _Basic), ("IO", _IO), ("ProcessMemory", ctypes.c_size_t),
                ("JobMemory", ctypes.c_size_t), ("PeakProcessMemory", ctypes.c_size_t),
                ("PeakJobMemory", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [("TotalUser", ctypes.c_longlong), ("TotalKernel", ctypes.c_longlong),
                ("PeriodUser", ctypes.c_longlong), ("PeriodKernel", ctypes.c_longlong),
                ("PageFaults", T.DWORD), ("TotalProcesses", T.DWORD), ("ActiveProcesses", T.DWORD),
                ("TerminatedProcesses", T.DWORD)]


assert ctypes.sizeof(_Basic) == 64 and ctypes.sizeof(_Extended) == 144 and ctypes.sizeof(_Accounting) == 48
for _name, _args, _result in [
    ("CreateJobObjectW", [T.LPVOID, T.LPCWSTR], T.HANDLE),
    ("SetInformationJobObject", [T.HANDLE, ctypes.c_int, T.LPVOID, T.DWORD], T.BOOL),
    ("AssignProcessToJobObject", [T.HANDLE, T.HANDLE], T.BOOL),
    ("TerminateJobObject", [T.HANDLE, T.UINT], T.BOOL),
    ("QueryInformationJobObject", [T.HANDLE, ctypes.c_int, T.LPVOID, T.DWORD, T.LPVOID], T.BOOL),
    ("ResumeThread", [T.HANDLE], T.DWORD),
    ("CloseHandle", [T.HANDLE], T.BOOL),
]:
    _function = getattr(_K, _name)
    _function.argtypes = _args
    _function.restype = _result


def _check(value: Any) -> Any:
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


class _Process:
    def __init__(self, handle: int, pid: int) -> None:
        self.handle = handle
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is None and _winapi.WaitForSingleObject(self.handle, 0) == 0:
            self.returncode = _winapi.GetExitCodeProcess(self.handle)
        return self.returncode

    def wait(self, timeout: float = 15) -> int:
        if _winapi.WaitForSingleObject(self.handle, int(timeout * 1000)) != 0:
            raise subprocess.TimeoutExpired("external_host", timeout)
        self.returncode = _winapi.GetExitCodeProcess(self.handle)
        return self.returncode

    def close(self) -> None:
        _winapi.CloseHandle(self.handle)


class Job:
    def __init__(self) -> None:
        self.handle = _check(_K.CreateJobObjectW(None, None))
        self.thread: Any = None
        self.creation_identity: dict[str, int | bool] = {}
        self.forced_tree_stop = False
        self.closed = False
        info = _Extended()
        info.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            _check(_K.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)))
        except BaseException:
            _K.CloseHandle(self.handle)
            raise

    def popen(self, args: list[str], **kwargs: Any) -> _Process:
        if set(kwargs) - {"cwd", "env", "stdin", "stdout", "stderr"}:
            raise ValueError("external_host_process_options")
        duplicates: list[int] = []
        with open(os.devnull, "rb") as null:
            def duplicate(stream: Any) -> int:
                value = _winapi.DuplicateHandle(
                    _winapi.GetCurrentProcess(), msvcrt.get_osfhandle(stream.fileno()),
                    _winapi.GetCurrentProcess(), 0, True, 2,
                )
                duplicates.append(value)
                return value
            try:
                startup = subprocess.STARTUPINFO()
                startup.dwFlags = subprocess.STARTF_USESTDHANDLES
                startup.hStdInput = duplicate(null)
                startup.hStdOutput = duplicate(kwargs["stdout"])
                stderr = kwargs.get("stderr")
                startup.hStdError = (startup.hStdOutput if stderr == subprocess.STDOUT else duplicate(stderr))
                startup.lpAttributeList = {"handle_list": duplicates}
                hp, ht, pid, tid = _winapi.CreateProcess(
                    args[0], subprocess.list2cmdline(args), None, None, True, 4 | 0x400,
                    kwargs.get("env") or dict(os.environ),
                    str(kwargs["cwd"]) if kwargs.get("cwd") is not None else None, startup,
                )
                try:
                    _check(_K.AssignProcessToJobObject(self.handle, hp))
                    self.thread = ht
                    self.creation_identity = {"pid": pid, "primary_thread_id": tid,
                                              "job_assigned_before_any_child_instruction": True}
                except BaseException:
                    _winapi.TerminateProcess(hp, 1223)
                    _winapi.WaitForSingleObject(hp, 15000)
                    _winapi.CloseHandle(ht)
                    _winapi.CloseHandle(hp)
                    raise
                return _Process(hp, pid)
            finally:
                for handle in duplicates:
                    _winapi.CloseHandle(handle)

    def resume(self) -> None:
        if self.thread is None:
            raise RuntimeError("owned_primary_thread_missing")
        try:
            if _K.ResumeThread(self.thread) == 0xffffffff:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _winapi.CloseHandle(self.thread)
            self.thread = None

    def terminate(self) -> None:
        self.forced_tree_stop = True
        _check(_K.TerminateJobObject(self.handle, 1223))

    def active(self) -> int:
        value = _Accounting()
        _check(_K.QueryInformationJobObject(self.handle, 1, ctypes.byref(value), ctypes.sizeof(value), None))
        return int(value.ActiveProcesses)

    def close(self) -> None:
        if self.closed:
            return
        if self.thread is not None:
            _winapi.CloseHandle(self.thread)
            self.thread = None
        if self.active():
            self.terminate()
        deadline = time.monotonic() + 15
        while self.active() and time.monotonic() < deadline:
            time.sleep(.02)
        if self.active():
            raise RuntimeError("owned_job_not_quiescent")
        _check(_K.CloseHandle(self.handle))
        self.closed = True
