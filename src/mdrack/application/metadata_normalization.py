"""Deterministic, bounded projection of source metadata into JSON values."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, cast

from mdrack.domain.blocks import JSONValue
from mdrack.domain.documents import MetadataDiagnostic

METADATA_NORMALIZER_VERSION = "metadata-json-v1"
MetadataInvalidPolicy = Literal["warn_and_continue", "fail_resource"]


@dataclass(frozen=True)
class MetadataLimits:
    """Resource limits applied before metadata reaches a storage boundary."""

    max_serialized_bytes: int = 65_536
    max_depth: int = 8
    max_object_keys: int = 1_000
    max_array_items: int = 1_000
    max_string_bytes: int = 16_384

    def __post_init__(self) -> None:
        if min(
            self.max_serialized_bytes,
            self.max_depth,
            self.max_object_keys,
            self.max_array_items,
            self.max_string_bytes,
        ) < 1:
            raise ValueError("metadata limits must be positive")


@dataclass(frozen=True)
class MetadataNormalizationResult:
    source: dict[str, JSONValue]
    diagnostics: tuple[MetadataDiagnostic, ...]
    fingerprint: str
    policy_fingerprint: str
    normalizer_version: str = METADATA_NORMALIZER_VERSION


class MetadataNormalizationError(ValueError):
    """Strict metadata policy rejected a resource without exposing source values."""

    def __init__(self, diagnostics: tuple[MetadataDiagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        categories = ",".join(item.category for item in diagnostics)
        super().__init__(f"metadata normalization failed: {categories}")


class _RejectedValue(Exception):
    def __init__(self, category: str) -> None:
        self.category = category


def canonical_metadata_bytes(value: Mapping[str, JSONValue]) -> bytes:
    """Serialize normalized metadata with stable UTF-8 JSON semantics."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def metadata_policy_fingerprint(
    limits: MetadataLimits,
    policy: MetadataInvalidPolicy,
) -> str:
    payload = {
        "limits": {
            "max_array_items": limits.max_array_items,
            "max_depth": limits.max_depth,
            "max_object_keys": limits.max_object_keys,
            "max_serialized_bytes": limits.max_serialized_bytes,
            "max_string_bytes": limits.max_string_bytes,
        },
        "normalizer_version": METADATA_NORMALIZER_VERSION,
        "policy": policy,
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def normalize_metadata(
    value: object,
    limits: MetadataLimits | None = None,
    policy: MetadataInvalidPolicy = "warn_and_continue",
) -> MetadataNormalizationResult:
    """Normalize one source metadata object without implicit stringification.

    Invalid fields are omitted in the default policy. Diagnostics contain only
    stable categories and counts; source keys and values never appear in them.
    Strict policy raises after producing the same deterministic diagnostics.
    """

    if policy not in {"warn_and_continue", "fail_resource"}:
        raise ValueError("metadata policy must be 'warn_and_continue' or 'fail_resource'")
    active_limits = limits or MetadataLimits()
    counts: Counter[str] = Counter()

    if not isinstance(value, Mapping):
        counts["METADATA_ROOT_NOT_OBJECT"] += 1
        normalized: dict[str, JSONValue] = {}
    else:
        try:
            normalized = _normalize_mapping(value, active_limits, counts, depth=0)
        except _RejectedValue as exc:
            counts[exc.category] += 1
            normalized = {}

    encoded = canonical_metadata_bytes(normalized)
    if len(encoded) > active_limits.max_serialized_bytes:
        counts["METADATA_SIZE_LIMIT_EXCEEDED"] += 1
        normalized = {}
        encoded = canonical_metadata_bytes(normalized)

    diagnostics = tuple(
        MetadataDiagnostic(category=category, count=count)
        for category, count in sorted(counts.items())
    )
    if diagnostics and policy == "fail_resource":
        raise MetadataNormalizationError(diagnostics)

    return MetadataNormalizationResult(
        source=normalized,
        diagnostics=diagnostics,
        fingerprint=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        policy_fingerprint=metadata_policy_fingerprint(active_limits, policy),
    )


def _normalize_mapping(
    value: Mapping[object, object],
    limits: MetadataLimits,
    counts: Counter[str],
    *,
    depth: int,
) -> dict[str, JSONValue]:
    if depth > limits.max_depth:
        raise _RejectedValue("METADATA_DEPTH_LIMIT_EXCEEDED")
    if len(value) > limits.max_object_keys:
        raise _RejectedValue("METADATA_OBJECT_KEY_LIMIT_EXCEEDED")
    if any(not isinstance(key, str) for key in value):
        raise _RejectedValue("METADATA_NON_STRING_KEY")

    normalized: dict[str, JSONValue] = {}
    keys = cast(list[str], list(value))
    for key in sorted(keys):
        try:
            _check_string(key, limits, key=True)
            normalized[key] = _normalize_value(
                value[key],
                limits,
                counts,
                depth=depth + 1,
            )
        except _RejectedValue as exc:
            counts[exc.category] += 1
    return normalized


def _normalize_value(
    value: object,
    limits: MetadataLimits,
    counts: Counter[str],
    *,
    depth: int,
) -> JSONValue:
    if depth > limits.max_depth:
        raise _RejectedValue("METADATA_DEPTH_LIMIT_EXCEEDED")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        _check_string(value, limits, key=False)
        return value
    if type(value) is int:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _RejectedValue("METADATA_NON_FINITE_NUMBER")
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return _normalize_mapping(value, limits, counts, depth=depth)
    if isinstance(value, (list, tuple)):
        if len(value) > limits.max_array_items:
            raise _RejectedValue("METADATA_ARRAY_ITEM_LIMIT_EXCEEDED")
        items: list[JSONValue] = []
        try:
            for item in value:
                items.append(_normalize_value(item, limits, counts, depth=depth + 1))
        except _RejectedValue:
            raise
        return items
    if isinstance(value, (set, frozenset)):
        if len(value) > limits.max_array_items:
            raise _RejectedValue("METADATA_ARRAY_ITEM_LIMIT_EXCEEDED")
        items = [_normalize_value(item, limits, counts, depth=depth + 1) for item in value]
        return sorted(items, key=_canonical_sort_key)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise _RejectedValue("METADATA_BINARY_UNSUPPORTED")
    raise _RejectedValue("METADATA_TYPE_UNSUPPORTED")


def _check_string(value: str, limits: MetadataLimits, *, key: bool) -> None:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        category = "METADATA_KEY_INVALID_UTF8" if key else "METADATA_STRING_INVALID_UTF8"
        raise _RejectedValue(category) from exc
    if len(encoded) > limits.max_string_bytes:
        category = "METADATA_KEY_LIMIT_EXCEEDED" if key else "METADATA_STRING_LIMIT_EXCEEDED"
        raise _RejectedValue(category)


def _canonical_sort_key(value: JSONValue) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
