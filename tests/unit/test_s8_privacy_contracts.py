"""S8 LM Studio ownership and privacy-safe diagnostic contracts."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.diagnostics.doctor import DoctorFinding, DoctorReport, report_to_dict
from mdrack.eval.privacy import build_safe_diagnostic_record, scan_privacy

PRIVATE_SENTINELS = [
    "QUERY_SENTINEL",
    "CONTENT_SENTINEL",
    "private/path.md",
    "/home/private/VAULT_SENTINEL",
    "VAULT_SENTINEL",
    "http://HOST_SENTINEL:43123/private-api",
    "HOST_SENTINEL",
    "43123",
    "private-api",
    "[0.125, 0.875]",
    "METADATA_SENTINEL",
    "PROVIDER_BODY_SENTINEL",
    "PRIVATE_EXCEPTION_SENTINEL",
]


def _assert_private_sentinels_absent(payload: object) -> None:
    rendered = json.dumps(payload, ensure_ascii=False)
    assert all(sentinel not in rendered for sentinel in PRIVATE_SENTINELS)
    assert scan_privacy(payload, PRIVATE_SENTINELS).safe


def test_lmstudio_old_and_new_imports_share_one_implementation_owner() -> None:
    from mdrack.embeddings.lmstudio import LMStudioProvider as LegacyProvider
    from mdrack.integrations.lmstudio import LMStudioProvider as CanonicalProvider

    assert LegacyProvider is CanonicalProvider
    assert CanonicalProvider.__module__ == "mdrack.integrations.lmstudio.client"


def test_status_schema_contains_endpoint_booleans_but_no_endpoint_values(tmp_path: Path) -> None:
    config_dir = tmp_path / ".mdrack"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                "[paths]",
                'store = ".mdrack"',
                "",
                "[embedding]",
                'provider = "lmstudio"',
                'model = "safe-model"',
                'endpoint = "http://HOST_SENTINEL:43123/private-api"',
                "dimensions = 8",
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["--root", str(tmp_path), "status"])

    assert result.exit_code == 0, result.output
    assert result.output.count("\n") == 1
    payload = json.loads(result.output)
    assert set(payload["data"]) == {
        "generation_state",
        "files_count",
        "chunks_count",
        "embeddings_count",
        "active_profile",
        "profile_model",
        "profile_dimensions",
        "configured_model",
        "configured_dimensions",
        "endpoint_configured",
        "endpoint_profile_recorded",
        "endpoint_match",
        "schema_version",
    }
    assert payload["data"]["endpoint_configured"] is True
    assert payload["data"]["endpoint_profile_recorded"] is False
    assert payload["data"]["endpoint_match"] is None
    _assert_private_sentinels_absent(payload)


def test_doctor_serializer_fails_closed_to_allowlisted_details_and_fixed_message() -> None:
    report = DoctorReport(
        ok=False,
        findings=[
            DoctorFinding(
                severity="warning",
                code="PROFILE_CONFIG_MISMATCH",
                message="PRIVATE_EXCEPTION_SENTINEL",
                details={
                    "profile": "default",
                    "configured_model": "safe-model-a",
                    "profile_model": "safe-model-b",
                    "configured_dimensions": 8,
                    "profile_dimensions": 12,
                    "expected_endpoint": "http://HOST_SENTINEL:43123/private-api",
                    "provider_body": "PROVIDER_BODY_SENTINEL",
                    "private_exception": "PRIVATE_EXCEPTION_SENTINEL",
                },
            )
        ],
    )

    payload = report_to_dict(report)

    assert payload["findings"] == [
        {
            "severity": "warning",
            "code": "PROFILE_CONFIG_MISMATCH",
            "message": "Embedding profile metadata does not match the current configuration",
            "details": {
                "profile": "default",
                "configured_model": "safe-model-a",
                "profile_model": "safe-model-b",
                "configured_dimensions": 8,
                "profile_dimensions": 12,
            },
        }
    ]
    _assert_private_sentinels_absent(payload)


def test_safe_support_record_rejects_raw_passthrough_and_accepts_exact_schema() -> None:
    payload = build_safe_diagnostic_record(
        generated_for="release",
        status="degraded",
        checks=[
            {
                "code": "PROVIDER_CHECK",
                "status": "degraded",
                "reason_code": "provider_unavailable",
                "counts": {"attempted": 0},
                "dimensions": {"configured": 8},
                "fingerprint": "sha256:0123456789abcdef",
            }
        ],
    )
    assert set(payload) == {"schema_version", "generated_for", "status", "checks"}
    assert set(payload["checks"][0]) == {
        "code",
        "status",
        "reason_code",
        "counts",
        "dimensions",
        "fingerprint",
    }

    with pytest.raises(ValueError, match="unsupported diagnostic check fields"):
        build_safe_diagnostic_record(
            generated_for="support",
            status="failed",
            checks=[{"code": "X", "status": "failed", "provider_body": "PROVIDER_BODY_SENTINEL"}],
        )


@pytest.mark.asyncio
async def test_lmstudio_failure_logs_never_emit_endpoint_fragments(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from mdrack.integrations.lmstudio import LMStudioProvider

    fake_transport_calls = 0
    outbound_requests = 0

    class BlockedClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "BlockedClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> None:
            nonlocal fake_transport_calls
            fake_transport_calls += 1
            request = httpx.Request("POST", "http://blocked.invalid")
            raise httpx.RequestError("PRIVATE_EXCEPTION_SENTINEL", request=request)

    monkeypatch.setattr("mdrack.integrations.lmstudio.client.httpx.AsyncClient", BlockedClient)
    provider = LMStudioProvider(
        endpoint="http://HOST_SENTINEL:43123/private-api",
        model="safe-model",
        dimensions=8,
    )

    with caplog.at_level(logging.INFO), pytest.raises(Exception):
        await provider.embed(["CONTENT_SENTINEL"])

    assert fake_transport_calls == 1
    assert outbound_requests == 0
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert all(sentinel not in rendered for sentinel in PRIVATE_SENTINELS)
