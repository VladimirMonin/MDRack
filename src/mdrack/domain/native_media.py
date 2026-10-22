"""Explicit finite temporal preparation recipe; no inferred full-media coverage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

NativeMediaKind = Literal["frames", "audio", "video"]


@dataclass(frozen=True)
class NativeMediaPolicy:
    start_ms: int = 0
    end_ms: int | None = None
    window_ms: int = 1000
    hop_ms: int = 1000
    frame_step_ms: int = 1000
    max_units: int = 128
    max_source_bytes: int = 33_554_432
    max_duration_ms: int = 60_000
    max_prepared_bytes: int = 16_777_216
    max_width: int = 640
    max_height: int = 640
    timeout_seconds: int = 60

    def __post_init__(self) -> None:
        if type(self.start_ms) is not int or self.start_ms < 0:
            raise ValueError("native_range_invalid")
        if self.end_ms is not None and (type(self.end_ms) is not int or self.end_ms <= self.start_ms):
            raise ValueError("native_range_invalid")
        for key, value in asdict(self).items():
            if key not in {"start_ms", "end_ms"} and (type(value) is not int or value < 1):
                raise ValueError("native_budget_invalid")
        if self.max_units > 10000 or self.max_width > 4096 or self.max_height > 4096:
            raise ValueError("native_budget_invalid")

    @property
    def fingerprint(self) -> str:
        return (
            "sha256:"
            + hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        )

    def range(self, duration_ms: int) -> tuple[int, int]:
        end = duration_ms if self.end_ms is None else self.end_ms
        if duration_ms > self.max_duration_ms:
            raise ValueError("native_duration_budget_exceeded")
        if not 0 <= self.start_ms < end <= duration_ms:
            raise ValueError("native_range_invalid")
        return self.start_ms, end

    def windows(self, duration_ms: int) -> tuple[tuple[int, int], ...]:
        start, end = self.range(duration_ms)
        count = (end - start + self.hop_ms - 1) // self.hop_ms
        if count > self.max_units:
            raise ValueError("native_unit_budget_exceeded")
        return tuple((s, min(s + self.window_ms, end)) for s in range(start, end, self.hop_ms))
