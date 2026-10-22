"""Pure native observations → ready vector-only graphs. No decoding/provider/storage I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mdrack_core import (
    EmbeddingSpaceRecord,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)
from mdrack_core.application.validation import validate_prepared_batch

from .builders import MediaResourceDescriptor
from .identifiers import frame_id, representation_id, stable_media_id
from .locators import TimeSegmentLocator, VideoFrameLocator


def _digest(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError("native_fingerprint_invalid")


def _integer(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("native_coordinate_invalid")


@dataclass(frozen=True)
class NativeFrameObservation:
    timestamp_ms: int
    input_fingerprint: str
    vector: tuple[float, ...] = field(repr=False)

    def __post_init__(self) -> None:
        _integer(self.timestamp_ms)
        _digest(self.input_fingerprint)
        if type(self.vector) is not tuple:
            raise ValueError("native_vector_invalid")


@dataclass(frozen=True)
class _NativeSegmentObservation:
    start_ms: int
    end_ms: int
    input_fingerprint: str
    vector: tuple[float, ...] = field(repr=False)

    def __post_init__(self) -> None:
        _integer(self.start_ms)
        _integer(self.end_ms)
        _digest(self.input_fingerprint)
        if self.end_ms <= self.start_ms or type(self.vector) is not tuple:
            raise ValueError("native_segment_invalid")


@dataclass(frozen=True)
class NativeAudioObservation(_NativeSegmentObservation):
    """A vector actually encoded from a bounded audio signal, never transcript text."""


@dataclass(frozen=True)
class NativeVideoObservation(_NativeSegmentObservation):
    """A vector actually encoded from a direct video clip, not a frame aggregate."""


@dataclass(frozen=True)
class NativeBatchInput:
    resource: MediaResourceDescriptor
    source_revision: str
    recipe_fingerprint: str
    space: EmbeddingSpaceRecord
    duration_ms: int
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.resource, MediaResourceDescriptor) or not isinstance(self.space, EmbeddingSpaceRecord):
            raise ValueError("native_batch_input_invalid")
        _digest(self.source_revision)
        _digest(self.recipe_fingerprint)
        for v in (self.duration_ms, self.start_ms, self.end_ms):
            _integer(v)
        if not 0 <= self.start_ms < self.end_ms <= self.duration_ms:
            raise ValueError("native_range_invalid")


def _build(
    value: NativeBatchInput,
    observations: tuple[NativeFrameObservation | NativeAudioObservation | NativeVideoObservation, ...],
    kind: str,
) -> PreparedResourceBatch:
    if not isinstance(value, NativeBatchInput) or type(observations) is not tuple or not observations:
        raise ValueError("native_observations_required")
    descriptor = value.resource
    if kind in {"frames", "video"} and descriptor.resource_kind != "video":
        raise ValueError("native_resource_kind_invalid")
    if kind == "audio" and descriptor.resource_kind not in {"audio", "video"}:
        raise ValueError("native_resource_kind_invalid")
    modality = "image" if kind == "frames" else kind
    representation_kind = {"frames": "native_video_frames", "audio": "native_audio", "video": "native_video"}[kind]
    rid = descriptor.resource_id
    rep_id = representation_id(
        rid, representation_kind, value.recipe_fingerprint, value.source_revision + ":" + value.space.fingerprint
    )
    rep = RepresentationRecord(
        rep_id,
        rid,
        representation_kind,
        modality,
        producer_fingerprint=value.recipe_fingerprint,
        metadata={"recipe_fingerprint": value.recipe_fingerprint},
    )
    units = []
    vectors = []
    previous: tuple[int, ...] | None = None
    for ordinal, observation in enumerate(observations):
        if (
            (kind == "frames" and not isinstance(observation, NativeFrameObservation))
            or (kind == "audio" and not isinstance(observation, NativeAudioObservation))
            or (kind == "video" and not isinstance(observation, NativeVideoObservation))
        ):
            raise ValueError("native_observation_kind_invalid")
        coordinate: tuple[int, ...]
        if isinstance(observation, NativeFrameObservation):
            if not value.start_ms <= observation.timestamp_ms < value.end_ms:
                raise ValueError("native_observation_outside_range")
            coordinate = (observation.timestamp_ms,)
            uid = frame_id(
                rid,
                value.recipe_fingerprint,
                ordinal,
                observation.timestamp_ms,
                observation.input_fingerprint + value.source_revision + value.space.fingerprint,
            )
            locator = VideoFrameLocator(observation.timestamp_ms, uid).to_core_locator()
        else:
            if not value.start_ms <= observation.start_ms < observation.end_ms <= value.end_ms:
                raise ValueError("native_observation_outside_range")
            coordinate = (observation.start_ms, observation.end_ms)
            uid = stable_media_id("passage", [rid, rep_id, *coordinate, observation.input_fingerprint])
            locator = TimeSegmentLocator(observation.start_ms, observation.end_ms, kind).to_core_locator()
        if previous is not None and coordinate <= previous:
            raise ValueError("native_observation_order_invalid")
        previous = coordinate
        units.append(
            SearchUnitRecord(
                uid,
                rid,
                rep_id,
                "frame" if kind == "frames" else "time_segment",
                modality,
                None,
                locator,
                ordinal,
                metadata={
                    "representation_kind": representation_kind,
                    "input_fingerprint": observation.input_fingerprint,
                },
            )
        )
        vectors.append(VectorRecord(uid, value.space.space_id, observation.vector))
    resource = ResourceRecord(
        rid,
        descriptor.resource_kind,
        descriptor.media_type,
        descriptor.source_namespace,
        descriptor.locator,
        value.source_revision,
        metadata={"source_revision": value.source_revision},
    )
    batch = PreparedResourceBatch(resource, (rep,), tuple(units), (value.space,), tuple(vectors))
    validate_prepared_batch(batch)
    return batch


def build_native_frame_batch(
    value: NativeBatchInput, observations: tuple[NativeFrameObservation, ...]
) -> PreparedResourceBatch:
    return _build(value, observations, "frames")


def build_native_audio_batch(
    value: NativeBatchInput, observations: tuple[NativeAudioObservation, ...]
) -> PreparedResourceBatch:
    return _build(value, observations, "audio")


def build_native_video_batch(
    value: NativeBatchInput, observations: tuple[NativeVideoObservation, ...]
) -> PreparedResourceBatch:
    return _build(value, observations, "video")
