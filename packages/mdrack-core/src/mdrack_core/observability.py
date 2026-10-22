"""Shared privacy-safe event and redaction mechanics for core services."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import math
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .domain.common import _SafeRequestId
from .domain.errors import DegradationCategory, ErrorCategory

REDACTED = "[redacted]"

CORE_EVENT_NAMES = frozenset(
    {
        "core.index.started",
        "core.index.validated",
        "core.index.completed",
        "core.index.failed",
        "core.search.started",
        "core.search.branch.completed",
        "core.search.branch.degraded",
        "core.search.fusion.completed",
        "core.search.completed",
        "core.search.failed",
        "core.similarity.started",
        "core.similarity.completed",
        "core.lifecycle.started",
        "core.lifecycle.completed",
        "core.lifecycle.degraded",
        "core.lifecycle.failed",
        "core.lifecycle.recovered",
    }
)

SAFE_FIELD_NAMES = frozenset(
    {
        "request_id",
        "run_id",
        "execution_id",
        "operation",
        "status",
        "reason",
        "category",
        "resource_kind",
        "media_type",
        "target",
        "adapter_name",
        "branch_fingerprint",
        "space_fingerprint",
        "representation_count",
        "unit_count",
        "text_unit_count",
        "vector_unit_count",
        "vector_count",
        "space_count",
        "facet_count",
        "input_bytes",
        "representation_token_count_total",
        "representation_token_count_max",
        "unit_token_count_total",
        "unit_token_count_max",
        "branch_count",
        "lexical_branch_count",
        "vector_branch_count",
        "scope_filter_count",
        "requested_limit",
        "candidate_limit_total",
        "candidate_count",
        "fusion_input_count",
        "unique_unit_count",
        "unique_resource_count",
        "result_count",
        "rrf_k",
        "degraded_branch_count",
        "elapsed_ms",
        "validation_ms",
        "storage_ms",
        "publication_applied",
    }
)


class LifecycleStatus(StrEnum):
    STARTED = "started"
    VALIDATED = "validated"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    DEGRADED = "degraded"
    FAILED = "failed"
    RECOVERED = "recovered"


class LifecycleOperation(StrEnum):
    """Closed operation categories permitted in privacy-safe lifecycle events."""

    ACCEPTANCE = "acceptance"
    CATALOG = "catalog"
    INDEXING = "indexing"
    INGESTION = "ingestion"
    RETRIEVAL = "retrieval"


class LifecycleReason(StrEnum):
    """Closed reason categories permitted in lifecycle evidence."""

    CLEANUP_FAILED = "cleanup_failed"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    DEPENDENCY_FAILED = "dependency_failed"
    INTEGRITY_FAILED = "integrity_failed"
    VALIDATION_FAILED = "validation_failed"


@dataclass(frozen=True)
class SafeFingerprint:
    value: str

    def __post_init__(self) -> None:
        digest = self.value.removeprefix("sha256:")
        if (
            not self.value.startswith("sha256:")
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("value must be a sha256 fingerprint")


def safe_fingerprint(value: str | bytes) -> SafeFingerprint:
    """Return a non-reversible stable fingerprint for a potentially private value."""
    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    return SafeFingerprint(f"sha256:{hashlib.sha256(raw).hexdigest()}")


def _sanitize_value(value: object) -> object:
    if value is None or isinstance(value, bool):
        return value
    if type(value) is int:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("event numbers must be finite")
        return value
    if isinstance(value, SafeFingerprint):
        return value.value
    if isinstance(value, _SafeRequestId):
        return str(value)
    if isinstance(
        value,
        (LifecycleStatus, LifecycleOperation, LifecycleReason, ErrorCategory, DegradationCategory),
    ):
        return value.value
    return REDACTED


@dataclass(frozen=True)
class SafeEvent:
    name: str
    fields: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.name not in CORE_EVENT_NAMES:
            raise ValueError("name must be a frozen core event name")
        if not isinstance(self.fields, Mapping):
            raise ValueError("fields must be a mapping")
        unknown = set(self.fields).difference(SAFE_FIELD_NAMES)
        if unknown:
            raise ValueError("fields contain names outside the safe event schema")
        sanitized = {
            key: _sanitize_value(self.fields[key])
            for key in sorted(self.fields)
        }
        object.__setattr__(self, "fields", MappingProxyType(sanitized))

    def to_log_message(self) -> str:
        payload = json.dumps(
            dict(self.fields),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return f"{self.name} {payload}"


# Saturating, content-free diagnostic-loss signal. Never uses the failed sink.
_diagnostic_loss_count = 0


def _note_sink_loss() -> None:
    global _diagnostic_loss_count
    _diagnostic_loss_count = min(_diagnostic_loss_count + 1, 2**31 - 1)


def _handle_sink_error(record: logging.LogRecord, *, closed_stream: bool = False) -> None:
    error = sys.exception()
    if isinstance(error, OSError) or (closed_stream and isinstance(error, ValueError)):
        _note_sink_loss()
    elif error is not None:
        raise error


def _deliver_record(handler: logging.Handler, record: logging.LogRecord) -> None:
    # Filters and format/schema mistakes are not sink I/O failures.
    decision = handler.filter(record)
    if not decision:
        return
    if isinstance(decision, logging.LogRecord):
        record = decision
    handler.acquire()
    try:
        if isinstance(handler, logging.StreamHandler) and type(handler).emit in (
            logging.StreamHandler.emit, logging.FileHandler.emit,
        ):
            # Stdlib emit catches I/O errors itself and handleError otherwise writes
            # a raw traceback to stderr. A delivery-local copy replaces only that
            # fallback, without mutating a host's handler or global logging policy.
            rendered = handler.format(record)
            guarded = copy.copy(handler)
            guarded.format = lambda record: rendered  # type: ignore[method-assign]
            def handle_error(record: logging.LogRecord) -> None:
                known_closed_stream = (
                    type(guarded.stream) in (io.StringIO, io.TextIOWrapper) and guarded.stream.closed is True
                )
                _handle_sink_error(record, closed_stream=known_closed_stream)

            guarded.handleError = handle_error  # type: ignore[method-assign]
            try:
                guarded.emit(record)
            except OSError:
                _note_sink_loss()
            finally:
                # Transfer only a FileHandler stream newly acquired during emit.
                # Ordinary StreamHandlers (notably lastResort) may expose a
                # read-only stream property and never own a delayed file handle.
                if isinstance(handler, logging.FileHandler) and handler.stream is None and guarded.stream is not None:
                    handler.stream = guarded.stream
        else:
            try:
                handler.emit(record)
            except OSError:
                _note_sink_loss()
    finally:
        handler.release()


def _log_safely(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    extra: Mapping[str, object] | None = None,
) -> None:
    """Deliver a pre-serialized safe message; isolate only sink I/O errors.

    Keep stdlib logger/handler filters, levels, propagation and record factories.
    No handler registration, root configuration, raw fallback or recursive logging.
    """
    if not logger.isEnabledFor(level):
        return
    record = logger.makeRecord(logger.name, level, "", 0, message, (), None, extra=extra)
    decision = logger.filter(record)
    if not decision:
        return
    if isinstance(decision, logging.LogRecord):
        record = decision
    current: logging.Logger | None = logger
    found = False
    while current is not None:
        for handler in current.handlers:
            found = True
            if record.levelno >= handler.level:
                _deliver_record(handler, record)
        if not current.propagate:
            break
        current = current.parent
    if not found and logging.lastResort is not None and record.levelno >= logging.lastResort.level:
        _deliver_record(logging.lastResort, record)


def emit_event(logger: logging.Logger, event: SafeEvent) -> None:
    """Emit one already-sanitized core lifecycle event through stdlib logging."""
    if not isinstance(event, SafeEvent):
        raise ValueError("event must be a SafeEvent")
    _log_safely(logger, logging.INFO, event.to_log_message())
