"""Shared validation and canonical serialization for media contracts."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import TypeAlias

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]

_MAX_JSON_CONTAINER_DEPTH = 64
_JSON_CONTAINER_LIMIT_ERROR = (
    "JSON containers must be acyclic and at most 64 levels deep"
)


def require_text(value: object, field_name: str, *, allow_empty: bool = False) -> str:
    """Validate UTF-8 text without normalizing caller content."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not allow_empty and not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError(f"{field_name} must be UTF-8 encodable") from None
    return value


def require_int(value: object, field_name: str, *, minimum: int = 0) -> int:
    """Validate an integer without accepting booleans."""
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field_name} must be an integer greater than or equal to {minimum}")
    return value


def require_probability(value: object, field_name: str) -> float:
    """Validate a finite probability."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number between 0 and 1")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{field_name} must be a finite number between 0 and 1")
    return result


def _freeze_json(
    value: object,
    field_name: str,
    *,
    active_container_ids: set[int] | None = None,
    container_depth: int = 0,
) -> JSONValue:
    if active_container_ids is None:
        active_container_ids = set()
    if value is None or isinstance(value, bool) or type(value) is int:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} numbers must be finite")
        return value
    if isinstance(value, str):
        return require_text(value, field_name, allow_empty=True)
    if isinstance(value, Mapping):
        container_id = id(value)
        if (
            container_id in active_container_ids
            or container_depth >= _MAX_JSON_CONTAINER_DEPTH
        ):
            raise ValueError(_JSON_CONTAINER_LIMIT_ERROR)
        active_container_ids.add(container_id)
        try:
            if any(not isinstance(key, str) for key in value):
                raise ValueError(f"{field_name} keys must be strings")
            return MappingProxyType(
                {
                    require_text(key, f"{field_name} key", allow_empty=True): _freeze_json(
                        item,
                        f"{field_name} value",
                        active_container_ids=active_container_ids,
                        container_depth=container_depth + 1,
                    )
                    for key, item in sorted(value.items())
                }
            )
        finally:
            active_container_ids.remove(container_id)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        container_id = id(value)
        if (
            container_id in active_container_ids
            or container_depth >= _MAX_JSON_CONTAINER_DEPTH
        ):
            raise ValueError(_JSON_CONTAINER_LIMIT_ERROR)
        active_container_ids.add(container_id)
        try:
            return tuple(
                _freeze_json(
                    item,
                    f"{field_name}[]",
                    active_container_ids=active_container_ids,
                    container_depth=container_depth + 1,
                )
                for item in value
            )
        finally:
            active_container_ids.remove(container_id)
    raise ValueError(f"{field_name} must use the JSON value grammar")


def freeze_metadata(value: object, field_name: str = "metadata") -> Mapping[str, JSONValue]:
    """Deeply validate and freeze a JSON object."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    frozen = _freeze_json(value, field_name)
    if not isinstance(frozen, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return frozen


def plain_json(value: object) -> object:
    """Convert immutable contract containers to ordinary JSON containers."""
    if isinstance(value, Mapping):
        return {key: plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [plain_json(item) for item in value]
    return value


def canonical_json(value: object) -> str:
    """Validate the complete JSON grammar and serialize it deterministically."""
    frozen = _freeze_json(value, "value")
    return json.dumps(
        plain_json(frozen),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def expect_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} must be a JSON object")
    return value


def expect_keys(value: object, field_name: str, keys: frozenset[str]) -> Mapping[str, object]:
    mapping = expect_mapping(value, field_name)
    if set(mapping) != keys:
        raise ValueError(f"{field_name} must contain exactly: {', '.join(sorted(keys))}")
    return mapping


def expect_sequence(value: object, field_name: str) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a JSON array")
    return tuple(value)
