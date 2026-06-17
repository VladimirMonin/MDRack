"""Safe JSON rendering for CLI output."""

from __future__ import annotations

import json
from typing import Any

import click


def emit_json(payload: dict[str, Any], *, pretty: bool = False) -> None:
    """Emit JSON to stdout with a Unicode-safe fallback.

    The CLI prefers raw Unicode for readability. On terminals that cannot encode
    some characters, such as emoji on legacy Windows code pages, fall back to an
    ASCII-escaped JSON form instead of failing the whole command.
    """

    indent = 2 if pretty else None
    text = json.dumps(payload, ensure_ascii=False, indent=indent)
    try:
        click.echo(text)
    except UnicodeEncodeError:
        click.echo(json.dumps(payload, ensure_ascii=True, indent=indent))
