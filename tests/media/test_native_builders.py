"""Native observations are not empty transcript/frame-caption artifacts."""

from dataclasses import replace

import pytest

from mdrack_core import EmbeddingSpaceRecord, Locator
from mdrack_core.application.validation import validate_prepared_batch
from mdrack_media import MediaResourceDescriptor, resource_id
from mdrack_media.native import (
    NativeAudioObservation,
    NativeBatchInput,
    NativeFrameObservation,
    NativeVideoObservation,
    build_native_audio_batch,
    build_native_frame_batch,
    build_native_video_batch,
)


def spec(kind="video"):
    return NativeBatchInput(
        MediaResourceDescriptor(
            resource_id("native-test", "clip"),
            kind,
            "video/mp4" if kind == "video" else "audio/wav",
            "native-test",
            Locator("external_record", {"ref": "synthetic"}),
        ),
        "sha256:" + "a" * 64,
        "sha256:" + "b" * 64,
        EmbeddingSpaceRecord("native-space", 2, "cosine", "native-fingerprint"),
        2000,
        250,
        1750,
    )


@pytest.mark.parametrize("kind", ["frames", "audio", "video"])
def test_native_builders_nullable_deterministic_and_coordinates(kind):
    value = spec("audio" if kind == "audio" else "video")
    if kind == "frames":
        observations = (
            NativeFrameObservation(300, "sha256:" + "c" * 64, (1.0, 0.0)),
            NativeFrameObservation(1250, "sha256:" + "d" * 64, (0.0, 1.0)),
        )
        build = build_native_frame_batch
    elif kind == "audio":
        observations = (
            NativeAudioObservation(250, 1000, "sha256:" + "c" * 64, (1.0, 0.0)),
            NativeAudioObservation(750, 1750, "sha256:" + "d" * 64, (0.0, 1.0)),
        )
        build = build_native_audio_batch
    else:
        observations = (
            NativeVideoObservation(250, 1000, "sha256:" + "c" * 64, (1.0, 0.0)),
            NativeVideoObservation(750, 1750, "sha256:" + "d" * 64, (0.0, 1.0)),
        )
        build = build_native_video_batch
    batch = build(value, observations)
    validate_prepared_batch(batch)
    assert batch == build(value, observations)
    assert len(batch.representations) == 1 and len(batch.units) == len(batch.vectors) == 2
    assert all(r.text is None for r in batch.representations)
    assert all(u.text is None for u in batch.units)
    assert batch.spaces == (value.space,)
    assert batch.resource.content_hash == value.source_revision
    assert batch != build(replace(value, recipe_fingerprint="sha256:" + "e" * 64), observations)
    assert batch.units[0].evidence_locator.payload.get("timestamp_ms", 250) == (300 if kind == "frames" else 250)


def test_native_builder_refuses_empty_wrong_kind_bounds_dimensions_nonfinite_and_zero():
    value = spec()
    for obs in [
        (),
        (NativeFrameObservation(2000, "sha256:" + "c" * 64, (1.0, 0.0)),),
        (NativeFrameObservation(300, "sha256:" + "c" * 64, (1.0,)),),
        (NativeFrameObservation(300, "sha256:" + "c" * 64, (float("nan"), 0.0)),),
        (NativeFrameObservation(300, "sha256:" + "c" * 64, (0.0, 0.0)),),
    ]:
        with pytest.raises(ValueError):
            build_native_frame_batch(value, obs)
    frame = NativeFrameObservation(300, "sha256:" + "c" * 64, (1.0, 0.0))
    with pytest.raises(ValueError):
        build_native_frame_batch(value, (frame, frame))
    with pytest.raises(ValueError):
        build_native_frame_batch(spec("audio"), (frame,))
    with pytest.raises(ValueError):
        NativeAudioObservation(True, 10, "sha256:" + "c" * 64, (1.0, 0.0))
    with pytest.raises(ValueError):
        NativeVideoObservation(10, 10, "sha256:" + "c" * 64, (1.0, 0.0))
