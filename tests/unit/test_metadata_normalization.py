"""M1 contracts for bounded deterministic metadata normalization."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from mdrack.application.metadata_normalization import (
    MetadataLimits,
    MetadataNormalizationError,
    canonical_metadata_bytes,
    normalize_metadata,
)


class _Unsupported:
    pass


def test_supported_json_and_yaml_values_preserve_types_deterministically() -> None:
    source = {
        "string": "3",
        "integer": 3,
        "float": 3.0,
        "boolean": True,
        "null": None,
        "array": ["one", 2, False],
        "object": {"key": "value"},
        "date": date(2026, 7, 20),
        "datetime": datetime(2026, 7, 20, 12, 30, tzinfo=timezone.utc),
        "tuple": ("b", "a"),
        "set": {"b", "a"},
    }

    first = normalize_metadata(source)
    second = normalize_metadata(source)

    assert first == second
    assert first.source == {
        "array": ["one", 2, False],
        "boolean": True,
        "date": "2026-07-20",
        "datetime": "2026-07-20T12:30:00+00:00",
        "float": 3.0,
        "integer": 3,
        "null": None,
        "object": {"key": "value"},
        "set": ["a", "b"],
        "string": "3",
        "tuple": ["b", "a"],
    }
    assert first.diagnostics == ()
    assert first.fingerprint.startswith("sha256:")
    assert first.policy_fingerprint.startswith("sha256:")
    assert canonical_metadata_bytes(first.source) == canonical_metadata_bytes(second.source)


def test_invalid_fields_are_omitted_without_implicit_stringification() -> None:
    sentinel = "PRIVATE_METADATA_SENTINEL"
    result = normalize_metadata(
        {
            "kept": "safe",
            "binary": sentinel.encode(),
            "nan": float("nan"),
            "infinity": float("inf"),
            "object": _Unsupported(),
        }
    )

    assert result.source == {"kept": "safe"}
    assert [(item.category, item.count) for item in result.diagnostics] == [
        ("METADATA_BINARY_UNSUPPORTED", 1),
        ("METADATA_NON_FINITE_NUMBER", 2),
        ("METADATA_TYPE_UNSUPPORTED", 1),
    ]
    rendered = repr(result.diagnostics)
    assert sentinel not in rendered
    assert "_Unsupported" not in rendered


def test_limits_drop_only_rejected_fields_and_bound_final_payload() -> None:
    limits = MetadataLimits(
        max_serialized_bytes=1_000,
        max_depth=2,
        max_object_keys=10,
        max_array_items=2,
        max_string_bytes=5,
    )
    result = normalize_metadata(
        {
            "array": [1, 2, 3],
            "deep": {"one": {"two": "three"}},
            "long": "123456",
            "safe": "ok",
        },
        limits,
    )

    assert result.source == {"deep": {"one": {}}, "safe": "ok"}
    assert {item.category for item in result.diagnostics} == {
        "METADATA_ARRAY_ITEM_LIMIT_EXCEEDED",
        "METADATA_DEPTH_LIMIT_EXCEEDED",
        "METADATA_STRING_LIMIT_EXCEEDED",
    }
    assert len(canonical_metadata_bytes(result.source)) <= limits.max_serialized_bytes

    oversized = normalize_metadata(
        {"large": "12345", "safe": "ok"},
        MetadataLimits(max_serialized_bytes=12),
    )
    assert oversized.source == {}
    assert oversized.diagnostics[0].category == "METADATA_SIZE_LIMIT_EXCEEDED"


def test_root_limits_and_strict_policy_fail_with_value_free_diagnostics() -> None:
    default = normalize_metadata({"a": 1, "b": 2}, MetadataLimits(max_object_keys=1))
    assert default.source == {}
    assert default.diagnostics[0].category == "METADATA_OBJECT_KEY_LIMIT_EXCEEDED"

    with pytest.raises(MetadataNormalizationError) as caught:
        normalize_metadata(
            {"private-key": _Unsupported()},
            policy="fail_resource",
        )
    assert "private-key" not in str(caught.value)
    assert "_Unsupported" not in str(caught.value)


def test_key_and_container_grammar_rejects_unsafe_shapes_without_values() -> None:
    non_string_key = normalize_metadata({1: "private"})
    assert non_string_key.source == {}
    assert non_string_key.diagnostics[0].category == "METADATA_NON_STRING_KEY"

    long_key = normalize_metadata(
        {"PRIVATE_KEY_SENTINEL": "private"},
        MetadataLimits(max_string_bytes=5),
    )
    assert long_key.source == {}
    assert long_key.diagnostics[0].category == "METADATA_KEY_LIMIT_EXCEEDED"
    assert "PRIVATE_KEY_SENTINEL" not in repr(long_key.diagnostics)

    array_limit = normalize_metadata(
        {"items": [1, 2]},
        MetadataLimits(max_array_items=1),
    )
    assert array_limit.source == {}
    assert array_limit.diagnostics[0].category == "METADATA_ARRAY_ITEM_LIMIT_EXCEEDED"
