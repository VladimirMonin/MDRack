"""Launch only the selected synthetic Python tool using the qualified interpreter.

This is a call-through at the external executable edge, not a mocked extractor
response: stdin/stdout, process termination and all product parsing remain real.
It avoids relying on POSIX shebang dispatch for a .py fixture on Windows.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def install_python_tool_launcher(
    monkeypatch: pytest.MonkeyPatch, script: Path,
) -> list[tuple[int, tuple[str, ...]]]:
    original = subprocess.Popen
    launches: list[tuple[int, tuple[str, ...]]] = []

    def launch(argv: Any, *args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        selected = argv == [str(script)]
        if selected:
            argv = [sys.executable, str(script)]
        process = original(argv, *args, **kwargs)
        if selected:
            launches.append((process.pid, tuple(argv)))
        return process

    monkeypatch.setattr(subprocess, 'Popen', launch)
    return launches
