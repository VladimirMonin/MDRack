"""Pure media policy records; no policy is executed in this package slice."""

from __future__ import annotations

from dataclasses import dataclass

from .common import expect_keys, require_int, require_text

OVERFLOW_REJECT = "reject"
OVERFLOW_CALLER_SPLIT = "caller_split"
OVERFLOW_POLICIES = frozenset({OVERFLOW_REJECT, OVERFLOW_CALLER_SPLIT})


@dataclass(frozen=True)
class TimedChunkingPolicy:
    soft_min_tokens: int = 180
    target_tokens: int = 320
    soft_max_tokens: int = 480
    hard_max_tokens: int = 800
    soft_min_duration_ms: int = 20_000
    target_duration_ms: int = 60_000
    soft_max_duration_ms: int = 90_000
    hard_max_duration_ms: int = 120_000
    medium_pause_ms: int = 700
    strong_pause_ms: int = 1_500
    overlap_atoms: int = 0

    def __post_init__(self) -> None:
        token_limits = (
            self.soft_min_tokens,
            self.target_tokens,
            self.soft_max_tokens,
            self.hard_max_tokens,
        )
        duration_limits = (
            self.soft_min_duration_ms,
            self.target_duration_ms,
            self.soft_max_duration_ms,
            self.hard_max_duration_ms,
        )
        if any(require_int(item, "token limit", minimum=1) < 1 for item in token_limits):
            raise AssertionError("unreachable")
        if any(require_int(item, "duration limit", minimum=1) < 1 for item in duration_limits):
            raise AssertionError("unreachable")
        if tuple(sorted(token_limits)) != token_limits:
            raise ValueError("token limits must be non-decreasing")
        if tuple(sorted(duration_limits)) != duration_limits:
            raise ValueError("duration limits must be non-decreasing")
        require_int(self.medium_pause_ms, "medium_pause_ms")
        require_int(self.strong_pause_ms, "strong_pause_ms")
        if self.strong_pause_ms < self.medium_pause_ms:
            raise ValueError("strong_pause_ms must be greater than or equal to medium_pause_ms")
        require_int(self.overlap_atoms, "overlap_atoms")

    def to_dict(self) -> dict[str, int]:
        return {
            "hard_max_duration_ms": self.hard_max_duration_ms,
            "hard_max_tokens": self.hard_max_tokens,
            "medium_pause_ms": self.medium_pause_ms,
            "overlap_atoms": self.overlap_atoms,
            "soft_max_duration_ms": self.soft_max_duration_ms,
            "soft_max_tokens": self.soft_max_tokens,
            "soft_min_duration_ms": self.soft_min_duration_ms,
            "soft_min_tokens": self.soft_min_tokens,
            "strong_pause_ms": self.strong_pause_ms,
            "target_duration_ms": self.target_duration_ms,
            "target_tokens": self.target_tokens,
        }

    @classmethod
    def from_dict(cls, value: object) -> TimedChunkingPolicy:
        keys = frozenset(cls().to_dict())
        data = expect_keys(value, "timed chunking policy", keys)
        return cls(**data)  # type: ignore[arg-type]


@dataclass(frozen=True)
class TextNormalizationPolicy:
    version: str = "identity-v1"
    whitespace: str = "preserve"

    def __post_init__(self) -> None:
        require_text(self.version, "version")
        if self.whitespace not in {"preserve", "technical-collapse"}:
            raise ValueError("whitespace must be preserve or technical-collapse")

    def to_dict(self) -> dict[str, str]:
        return {"version": self.version, "whitespace": self.whitespace}

    @classmethod
    def from_dict(cls, value: object) -> TextNormalizationPolicy:
        data = expect_keys(value, "normalization policy", frozenset({"version", "whitespace"}))
        return cls(version=data["version"], whitespace=data["whitespace"])  # type: ignore[arg-type]


@dataclass(frozen=True)
class WholeResourceTextPolicy:
    max_tokens: int = 8_000
    overflow: str = OVERFLOW_REJECT

    def __post_init__(self) -> None:
        require_int(self.max_tokens, "max_tokens", minimum=1)
        if self.overflow not in OVERFLOW_POLICIES:
            raise ValueError("overflow must be reject or caller_split")

    def to_dict(self) -> dict[str, object]:
        return {"max_tokens": self.max_tokens, "overflow": self.overflow}

    @classmethod
    def from_dict(cls, value: object) -> WholeResourceTextPolicy:
        data = expect_keys(value, "whole resource text policy", frozenset({"max_tokens", "overflow"}))
        return cls(max_tokens=data["max_tokens"], overflow=data["overflow"])  # type: ignore[arg-type]
