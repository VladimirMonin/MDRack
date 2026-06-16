"""JSON envelope functions for CLI output."""

from __future__ import annotations

from typing import Any


def success(data: dict[str, Any], command: str) -> dict[str, Any]:
    """Wrap a successful response in a JSON envelope.

    Args:
        data: Response payload.
        command: CLI command name that produced this response.

    Returns:
        Dict with keys: ok, data, meta.
    """
    return {"ok": True, "data": data, "meta": {"command": command}}


def error(
    message: str,
    code: str,
    command: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap an error response in a JSON envelope.

    Args:
        message: Human-readable error description.
        code: Machine-readable error code.
        command: CLI command name that produced this response.
        details: Optional additional error context.

    Returns:
        Dict with keys: ok, error, meta.
    """
    err: dict[str, Any] = {"message": message, "code": code}
    if details is not None:
        err["details"] = details
    return {"ok": False, "error": err, "meta": {"command": command}}
