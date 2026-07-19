"""Reusable cross-surface privacy and evidence oracle for offline tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from mdrack.eval.privacy import PrivacyScanResult, scan_privacy


@dataclass(frozen=True)
class SentinelMatrix:
    """Distinct private values and recursive keys used by evidence tests."""

    values: dict[str, str]
    forbidden_keys: tuple[str, ...]

    @classmethod
    def complete(cls) -> SentinelMatrix:
        return cls(
            values={
                "content": "PRIVATE_CONTENT_SENTINEL_Q2",
                "title": "PRIVATE_TITLE_SENTINEL_Q2",
                "relative_path": "private/course/lesson-q2.md",
                "absolute_path": "/home/private/PRIVATE_ROOT_SENTINEL_Q2/lesson.md",
                "root": "PRIVATE_ROOT_SENTINEL_Q2",
                "url": "https://PRIVATE_HOST_SENTINEL_Q2.invalid:43123/private-api",
                "vector": "[0.812345,0.187655]",
                "metadata": "PRIVATE_METADATA_SENTINEL_Q2",
                "facet": "PRIVATE_FACET_SENTINEL_Q2",
                "locator": "PRIVATE_LOCATOR_SENTINEL_Q2",
                "provider": "PRIVATE_PROVIDER_SENTINEL_Q2",
                "provider_body": "PRIVATE_PROVIDER_BODY_SENTINEL_Q2",
                "credential": "Authorization: Bearer PRIVATE_CREDENTIAL_SENTINEL_Q2",
                "exception": "PRIVATE_EXCEPTION_SENTINEL_Q2",
                "chained_exception": "PRIVATE_CHAINED_EXCEPTION_SENTINEL_Q2",
            },
            forbidden_keys=(
                "PRIVATE_METADATA_KEY_SENTINEL_Q2",
                "PRIVATE_FACET_KEY_SENTINEL_Q2",
                "PRIVATE_LOCATOR_KEY_SENTINEL_Q2",
                "PRIVATE_PROVIDER_KEY_SENTINEL_Q2",
                "PRIVATE_CREDENTIAL_KEY_SENTINEL_Q2",
            ),
        )

    @property
    def forbidden_values(self) -> list[str]:
        return list(self.values.values())

    @property
    def all_tokens(self) -> tuple[str, ...]:
        return (*self.values.values(), *self.forbidden_keys)


class EvidenceOracle:
    """Fail closed when any private sentinel reaches a captured evidence surface."""

    SURFACES: ClassVar[frozenset[str]] = frozenset(
        {"api", "cache", "cli_stderr", "cli_stdout", "eval", "log", "provider"}
    )

    def __init__(self, sentinels: SentinelMatrix) -> None:
        self._sentinels = sentinels
        self._captured: dict[str, list[object]] = {
            surface: [] for surface in sorted(self.SURFACES)
        }

    def capture(self, surface: str, payload: object) -> None:
        if surface not in self.SURFACES:
            raise ValueError("surface must be a frozen evidence surface")
        self._captured[surface].append(payload)

    def scan(self) -> dict[str, PrivacyScanResult]:
        return {
            surface: scan_privacy(
                payloads,
                forbidden_values=self._sentinels.forbidden_values,
                forbidden_keys=list(self._sentinels.forbidden_keys),
            )
            for surface, payloads in self._captured.items()
        }

    def assert_safe(self) -> None:
        unsafe = [surface for surface, result in self.scan().items() if not result.safe]
        if unsafe:
            raise AssertionError(
                "private evidence reached frozen surface(s): " + ",".join(sorted(unsafe))
            )


def exception_chain_payload(error: BaseException) -> list[dict[str, str]]:
    """Return raw exception evidence for negative oracle tests, including chains."""
    payload: list[dict[str, str]] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        payload.append(
            {
                "type": type(current).__name__,
                "message": str(current),
                "repr": repr(current),
            }
        )
        current = current.__cause__ or current.__context__
    return payload
