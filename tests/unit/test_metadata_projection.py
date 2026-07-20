"""M2 pure metadata projection and typed-scalar codec contracts."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from mdrack.application.metadata_projection import (
    FACET_SCALAR_CODEC,
    MetadataProjection,
    MetadataProjectionPolicy,
    metadata_projection_policy_from_config,
    resolve_json_pointer,
)
from mdrack.config.loader import load_config
from mdrack.config.models import MetadataConfig, MetadataProjectionConfig


@pytest.mark.parametrize(
    "value",
    ["3", "slash/value~percent%", 3, -4, 3.0, -0.0, True, False, None],
)
def test_typed_scalar_codec_has_ingest_query_display_identity(value: object) -> None:
    encoded = FACET_SCALAR_CODEC.encode(value)  # type: ignore[arg-type]
    decoded = FACET_SCALAR_CODEC.decode(encoded)
    displayed = FACET_SCALAR_CODEC.display(encoded)
    parsed = FACET_SCALAR_CODEC.parse_display(displayed)

    assert type(decoded) is type(value)
    assert type(parsed) is type(value)
    assert FACET_SCALAR_CODEC.encode(decoded) == encoded
    assert FACET_SCALAR_CODEC.encode(parsed) == encoded
    if isinstance(value, float) and value == 0.0:
        assert isinstance(decoded, float)
        assert math.copysign(1.0, decoded) == math.copysign(1.0, value)


def test_typed_scalar_codec_separates_types_and_rejects_noncanonical_values() -> None:
    values = ("3", 3, 3.0, True, None)
    assert len({FACET_SCALAR_CODEC.encode(value) for value in values}) == len(values)

    for invalid in ("plain", "x:value", "i:03", "f:3", "b:1", "z:", "s:%2f"):
        with pytest.raises(ValueError):
            FACET_SCALAR_CODEC.decode(invalid)
    with pytest.raises(ValueError):
        FACET_SCALAR_CODEC.encode(float("inf"))
    with pytest.raises((TypeError, ValueError)):
        FACET_SCALAR_CODEC.encode(["not", "scalar"])  # type: ignore[arg-type]


def test_json_pointer_resolves_escaped_keys_arrays_and_missing_paths() -> None:
    source = {
        "a/b": {"til~de": ["zero", {"value": 42}]},
        "": "empty-key",
    }

    assert resolve_json_pointer(source, "") is source
    assert resolve_json_pointer(source, "/a~1b/til~0de/1/value") == 42
    assert resolve_json_pointer(source, "/") == "empty-key"
    with pytest.raises(KeyError):
        resolve_json_pointer(source, "/a~1b/til~0de/01")
    with pytest.raises(KeyError):
        resolve_json_pointer(source, "/missing")
    for invalid in ("relative", "/bad~", "/bad~2escape"):
        with pytest.raises(ValueError):
            resolve_json_pointer(source, invalid)


def test_projection_supports_all_modes_without_flattening_objects() -> None:
    policy = MetadataProjectionPolicy(
        (
            MetadataProjection("/title", "canonical_title"),
            MetadataProjection("/status", "facet", "status"),
            MetadataProjection("/tags", "facet_many", "tag"),
            MetadataProjection("/mixed", "facet_many", "mixed"),
            MetadataProjection("/aliases", "lexical_text"),
            MetadataProjection("/opaque", "store_only"),
            MetadataProjection("/ignored", "ignore"),
        )
    )
    result = policy.project(
        {
            "title": "Projected title",
            "status": False,
            "tags": ["python", 3, 3.0, None, "python"],
            "mixed": ["safe", {"not": "a scalar"}],
            "aliases": ["MDR", "Rack", "MDR"],
            "opaque": {"nested": True},
            "ignored": "private",
        },
        fallback_title="Fallback",
    )

    assert result.canonical_title == "Projected title"
    assert [(item.namespace, FACET_SCALAR_CODEC.decode(item.value)) for item in result.facets] == [
        ("status", False),
        ("tag", "python"),
        ("tag", 3),
        ("tag", 3.0),
        ("tag", None),
    ]
    assert result.lexical_values == ("MDR", "Rack")
    assert result.policy_fingerprint == policy.fingerprint
    assert policy.project({}, fallback_title="Fallback").canonical_title == "Fallback"


def test_metadata_config_validates_projection_contract_and_loads_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[metadata]
invalid_policy = "fail_resource"

[[metadata.projections]]
path = "/project/name"
mode = "facet"
namespace = "project"

[[metadata.projections]]
path = "/aliases"
mode = "lexical_text"
""",
        encoding="utf-8",
    )
    config = load_config(toml_path=config_path)
    policy = metadata_projection_policy_from_config(config.metadata)

    assert config.metadata.invalid_policy == "fail_resource"
    assert [(item.path, item.mode, item.namespace) for item in policy.projections] == [
        ("/project/name", "facet", "project"),
        ("/aliases", "lexical_text", None),
    ]
    with pytest.raises(ValidationError, match="require a namespace"):
        MetadataProjectionConfig(path="/status", mode="facet")
    with pytest.raises(ValidationError, match="invalid JSON Pointer escape"):
        MetadataProjectionConfig(path="/bad~2path", mode="store_only")
    with pytest.raises(ValidationError, match="must be unique"):
        MetadataConfig(
            projections=[
                MetadataProjectionConfig(path="/same", mode="store_only"),
                MetadataProjectionConfig(path="/same", mode="ignore"),
            ]
        )
