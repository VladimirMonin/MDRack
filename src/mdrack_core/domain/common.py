"""Shared validation and deterministic JSON-safe value handling."""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import TypeAlias

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]

MAX_JSON_DEPTH = 16
MAX_JSON_ITEMS = 10_000
MAX_JSON_BYTES = 1_000_000

_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_ULID_PATTERN = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}")


class _SafeRequestId(str):
    """Validated correlation identity that observability may serialize verbatim."""


def normalize_request_id(value: object, field_name: str = "request_id") -> _SafeRequestId:
    """Validate and canonicalize a UUID or canonical Crockford-base32 ULID."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a UUID or ULID")
    if _UUID_PATTERN.fullmatch(value):
        try:
            return _SafeRequestId(str(uuid.UUID(value)))
        except ValueError:
            pass
    if _ULID_PATTERN.fullmatch(value):
        return _SafeRequestId(value)
    raise ValueError(f"{field_name} must be a UUID or ULID")


def normalize_optional_request_id(
    value: object,
    field_name: str = "request_id",
) -> _SafeRequestId | None:
    """Validate an optional correlation identity."""
    if value is None:
        return None
    return normalize_request_id(value, field_name)


def require_utf8_encodable(value: object, field_name: str) -> str:
    """Return a string only when its exact value is UTF-8 encodable."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    try:
        str.encode(value, "utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError(f"{field_name} must be UTF-8 encodable") from None
    return value


def require_non_empty(value: object, field_name: str) -> str:
    """Return a non-blank string without changing caller-supplied identity."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return require_utf8_encodable(value, field_name)


def require_optional_non_empty(value: object, field_name: str) -> str | None:
    """Validate an optional non-blank string."""
    if value is None:
        return None
    return require_non_empty(value, field_name)


def require_integer(value: object, field_name: str, *, minimum: int) -> int:
    """Validate an integer range without accepting booleans as integers."""
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field_name} must be an integer greater than or equal to {minimum}")
    return value


def require_finite_number(value: object, field_name: str) -> float:
    """Return a finite numeric value without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be a finite number")
    return result


def _freeze_json_value(value: object, *, depth: int, item_count: list[int]) -> JSONValue:
    if depth > MAX_JSON_DEPTH:
        raise ValueError(f"JSON value exceeds maximum depth {MAX_JSON_DEPTH}")

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return require_utf8_encodable(value, "JSON string")
    if type(value) is int:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value

    if isinstance(value, Mapping):
        item_count[0] += len(value)
        if item_count[0] > MAX_JSON_ITEMS:
            raise ValueError(f"JSON value exceeds maximum item count {MAX_JSON_ITEMS}")
        keys = list(value)
        if any(not isinstance(key, str) for key in keys):
            raise ValueError("JSON object keys must be strings")
        for key in keys:
            require_utf8_encodable(key, "JSON object key")
        frozen: dict[str, JSONValue] = {}
        for key in sorted(keys):
            frozen[key] = _freeze_json_value(
                value[key],
                depth=depth + 1,
                item_count=item_count,
            )
        return MappingProxyType(frozen)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        item_count[0] += len(value)
        if item_count[0] > MAX_JSON_ITEMS:
            raise ValueError(f"JSON value exceeds maximum item count {MAX_JSON_ITEMS}")
        return tuple(
            _freeze_json_value(item, depth=depth + 1, item_count=item_count)
            for item in value
        )

    raise ValueError("value must use the JSON-safe scalar/list/map grammar")


def to_plain_json(value: JSONValue) -> object:
    """Return ordinary JSON containers from the immutable domain representation."""
    if isinstance(value, Mapping):
        return {key: to_plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [to_plain_json(item) for item in value]
    return value


def canonical_json(value: JSONValue) -> str:
    """Serialize a validated value deterministically."""
    return json.dumps(
        to_plain_json(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def freeze_json_mapping(value: object, field_name: str) -> Mapping[str, JSONValue]:
    """Validate, deeply freeze, and size-limit a JSON object."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    frozen = _freeze_json_value(value, depth=0, item_count=[0])
    if not isinstance(frozen, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    if len(canonical_json(frozen).encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError(f"{field_name} exceeds maximum encoded size {MAX_JSON_BYTES}")
    return frozen
