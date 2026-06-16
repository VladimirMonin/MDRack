"""Custom exceptions and Click error handler for MDRack."""

from __future__ import annotations

import json
import logging
from typing import Any

import click

from mdrack.output.envelope import error as envelope_error

logger = logging.getLogger(__name__)


class MDRackError(Exception):
    """Base exception for all MDRack errors."""

    code: str = "MDRACK_ERROR"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details


class ConfigError(MDRackError):
    """Configuration loading or validation error."""

    code: str = "CONFIG_ERROR"


class StorageError(MDRackError):
    """SQLite storage read/write error."""

    code: str = "STORAGE_ERROR"


class EmbeddingError(MDRackError):
    """Embedding provider or model error."""

    code: str = "EMBEDDING_ERROR"


class ClickExceptionHandler(click.exceptions.UsageError):
    """Convert MDRackError into JSON output via Click."""

    def __init__(
        self,
        exc: MDRackError,
        command_name: str | None = None,
    ) -> None:
        self.exc = exc
        self.command_name = command_name or "unknown"
        super().__init__(str(exc), ctx=None)

    def format_message(self) -> str:  # noqa: ARG002
        """Return JSON error envelope as the displayed message."""
        payload = envelope_error(
            message=str(self.exc),
            code=self.exc.code,
            command=self.command_name,
            details=self.exc.details,
        )
        return json.dumps(payload, ensure_ascii=False)


def handle_mdrack_error(ctx: click.Context, exc: MDRackError) -> None:
    """Click exception handler that outputs JSON errors.

    Registered via ``main.result_callback`` or ``@main.resultcallback``.
    """
    cmd_name = ctx.invoked_subcommand or ctx.info_name or "mdrack"
    handler = ClickExceptionHandler(exc, command_name=cmd_name)
    click.echo(handler.format_message(), err=True)
    ctx.exit(1)
