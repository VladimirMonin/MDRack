"""Pure metadata projection, JSON Pointer resolution, and typed facet values."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast
from urllib.parse import quote, unquote

from mdrack.domain.blocks import JSONValue
from mdrack_core.domain import Facet

MetadataScalar: TypeAlias = str | int | float | bool | None
MetadataProjectionMode = Literal[
    "store_only",
    "canonical_title",
    "facet",
    "facet_many",
    "lexical_text",
    "ignore",
]

_METADATA_PROJECTION_VERSION = "metadata-projection-v1"
_MISSING = object()
_SCALAR_PREFIXES = frozenset({"s", "i", "f", "b", "z"})


class FacetScalarCodec:
    """Canonical reversible codec for scalar values stored in string facets."""

    @staticmethod
    def encode(value: MetadataScalar) -> str:
        if value is None:
            return "z:null"
        if isinstance(value, bool):
            return "b:true" if value else "b:false"
        if type(value) is int:
            return f"i:{value}"
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("facet floats must be finite")
            payload = json.dumps(value, allow_nan=False, separators=(",", ":"))
            return f"f:{payload}"
        if isinstance(value, str):
            try:
                return "s:" + quote(value, safe="", encoding="utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise ValueError("facet strings must be UTF-8 encodable") from exc
        raise TypeError("facet values must be string, integer, finite float, boolean, or null")

    @classmethod
    def decode(cls, encoded: str) -> MetadataScalar:
        if not isinstance(encoded, str) or len(encoded) < 2 or encoded[1] != ":":
            raise ValueError("facet value is not typed-scalar-v1")
        prefix, payload = encoded[0], encoded[2:]
        if prefix not in _SCALAR_PREFIXES:
            raise ValueError("facet value has an unknown scalar type")
        if prefix == "z":
            if payload != "null":
                raise ValueError("null facet value is not canonical")
            return None
        if prefix == "b":
            if payload not in {"true", "false"}:
                raise ValueError("boolean facet value is not canonical")
            return payload == "true"
        if prefix == "i":
            try:
                integer_value = int(payload)
            except ValueError as exc:
                raise ValueError("integer facet value is invalid") from exc
            if str(integer_value) != payload:
                raise ValueError("integer facet value is not canonical")
            return integer_value
        if prefix == "f":
            try:
                float_value = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ValueError("float facet value is invalid") from exc
            if not isinstance(float_value, float) or not math.isfinite(float_value):
                raise ValueError("float facet value is invalid")
            if cls.encode(float_value) != encoded:
                raise ValueError("float facet value is not canonical")
            return float_value
        try:
            string_value = unquote(payload, encoding="utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("string facet value is invalid UTF-8") from exc
        if cls.encode(string_value) != encoded:
            raise ValueError("string facet value is not canonical")
        return string_value

    @classmethod
    def display(cls, encoded: str) -> str:
        """Return JSON scalar text suitable for an exact typed query round-trip."""

        value = cls.decode(encoded)
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    @staticmethod
    def parse_display(value: str) -> MetadataScalar:
        """Parse the same JSON scalar syntax emitted by :meth:`display`."""

        if not isinstance(value, str):
            raise TypeError("display value must be a string")
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("metadata filter value must be a JSON scalar") from exc
        if not _is_scalar(decoded):
            raise ValueError("metadata filter value must be a JSON scalar")
        scalar = cast(MetadataScalar, decoded)
        FacetScalarCodec.encode(scalar)
        return scalar


FACET_SCALAR_CODEC = FacetScalarCodec()


@dataclass(frozen=True)
class MetadataProjection:
    path: str
    mode: MetadataProjectionMode
    namespace: str | None = None

    def __post_init__(self) -> None:
        _parse_pointer(self.path)
        if self.path == "":
            raise ValueError("metadata projection path must not select the root object")
        if self.mode not in {
            "store_only",
            "canonical_title",
            "facet",
            "facet_many",
            "lexical_text",
            "ignore",
        }:
            raise ValueError("metadata projection mode is invalid")
        needs_namespace = self.mode in {"facet", "facet_many"}
        if needs_namespace and (not isinstance(self.namespace, str) or not self.namespace):
            raise ValueError("facet projections require a non-empty namespace")
        if not needs_namespace and self.namespace is not None:
            raise ValueError("namespace is only valid for facet projections")


@dataclass(frozen=True)
class MetadataProjectionResult:
    canonical_title: str | None
    facets: tuple[Facet, ...]
    lexical_values: tuple[str, ...]
    policy_fingerprint: str


@dataclass(frozen=True)
class MetadataProjectionPolicy:
    projections: tuple[MetadataProjection, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.projections, (tuple, list)) or any(
            not isinstance(item, MetadataProjection) for item in self.projections
        ):
            raise ValueError("projections must contain MetadataProjection values")
        projections = tuple(self.projections)
        paths = [item.path for item in projections]
        if len(paths) != len(set(paths)):
            raise ValueError("metadata projection paths must be unique")
        object.__setattr__(self, "projections", projections)

    @property
    def fingerprint(self) -> str:
        payload = {
            "projections": [
                {"mode": item.mode, "namespace": item.namespace, "path": item.path}
                for item in self.projections
            ],
            "version": _METADATA_PROJECTION_VERSION,
        }
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def project(
        self,
        source: Mapping[str, JSONValue],
        *,
        fallback_title: str | None = None,
    ) -> MetadataProjectionResult:
        if not isinstance(source, Mapping):
            raise TypeError("source metadata must be a mapping")
        title = fallback_title
        facets: list[Facet] = []
        lexical_values: list[str] = []
        seen_facets: set[tuple[str, str]] = set()
        seen_lexical: set[str] = set()

        for projection in self.projections:
            value = _resolve_or_missing(source, projection.path)
            if value is _MISSING or projection.mode in {"store_only", "ignore"}:
                continue
            if projection.mode == "canonical_title":
                if isinstance(value, str) and value:
                    title = value
                continue
            if projection.mode == "facet":
                if _is_scalar(value):
                    assert projection.namespace is not None
                    _append_facet(facets, seen_facets, projection.namespace, cast(MetadataScalar, value))
                continue
            if projection.mode == "facet_many":
                if isinstance(value, (list, tuple)) and all(_is_scalar(item) for item in value):
                    assert projection.namespace is not None
                    for item in value:
                        _append_facet(
                            facets,
                            seen_facets,
                            projection.namespace,
                            cast(MetadataScalar, item),
                        )
                continue
            if projection.mode == "lexical_text":
                values: Sequence[object]
                if _is_scalar(value):
                    values = (value,)
                elif isinstance(value, (list, tuple)) and all(_is_scalar(item) for item in value):
                    values = value
                else:
                    continue
                for item in values:
                    rendered = _lexical_scalar(cast(MetadataScalar, item))
                    if rendered and rendered not in seen_lexical:
                        lexical_values.append(rendered)
                        seen_lexical.add(rendered)

        return MetadataProjectionResult(
            canonical_title=title,
            facets=tuple(facets),
            lexical_values=tuple(lexical_values),
            policy_fingerprint=self.fingerprint,
        )


def metadata_projection_policy_from_config(config: object) -> MetadataProjectionPolicy:
    """Build the pure policy from a validated MetadataConfig-like object."""

    configured = getattr(config, "projections", None)
    if configured is None:
        return DEFAULT_METADATA_PROJECTION_POLICY
    projections = tuple(
        MetadataProjection(
            path=getattr(item, "path"),
            mode=getattr(item, "mode"),
            namespace=getattr(item, "namespace", None),
        )
        for item in configured
    )
    return MetadataProjectionPolicy(projections)


def resolve_json_pointer(document: object, pointer: str) -> object:
    """Resolve an RFC 6901 pointer over JSON-compatible mappings and arrays."""

    current = document
    for token in _parse_pointer(pointer):
        if isinstance(current, Mapping):
            if token not in current:
                raise KeyError(pointer)
            current = current[token]
            continue
        if isinstance(current, (list, tuple)):
            if token == "-" or not token.isdigit() or (len(token) > 1 and token.startswith("0")):
                raise KeyError(pointer)
            index = int(token)
            if index >= len(current):
                raise KeyError(pointer)
            current = current[index]
            continue
        raise KeyError(pointer)
    return current


def _resolve_or_missing(document: object, pointer: str) -> object:
    try:
        return resolve_json_pointer(document, pointer)
    except KeyError:
        return _MISSING


def _parse_pointer(pointer: str) -> tuple[str, ...]:
    if not isinstance(pointer, str):
        raise TypeError("JSON Pointer must be a string")
    if pointer == "":
        return ()
    if not pointer.startswith("/"):
        raise ValueError("JSON Pointer must be empty or start with '/'")
    return tuple(_decode_pointer_token(token) for token in pointer[1:].split("/"))


def _decode_pointer_token(token: str) -> str:
    decoded: list[str] = []
    index = 0
    while index < len(token):
        char = token[index]
        if char != "~":
            decoded.append(char)
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise ValueError("JSON Pointer contains an invalid escape")
        decoded.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(decoded)


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, bool)) or type(value) is int or (
        isinstance(value, float) and math.isfinite(value)
    )


def _append_facet(
    facets: list[Facet],
    seen: set[tuple[str, str]],
    namespace: str,
    value: MetadataScalar,
) -> None:
    encoded = FACET_SCALAR_CODEC.encode(value)
    key = (namespace, encoded)
    if key not in seen:
        facets.append(Facet(namespace, encoded))
        seen.add(key)


def _lexical_scalar(value: MetadataScalar) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


DEFAULT_METADATA_PROJECTION_POLICY = MetadataProjectionPolicy(
    (
        MetadataProjection("/title", "canonical_title"),
        MetadataProjection("/tags", "facet_many", "tag"),
        MetadataProjection("/aliases", "lexical_text"),
    )
)


__all__ = [
    "DEFAULT_METADATA_PROJECTION_POLICY",
    "FACET_SCALAR_CODEC",
    "FacetScalarCodec",
    "MetadataProjection",
    "MetadataProjectionMode",
    "MetadataProjectionPolicy",
    "MetadataProjectionResult",
    "MetadataScalar",
    "metadata_projection_policy_from_config",
    "resolve_json_pointer",
]
