"""Typed integer-millisecond media locators projected to generic core locators."""

from __future__ import annotations

from dataclasses import dataclass

from mdrack_core import Locator

from .common import expect_keys, require_int, require_text
from .identifiers import ID_FRAME, validate_media_id

LOCATOR_TIME_SEGMENT = "time_segment"
LOCATOR_VIDEO_FRAME = "video_frame"
LOCATOR_WHOLE_MEDIA = "whole_media"
TRACK_AUDIO = "audio"
TRACK_VIDEO = "video"
TRACKS = frozenset({TRACK_AUDIO, TRACK_VIDEO})


@dataclass(frozen=True)
class TimeSegmentLocator:
    start_ms: int
    end_ms: int
    track: str = TRACK_AUDIO

    def __post_init__(self) -> None:
        require_int(self.start_ms, "start_ms")
        require_int(self.end_ms, "end_ms")
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        require_text(self.track, "track")
        if self.track not in TRACKS:
            raise ValueError("track must be audio or video")

    def to_core_locator(self) -> Locator:
        return Locator(
            kind=LOCATOR_TIME_SEGMENT,
            payload={"end_ms": self.end_ms, "start_ms": self.start_ms, "track": self.track},
        )

    def to_dict(self) -> dict[str, object]:
        return {"end_ms": self.end_ms, "start_ms": self.start_ms, "track": self.track}

    @classmethod
    def from_dict(cls, value: object) -> TimeSegmentLocator:
        data = expect_keys(value, "time segment locator", frozenset({"start_ms", "end_ms", "track"}))
        return cls(start_ms=data["start_ms"], end_ms=data["end_ms"], track=data["track"])  # type: ignore[arg-type]


@dataclass(frozen=True)
class VideoFrameLocator:
    timestamp_ms: int
    frame_id: str

    def __post_init__(self) -> None:
        require_int(self.timestamp_ms, "timestamp_ms")
        validate_media_id(self.frame_id, "frame_id", kind=ID_FRAME)

    def to_core_locator(self) -> Locator:
        return Locator(
            kind=LOCATOR_VIDEO_FRAME,
            payload={"frame_id": self.frame_id, "timestamp_ms": self.timestamp_ms},
        )

    def to_dict(self) -> dict[str, object]:
        return {"frame_id": self.frame_id, "timestamp_ms": self.timestamp_ms}

    @classmethod
    def from_dict(cls, value: object) -> VideoFrameLocator:
        data = expect_keys(value, "video frame locator", frozenset({"timestamp_ms", "frame_id"}))
        return cls(timestamp_ms=data["timestamp_ms"], frame_id=data["frame_id"])  # type: ignore[arg-type]


@dataclass(frozen=True)
class WholeMediaLocator:
    def to_core_locator(self) -> Locator:
        return Locator(kind=LOCATOR_WHOLE_MEDIA, payload={})

    def to_dict(self) -> dict[str, object]:
        return {}

    @classmethod
    def from_dict(cls, value: object) -> WholeMediaLocator:
        expect_keys(value, "whole media locator", frozenset())
        return cls()
