from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError
from enum import StrEnum
from io import StringIO

import pytest

from mdrack_core.domain import DegradationCategory, ErrorCategory
from mdrack_core.observability import (
    CORE_EVENT_NAMES,
    REDACTED,
    LifecycleStatus,
    SafeEvent,
    SafeFingerprint,
    emit_event,
    safe_fingerprint,
)

SENTINELS = (
    "PRIVATE_QUERY_SENTINEL",
    "PRIVATE_CONTENT_SENTINEL",
    "/private/root/notes/secret.md",
    "vault-private-root",
    "https://secret-host.invalid:1234/private/path",
    "secret-host.invalid",
    "[0.123456, 0.654321]",
    "PRIVATE_PROVIDER_BODY_SENTINEL",
    "PRIVATE_EXCEPTION_SENTINEL",
    "PRIVATE_METADATA_SENTINEL",
    "PRIVATE_FACET_SENTINEL",
)


def capture(event: SafeEvent) -> str:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger(f"mdrack-core-test-{id(event)}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        emit_event(logger, event)
    finally:
        logger.removeHandler(handler)
    return stream.getvalue()


@pytest.mark.parametrize("sentinel", SENTINELS)
def test_plain_private_strings_are_redacted_from_every_safe_string_field(sentinel: str) -> None:
    event = SafeEvent(
        "core.search.branch.degraded",
        {
            "request_id": sentinel,
            "operation": sentinel,
            "status": sentinel,
            "reason": sentinel,
            "resource_kind": sentinel,
            "media_type": sentinel,
            "target": sentinel,
            "adapter_name": sentinel,
        },
    )
    output = capture(event)

    assert sentinel not in output
    assert output.count(REDACTED) == 8


@pytest.mark.parametrize(
    "private_value",
    [
        RuntimeError("PRIVATE_EXCEPTION_SENTINEL"),
        [0.123456, 0.654321],
        {"query": "PRIVATE_QUERY_SENTINEL"},
        ("PRIVATE_CONTENT_SENTINEL",),
        b"PRIVATE_PROVIDER_BODY_SENTINEL",
        object(),
    ],
)
def test_complex_private_values_are_redacted_without_stringification(private_value: object) -> None:
    output = capture(SafeEvent("core.index.failed", {"reason": private_value}))

    assert REDACTED in output
    for sentinel in SENTINELS:
        assert sentinel not in output
    assert "RuntimeError" not in output


def test_safe_enum_numeric_and_fingerprint_values_remain_observable() -> None:
    private = "https://secret-host.invalid:1234/private/path"
    event = SafeEvent(
        "core.search.completed",
        {
            "status": LifecycleStatus.COMPLETED,
            "category": DegradationCategory.ADAPTER_TIMEOUT,
            "reason": ErrorCategory.ADAPTER_ERROR,
            "result_count": 3,
            "elapsed_ms": 1.25,
            "branch_fingerprint": safe_fingerprint(private),
        },
    )
    output = capture(event)

    assert '"status":"completed"' in output
    assert '"category":"adapter_timeout"' in output
    assert '"reason":"adapter_error"' in output
    assert '"result_count":3' in output
    assert '"elapsed_ms":1.25' in output
    assert "sha256:" in output
    assert private not in output


def test_safe_event_schema_and_values_fail_closed() -> None:
    assert len(CORE_EVENT_NAMES) == 12
    with pytest.raises(ValueError, match="frozen core event"):
        SafeEvent("PRIVATE_QUERY_SENTINEL", {})
    with pytest.raises(ValueError, match="outside the safe event schema"):
        SafeEvent("core.search.started", {"query": "PRIVATE_QUERY_SENTINEL"})
    with pytest.raises(ValueError, match="finite"):
        SafeEvent("core.search.completed", {"elapsed_ms": float("nan")})
    with pytest.raises(ValueError, match="mapping"):
        SafeEvent("core.search.started", ["PRIVATE_QUERY_SENTINEL"])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="sha256 fingerprint"):
        SafeFingerprint("sha256:" + "PRIVATE_EXCEPTION_SENTINEL".ljust(64, "x"))


def test_unowned_string_enums_cannot_bypass_redaction() -> None:
    class UnsafeLabel(StrEnum):
        VALUE = "PRIVATE_QUERY_SENTINEL"

    output = capture(SafeEvent("core.search.failed", {"reason": UnsafeLabel.VALUE}))
    assert "PRIVATE_QUERY_SENTINEL" not in output
    assert REDACTED in output


def test_event_fields_are_copied_sorted_and_immutable() -> None:
    supplied: dict[str, object] = {"result_count": 1, "status": LifecycleStatus.COMPLETED}
    event = SafeEvent("core.search.completed", supplied)
    supplied["result_count"] = 99

    assert event.fields["result_count"] == 1
    assert tuple(event.fields) == ("result_count", "status")
    with pytest.raises(TypeError):
        event.fields["result_count"] = 2  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        event.name = "core.search.failed"  # type: ignore[misc]


def test_emit_event_rejects_unredacted_objects() -> None:
    logger = logging.getLogger("mdrack-core-invalid-event")
    with pytest.raises(ValueError, match="SafeEvent"):
        emit_event(logger, object())  # type: ignore[arg-type]
