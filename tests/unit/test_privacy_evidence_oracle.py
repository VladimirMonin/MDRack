"""Cross-surface privacy/evidence oracle scaffold for v0.4."""

from __future__ import annotations

import json
import logging
import socket
from io import StringIO
from typing import Any

import pytest

from mdrack.eval.privacy import (
    PrivacyViolation,
    build_safe_diagnostic_record,
    scan_privacy,
    serialize_safe_json,
)
from mdrack.eval.reporting import build_safe_eval_results, build_safe_eval_summary
from mdrack.eval.retrieval import EvalQueryResult, EvalReport
from mdrack.output.envelope import success as envelope_success
from mdrack_core.observability import SafeEvent, emit_event
from tests.privacy_oracle import EvidenceOracle, SentinelMatrix, exception_chain_payload


def _safe_eval_report(sentinels: SentinelMatrix, *, status: str) -> EvalReport:
    conditions_met = status == "success"
    error = None if conditions_met else sentinels.values["provider_body"]
    result = EvalQueryResult(
        query_id=sentinels.values["title"],
        query=sentinels.values["content"],
        mode="semantic",
        retrieved_ids=[sentinels.values["locator"]],
        expected_ids=[sentinels.values["relative_path"]],
        k=5,
        recall_at_k=1.0 if conditions_met else 0.0,
        mrr=1.0 if conditions_met else 0.0,
        precision_at_k=1.0 if conditions_met else 0.0,
        ndcg_at_k=1.0 if conditions_met else 0.0,
        conditions_met=conditions_met,
        error=error,
    )
    return EvalReport(
        results=[] if status == "empty" else [result],
        summary={
            "queries_total": 0 if status == "empty" else 1,
            "queries_successful": int(conditions_met),
            "queries_failed": int(not conditions_met),
            "queries_with_zero_gold": int(status == "empty"),
            "avg_recall_at_k": result.recall_at_k,
            "avg_mrr": result.mrr,
            "avg_precision_at_k": result.precision_at_k,
            "avg_ndcg_at_k": result.ndcg_at_k,
            sentinels.forbidden_keys[0]: sentinels.values["metadata"],
        },
    )


def _capture_safe_event(sentinels: SentinelMatrix) -> str:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("mdrack-q2-evidence-oracle")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        emit_event(
            logger,
            SafeEvent(
                "core.search.branch.degraded",
                {
                    "operation": sentinels.values["content"],
                    "status": sentinels.values["provider"],
                    "reason": sentinels.values["exception"],
                    "adapter_name": sentinels.values["url"],
                },
            ),
        )
    finally:
        logger.removeHandler(handler)
    return stream.getvalue()


def test_cross_surface_oracle_accepts_only_sanitized_branch_evidence() -> None:
    sentinels = SentinelMatrix.complete()
    oracle = EvidenceOracle(sentinels)
    report = _safe_eval_report(sentinels, status="provider")
    safe_results = build_safe_eval_results(report)
    safe_summary = build_safe_eval_summary(report)
    diagnostic = build_safe_diagnostic_record(
        generated_for="support",
        status="degraded",
        checks=[
            {
                "code": "PROVIDER_CHECK",
                "status": "degraded",
                "reason_code": "provider_unavailable",
                "counts": {"attempted": 0},
            }
        ],
    )
    public_api = {"resource_id": "public-resource-1", "status": "ok"}
    cli_payload = envelope_success(public_api, command="resource inspect")
    cache_text = serialize_safe_json(
        {"summary": safe_summary, "results": safe_results},
        forbidden_values=sentinels.forbidden_values,
        forbidden_keys=list(sentinels.forbidden_keys),
    )

    oracle.capture("api", public_api)
    oracle.capture("cache", json.loads(cache_text))
    oracle.capture("cli_stdout", cli_payload)
    oracle.capture("cli_stderr", "")
    oracle.capture("eval", {"summary": safe_summary, "results": safe_results})
    oracle.capture("log", _capture_safe_event(sentinels))
    oracle.capture("provider", diagnostic)

    oracle.assert_safe()
    assert all(token not in cache_text for token in sentinels.all_tokens)


@pytest.mark.parametrize(
    ("status", "reason_code"),
    (
        ("ok", None),
        ("empty", "no_records"),
        ("degraded", "provider_unavailable"),
        ("failed", "validation_failed"),
        ("failed", "storage_failed"),
        ("failed", "provider_failed"),
        ("failed", "cleanup_failed"),
        ("failed", "interrupted"),
    ),
)
def test_branch_matrix_is_safe_and_keeps_public_evidence_separate(
    status: str,
    reason_code: str | None,
) -> None:
    sentinels = SentinelMatrix.complete()
    check: dict[str, Any] = {
        "code": "Q2_BRANCH",
        "status": status,
        "counts": {"attempted": 0},
    }
    if reason_code is not None:
        check["reason_code"] = reason_code
    payload = build_safe_diagnostic_record(
        generated_for="release",
        status=status,
        checks=[check],
    )

    assert scan_privacy(
        payload,
        forbidden_values=sentinels.forbidden_values,
        forbidden_keys=list(sentinels.forbidden_keys),
    ).safe
    assert payload["checks"][0]["code"] == "Q2_BRANCH"


def test_recursive_forbidden_keys_and_values_fail_before_cache_write() -> None:
    sentinels = SentinelMatrix.complete()
    writes: list[str] = []
    unsafe = {
        "level_1": [
            {
                "level_2": {
                    sentinels.forbidden_keys[0]: sentinels.values["metadata"],
                }
            }
        ]
    }

    with pytest.raises(PrivacyViolation) as caught:
        text = serialize_safe_json(
            unsafe,
            forbidden_values=sentinels.forbidden_values,
            forbidden_keys=list(sentinels.forbidden_keys),
        )
        writes.append(text)

    assert writes == []
    assert str(caught.value) == "evidence contains private data"
    assert all(token not in str(caught.value) for token in sentinels.all_tokens)
    result = scan_privacy(
        unsafe,
        forbidden_values=sentinels.forbidden_values,
        forbidden_keys=list(sentinels.forbidden_keys),
    )
    assert {finding.category for finding in result.findings} >= {
        "forbidden_key",
        "forbidden_value",
    }
    assert all(token not in finding.location for token in sentinels.all_tokens for finding in result.findings)


def test_every_sentinel_family_and_recursive_key_is_detected_without_echo() -> None:
    sentinels = SentinelMatrix.complete()
    nested_values: object = {
        "private": [
            {"family": family, "value": value}
            for family, value in sentinels.values.items()
        ]
    }
    for key in sentinels.forbidden_keys:
        nested_values = {"next": nested_values, key: "present"}

    result = scan_privacy(
        nested_values,
        forbidden_values=sentinels.forbidden_values,
        forbidden_keys=list(sentinels.forbidden_keys),
    )

    assert result.safe is False
    assert sum(finding.category == "forbidden_value" for finding in result.findings) >= len(
        sentinels.values
    )
    assert sum(finding.category == "forbidden_key" for finding in result.findings) == len(
        sentinels.forbidden_keys
    )
    rendered_findings = json.dumps(result.to_dict(), sort_keys=True)
    assert all(token not in rendered_findings for token in sentinels.all_tokens)


def test_raw_and_chained_exceptions_are_detected_but_safe_eval_projection_drops_them() -> None:
    sentinels = SentinelMatrix.complete()
    try:
        try:
            raise RuntimeError(sentinels.values["chained_exception"])
        except RuntimeError as cause:
            raise ValueError(sentinels.values["exception"]) from cause
    except ValueError as error:
        raw_chain = exception_chain_payload(error)

    raw_result = scan_privacy(raw_chain, forbidden_values=sentinels.forbidden_values)
    assert raw_result.safe is False
    assert all(token not in finding.location for token in sentinels.all_tokens for finding in raw_result.findings)

    report = _safe_eval_report(sentinels, status="provider")
    projected = {
        "results": build_safe_eval_results(report),
        "summary": build_safe_eval_summary(report),
    }
    assert scan_privacy(
        projected,
        forbidden_values=sentinels.forbidden_values,
        forbidden_keys=list(sentinels.forbidden_keys),
    ).safe


def test_oracle_scaffold_performs_zero_offline_socket_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinels = SentinelMatrix.complete()
    attempts = 0

    def blocked(*args: object, **kwargs: object) -> None:
        nonlocal attempts
        del args, kwargs
        attempts += 1
        raise AssertionError("offline evidence oracle attempted network access")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)

    for status in ("success", "empty", "degraded", "validation", "storage", "provider", "cleanup", "interruption"):
        report = _safe_eval_report(sentinels, status=status)
        serialize_safe_json(
            {
                "results": build_safe_eval_results(report),
                "summary": build_safe_eval_summary(report),
            },
            forbidden_values=sentinels.forbidden_values,
            forbidden_keys=list(sentinels.forbidden_keys),
        )

    assert attempts == 0
