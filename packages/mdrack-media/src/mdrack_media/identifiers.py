"""Deterministic logical media identifiers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from .common import canonical_json, require_int, require_text

ID_RESOURCE = "resource"
ID_REPRESENTATION = "representation"
ID_ATOM = "atom"
ID_PASSAGE = "passage"
ID_FRAME = "frame"
ID_WHOLE = "whole"
ID_KINDS = frozenset(
    {ID_RESOURCE, ID_REPRESENTATION, ID_ATOM, ID_PASSAGE, ID_FRAME, ID_WHOLE}
)
_ID_PATTERN = re.compile(r"(resource|representation|atom|passage|frame|whole)_[0-9a-f]{64}")


def validate_media_id(value: object, field_name: str, *, kind: str | None = None) -> str:
    """Validate one canonical logical ID and optional required kind."""
    identifier = require_text(value, field_name)
    match = _ID_PATTERN.fullmatch(identifier)
    if match is None or (kind is not None and match.group(1) != kind):
        expected = kind or "media"
        raise ValueError(f"{field_name} must be a canonical {expected} ID")
    return identifier


def stable_media_id(kind: str, parts: Sequence[object]) -> str:
    """Create a framed deterministic ID without exposing its input parts."""
    if kind not in ID_KINDS:
        raise ValueError("kind must be a frozen media ID kind")
    if not isinstance(parts, (list, tuple)) or not parts:
        raise ValueError("parts must be a non-empty sequence")
    payload = {"contract": "mdrack.media-id.v1", "kind": kind, "parts": list(parts)}
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{kind}_{digest}"


def resource_id(source_namespace: str, source_identity: str) -> str:
    return stable_media_id(
        ID_RESOURCE,
        [require_text(source_namespace, "source_namespace"), require_text(source_identity, "source_identity")],
    )


def representation_id(
    resource_identifier: str,
    representation_kind: str,
    producer_fingerprint: str,
    normalization_fingerprint: str,
) -> str:
    return stable_media_id(
        ID_REPRESENTATION,
        [
            validate_media_id(resource_identifier, "resource_id", kind=ID_RESOURCE),
            require_text(representation_kind, "representation_kind"),
            require_text(producer_fingerprint, "producer_fingerprint"),
            require_text(normalization_fingerprint, "normalization_fingerprint"),
        ],
    )


def atom_id(resource_identifier: str, producer_fingerprint: str, ordinal: int) -> str:
    return stable_media_id(
        ID_ATOM,
        [
            validate_media_id(resource_identifier, "resource_id", kind=ID_RESOURCE),
            require_text(producer_fingerprint, "producer_fingerprint"),
            require_int(ordinal, "ordinal"),
        ],
    )


def passage_id(
    resource_identifier: str,
    grouper_fingerprint: str,
    ordinal: int,
    first_atom_id: str,
    last_atom_id: str,
    start_ms: int,
    end_ms: int,
    text_digest: str,
) -> str:
    return stable_media_id(
        ID_PASSAGE,
        [
            validate_media_id(resource_identifier, "resource_id", kind=ID_RESOURCE),
            require_text(grouper_fingerprint, "grouper_fingerprint"),
            require_int(ordinal, "ordinal"),
            validate_media_id(first_atom_id, "first_atom_id", kind=ID_ATOM),
            validate_media_id(last_atom_id, "last_atom_id", kind=ID_ATOM),
            require_int(start_ms, "start_ms"),
            require_int(end_ms, "end_ms"),
            require_text(text_digest, "text_digest"),
        ],
    )


def frame_id(
    resource_identifier: str,
    producer_fingerprint: str,
    ordinal: int,
    timestamp_ms: int,
    observation_identity: str,
) -> str:
    return stable_media_id(
        ID_FRAME,
        [
            validate_media_id(resource_identifier, "resource_id", kind=ID_RESOURCE),
            require_text(producer_fingerprint, "producer_fingerprint"),
            require_int(ordinal, "ordinal"),
            require_int(timestamp_ms, "timestamp_ms"),
            require_text(observation_identity, "observation_identity"),
        ],
    )


def whole_resource_id(
    resource_identifier: str,
    representation_identifier: str,
    aggregation_fingerprint: str,
) -> str:
    return stable_media_id(
        ID_WHOLE,
        [
            validate_media_id(resource_identifier, "resource_id", kind=ID_RESOURCE),
            validate_media_id(
                representation_identifier,
                "representation_id",
                kind=ID_REPRESENTATION,
            ),
            require_text(aggregation_fingerprint, "aggregation_fingerprint"),
        ],
    )
