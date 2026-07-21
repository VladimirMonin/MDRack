"""Strict provider-free frame-caption manifest ingestion."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from mdrack.application.manifest import MAX_MANIFEST_BYTES
from mdrack_media import (
    REPRESENTATION_FRAME_CAPTION,
    TOKEN_COUNT_ESTIMATED,
    FrameCaptionArtifact,
    FrameCaptionObservation,
    NormalizationFingerprint,
    ProducerFingerprint,
    TokenCount,
    TokenCounterFingerprint,
    frame_id,
    representation_id,
)

FRAME_CAPTION_SCHEMA = "mdrack.frame-captions.v1"
_FRAME_KEYS = frozenset({"caption", "frame_id", "metadata", "timestamp_ms"})
_ROOT_KEYS = frozenset(
    {"frames", "metadata", "normalization_fingerprint", "producer_fingerprint", "resource_id", "schema"}
)
_FORBIDDEN_METADATA_KEYS = frozenset({"frame_path", "provider_payload"})
_DEFAULT_NORMALIZATION = NormalizationFingerprint.from_payload(
    {"algorithm": "frame-caption-preserve-v1"}
)
_DEFAULT_COUNTER = TokenCounterFingerprint.from_payload(
    {"algorithm": "unicode-whitespace-v1", "version": 1}
)


class FrameCaptionManifestError(ValueError):
    """A fixed, payload-free frame manifest validation failure."""


@dataclass(frozen=True)
class FrameCaptionReadResult:
    artifact: FrameCaptionArtifact
    frame_count: int


def read_frame_captions(source: bytes) -> FrameCaptionReadResult:
    """Parse one strict JSON manifest without resolving media or frame files."""
    if not isinstance(source, bytes) or len(source) > MAX_MANIFEST_BYTES:
        raise FrameCaptionManifestError("frame_manifest_invalid")
    try:
        data = json.loads(source.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FrameCaptionManifestError("frame_manifest_invalid") from None
    if not isinstance(data, dict) or set(data) != _ROOT_KEYS:
        raise FrameCaptionManifestError("frame_manifest_invalid")
    if data.get("schema") != FRAME_CAPTION_SCHEMA:
        raise FrameCaptionManifestError("frame_manifest_invalid")
    resource_id = data.get("resource_id")
    raw_frames = data.get("frames")
    raw_metadata = data.get("metadata")
    if not isinstance(resource_id, str) or not isinstance(raw_frames, list):
        raise FrameCaptionManifestError("frame_manifest_invalid")
    if not isinstance(raw_metadata, dict):
        raise FrameCaptionManifestError("frame_manifest_invalid")
    try:
        producer = ProducerFingerprint.from_dict(data.get("producer_fingerprint"))
        normalization_value = data.get("normalization_fingerprint")
        normalization = (
            _DEFAULT_NORMALIZATION
            if normalization_value is None
            else NormalizationFingerprint.from_dict(normalization_value)
        )
        observations = tuple(
            _observation(resource_id, producer, normalization, ordinal, item)
            for ordinal, item in enumerate(raw_frames)
        )
        artifact = validate_frame_caption_artifact(
            FrameCaptionArtifact(
                resource_id=resource_id,
                representation_id=representation_id(
                    resource_id,
                    REPRESENTATION_FRAME_CAPTION,
                    producer.value,
                    normalization.value,
                ),
                representation_kind=REPRESENTATION_FRAME_CAPTION,
                observations=observations,
                producer_fingerprint=producer,
                normalization_fingerprint=normalization,
                metadata=raw_metadata,
            )
        )
    except FrameCaptionManifestError:
        raise
    except (TypeError, ValueError):
        raise FrameCaptionManifestError("frame_manifest_invalid") from None
    return FrameCaptionReadResult(artifact=artifact, frame_count=len(observations))


def validate_frame_caption_artifact(artifact: FrameCaptionArtifact) -> FrameCaptionArtifact:
    """Reject semantic duplicates and dedicated extraction/provider metadata."""
    identities = [item.observation_identity for item in artifact.observations]
    content_keys = [(item.timestamp_ms, item.caption) for item in artifact.observations]
    if len(set(identities)) != len(identities) or len(set(content_keys)) != len(content_keys):
        raise FrameCaptionManifestError("frame_manifest_duplicate")
    metadata_values = (artifact.metadata, *(item.metadata for item in artifact.observations))
    if any(_contains_forbidden_metadata(value) for value in metadata_values):
        raise FrameCaptionManifestError("frame_manifest_forbidden_metadata")
    return artifact


def _contains_forbidden_metadata(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in _FORBIDDEN_METADATA_KEYS or _contains_forbidden_metadata(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_metadata(item) for item in value)
    return False


def _observation(
    resource_id: str,
    producer: ProducerFingerprint,
    normalization: NormalizationFingerprint,
    ordinal: int,
    value: object,
) -> FrameCaptionObservation:
    if not isinstance(value, dict) or set(value) != _FRAME_KEYS:
        raise FrameCaptionManifestError("frame_manifest_invalid")
    identity = value.get("frame_id")
    timestamp_ms = value.get("timestamp_ms")
    caption = value.get("caption")
    metadata = value.get("metadata")
    if (
        not isinstance(identity, str)
        or not identity
        or type(timestamp_ms) is not int
        or timestamp_ms < 0
        or not isinstance(caption, str)
        or not caption.strip()
        or not isinstance(metadata, Mapping)
    ):
        raise FrameCaptionManifestError("frame_manifest_invalid")
    return FrameCaptionObservation(
        frame_id=frame_id(resource_id, producer.value, ordinal, timestamp_ms, identity),
        resource_id=resource_id,
        timestamp_ms=timestamp_ms,
        observation_identity=identity,
        caption=caption,
        ordinal=ordinal,
        token_count=TokenCount(
            count=len(caption.split()),
            kind=TOKEN_COUNT_ESTIMATED,
            counter_fingerprint=_DEFAULT_COUNTER,
        ),
        producer_fingerprint=producer,
        normalization_fingerprint=normalization,
        metadata=metadata,
    )


__all__ = [
    "FRAME_CAPTION_SCHEMA",
    "FrameCaptionManifestError",
    "FrameCaptionReadResult",
    "read_frame_captions",
    "validate_frame_caption_artifact",
]
