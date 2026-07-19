"""Pure media extension protocols."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .fingerprints import TokenCounterFingerprint


@runtime_checkable
class TokenCounter(Protocol):
    """Caller-owned exact tokenizer or deterministic estimate."""

    @property
    def fingerprint(self) -> TokenCounterFingerprint:
        ...

    def count(self, text: str) -> int:
        ...
