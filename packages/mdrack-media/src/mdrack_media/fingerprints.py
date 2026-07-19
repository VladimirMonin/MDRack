"""Typed, non-interchangeable media pipeline fingerprints."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Self

from .common import canonical_json, freeze_metadata

_FINGERPRINT_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class _Fingerprint:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or _FINGERPRINT_PATTERN.fullmatch(self.value) is None:
            raise ValueError("value must be a lowercase sha256 fingerprint")

    @classmethod
    def from_payload(cls, payload: object) -> Self:
        """Fingerprint one canonical JSON policy/configuration payload."""
        encoded = canonical_json(freeze_metadata(payload, "payload")).encode("utf-8")
        return cls(f"sha256:{hashlib.sha256(encoded).hexdigest()}")

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, str):
            raise ValueError("fingerprint must be a string")
        return cls(value)

    def to_dict(self) -> str:
        return self.value


class ProducerFingerprint(_Fingerprint):
    """Identity of the artifact producer and its effective configuration."""


class NormalizationFingerprint(_Fingerprint):
    """Identity of text normalization policy."""


class GrouperFingerprint(_Fingerprint):
    """Identity of timed grouping algorithm and policy."""


class TokenCounterFingerprint(_Fingerprint):
    """Identity of the exact tokenizer or deterministic estimate."""


class AggregationFingerprint(_Fingerprint):
    """Identity of whole-resource aggregation policy."""


class EmbeddingFingerprint(_Fingerprint):
    """Identity of the caller-owned embedding space preparation."""
