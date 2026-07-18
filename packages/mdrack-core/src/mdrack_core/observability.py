"""Shared privacy-safe event and redaction mechanics for core services."""

from __future__ import annotations

import hashlib
import json
import logging
import math
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
    }
)

SAFE_FIELD_NAMES = frozenset(
    {
        "request_id",
        "run_id",
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
    }
)


class LifecycleStatus(StrEnum):
    STARTED = "started"
    VALIDATED = "validated"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    DEGRADED = "degraded"
    FAILED = "failed"


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
    if isinstance(value, (LifecycleStatus, ErrorCategory, DegradationCategory)):
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


def emit_event(logger: logging.Logger, event: SafeEvent) -> None:
    """Emit one already-sanitized core lifecycle event through stdlib logging."""
    if not isinstance(event, SafeEvent):
        raise ValueError("event must be a SafeEvent")
    logger.info("%s", event.to_log_message())
