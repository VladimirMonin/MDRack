"""Privacy-safe media lifecycle event records."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .fingerprints import (
    AggregationFingerprint,
    EmbeddingFingerprint,
    GrouperFingerprint,
    NormalizationFingerprint,
    ProducerFingerprint,
    TokenCounterFingerprint,
)

MEDIA_EVENT_NAMES = frozenset(
    {
        "media.artifact.validated",
        "media.build.started",
        "media.build.completed",
        "media.build.failed",
        "media.group.started",
        "media.group.completed",
        "media.group.failed",
    }
)

SAFE_EVENT_FIELDS = frozenset(
    {
        "status",
        "operation",
        "category",
        "resource_kind",
        "atom_count",
        "passage_count",
        "frame_count",
        "token_count_total",
        "elapsed_ms",
        "producer_fingerprint",
        "normalization_fingerprint",
        "grouper_fingerprint",
        "token_counter_fingerprint",
        "aggregation_fingerprint",
        "embedding_fingerprint",
    }
)


class MediaEventStatus(StrEnum):
    STARTED = "started"
    VALIDATED = "validated"
    COMPLETED = "completed"
    FAILED = "failed"


class MediaOperation(StrEnum):
    VALIDATE = "validate"
    BUILD_TRANSCRIPT = "build_transcript"
    BUILD_FRAMES = "build_frames"
    GROUP_TRANSCRIPT = "group_transcript"


class MediaResourceKind(StrEnum):
    AUDIO = "audio"
    VIDEO = "video"


class MediaEventCategory(StrEnum):
    VALIDATION = "validation"
    UNSUPPORTED = "unsupported"
    INTERNAL = "internal"


_FINGERPRINT_TYPES = (
    ProducerFingerprint,
    NormalizationFingerprint,
    GrouperFingerprint,
    TokenCounterFingerprint,
    AggregationFingerprint,
    EmbeddingFingerprint,
)


def _safe_value(value: object) -> object:
    if value is None or isinstance(value, bool) or type(value) is int:
        return value
    if isinstance(
        value,
        (MediaEventStatus, MediaOperation, MediaResourceKind, MediaEventCategory, *_FINGERPRINT_TYPES),
    ):
        return value.value
    return "[redacted]"


@dataclass(frozen=True)
class MediaEvent:
    name: str
    fields: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.name not in MEDIA_EVENT_NAMES:
            raise ValueError("name must be a frozen media event name")
        if not isinstance(self.fields, Mapping):
            raise ValueError("fields must be a mapping")
        unknown = set(self.fields).difference(SAFE_EVENT_FIELDS)
        if unknown:
            raise ValueError("fields contain names outside the safe media event schema")
        object.__setattr__(
            self,
            "fields",
            MappingProxyType({key: _safe_value(self.fields[key]) for key in sorted(self.fields)}),
        )

    def to_log_message(self) -> str:
        payload = json.dumps(
            dict(self.fields),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return f"{self.name} {payload}"
