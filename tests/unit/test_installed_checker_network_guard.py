"""Actual installed-checker guard controls; no transport is reached."""
from __future__ import annotations

import re
import runpy
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts" / "check_installed_package.py"


def _lookalike_application_stack(call):
    """Non-stdlib frames deliberately using stdlib-looking file/function names."""
    lower: dict[str, object] = {}
    exec(
        compile(
            "def _fallback_socketpair(call):\n return call()\n",
            "C:/offline-application/socket.py",
            "exec",
        ),
        lower,
    )
    upper: dict[str, object] = {"lower": lower["_fallback_socketpair"]}
    exec(
        compile(
            "def _make_self_pipe(call):\n return lower(call)\n",
            "C:/offline-application/proactor_events.py",
            "exec",
        ),
        upper,
    )
    return upper["_make_self_pipe"](call)


def test_guard_rejects_non_stdlib_lookalike_stack_without_transport() -> None:
    checker = runpy.run_path(str(CHECKER))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setblocking(False)
        allowed = _lookalike_application_stack(
            lambda: checker["_owned_asyncio_self_pipe_connect"](sock, ("127.0.0.1", 9))
        )
    assert allowed is False


@pytest.mark.parametrize("lookalike", [False, True], ids=["ordinary", "lookalike"])
def test_actual_main_guard_blocks_application_connect_before_transport(lookalike: bool) -> None:
    checker = runpy.run_path(str(CHECKER))
    main = checker["main"]
    forwarded: list[bool] = []

    class StopAfterProbe(Exception):
        pass

    class TransportWouldHaveBeenCalled(Exception):
        pass

    def transport_sentinel(*args: object, **kwargs: object) -> None:
        forwarded.append(True)
        raise TransportWouldHaveBeenCalled("OFFLINE sentinel; no OS connect executed")

    def first_oracle() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setblocking(False)
            try:
                if lookalike:
                    _lookalike_application_stack(lambda: sock.connect(("127.0.0.1", 9)))
                else:
                    sock.connect(("127.0.0.1", 9))
            except AssertionError:
                raise StopAfterProbe() from None
        raise AssertionError("guard unexpectedly returned without refusal")

    with patch.dict(main.__globals__, {"_check_imports": first_oracle}), patch.object(
        socket.socket, "connect", transport_sentinel
    ):
        try:
            main()
        except (StopAfterProbe, TransportWouldHaveBeenCalled):
            pass
    assert not forwarded, "actual blocked_connect delegated an application connect to transport"


def test_checker_source_has_no_basename_frame_allowance() -> None:
    source = CHECKER.read_text(encoding="utf-8")
    assert "co_filename" not in source
    assert "_FALLBACK_SOCKETPAIR_CODE" in source
    assert "_SELF_PIPE_CODES" in source
    assert re.search(r"frame\.f_back\.f_code not in _SELF_PIPE_CODES", source)
