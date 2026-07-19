"""Offline S8 privacy matrix for eval, model, and generated diagnostics."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.eval.privacy import build_safe_diagnostic_record, scan_privacy
from mdrack.eval.retrieval import EvalReport
from mdrack.output.errors import EmbeddingError
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "src" / "mdrack" / "storage" / "sqlite" / "migrations"
SENTINELS = [
    "QUERY_SENTINEL",
    "CONTENT_SENTINEL",
    "/home/private/VAULT_SENTINEL",
    "http://HOST_SENTINEL:43123/private-api",
    "[0.125, 0.875]",
    "METADATA_SENTINEL",
    "PROVIDER_BODY_SENTINEL",
    "PRIVATE_EXCEPTION_SENTINEL",
]

_FIXED_CONFIG_ERROR = {
    "message": "Configuration could not be loaded",
    "code": "CONFIG_ERROR",
}
_FIXED_STATUS_ERROR = {
    "message": "Status could not be read",
    "code": "STATUS_ERROR",
}
_FIXED_DOCTOR_ERROR = {
    "message": "Diagnostics could not be completed",
    "code": "DOCTOR_ERROR",
}


def _assert_safe(payload: object) -> None:
    rendered = json.dumps(payload, ensure_ascii=False)
    assert all(value not in rendered for value in SENTINELS)
    assert scan_privacy(payload, SENTINELS).safe


def _setup_db(root: Path) -> None:
    store_dir = root / ".mdrack"
    store_dir.mkdir(exist_ok=True)
    conn = get_connection(store_dir / "knowledge.db")
    try:
        apply_migrations(conn, MIGRATIONS_DIR)
    finally:
        conn.close()


def _assert_fixed_cli_error(result: object, expected: dict[str, str]) -> dict[str, object]:
    assert getattr(result, "exit_code") == 1
    assert getattr(result, "exception") is not None
    output = getattr(result, "output")
    assert output.count("\n") == 1
    payload = json.loads(output)
    assert set(payload) == {"ok", "error", "meta"}
    assert payload["ok"] is False
    assert payload["error"] == expected
    _assert_safe(payload)
    return payload


@pytest.mark.parametrize(
    "command",
    [
        ["status"],
        ["doctor"],
        ["model", "list"],
        ["eval", "retrieval", "--queries", "unused.yaml"],
    ],
)
@pytest.mark.parametrize("config_kind", ["missing", "invalid"])
def test_explicit_config_failures_use_one_fixed_private_safe_envelope(
    command: list[str],
    config_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_path = tmp_path / "PRIVATE_EXCEPTION_SENTINEL-config.toml"
    if config_kind == "invalid":
        config_path.write_text("[CONTENT_SENTINEL", encoding="utf-8")

    outbound_requests = 0

    def block_http(*args: object, **kwargs: object) -> object:
        nonlocal outbound_requests
        outbound_requests += 1
        raise AssertionError("outbound request forbidden")

    monkeypatch.setattr("mdrack.integrations.lmstudio.client.httpx.AsyncClient", block_http)

    with caplog.at_level(logging.INFO):
        result = CliRunner().invoke(main, ["--config-file", str(config_path), *command])

    _assert_fixed_cli_error(result, _FIXED_CONFIG_ERROR)
    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "cli.config.failed" in rendered_logs
    assert all(value not in rendered_logs for value in SENTINELS)
    assert str(config_path) not in result.output
    assert outbound_requests == 0


@pytest.mark.parametrize(
    ("command", "patch_target", "expected"),
    [
        ("status", "mdrack.diagnostics.integrity.get_generation_status", _FIXED_STATUS_ERROR),
        ("status", "mdrack.diagnostics.integrity.get_store_status", _FIXED_STATUS_ERROR),
        ("status", "mdrack.storage.sqlite.connection.get_read_only_connection", _FIXED_STATUS_ERROR),
        ("doctor", "mdrack.storage.sqlite.connection.get_read_only_connection", _FIXED_DOCTOR_ERROR),
        ("doctor", "mdrack.diagnostics.doctor.run_doctor", _FIXED_DOCTOR_ERROR),
        ("doctor", "mdrack.diagnostics.doctor.report_to_dict", _FIXED_DOCTOR_ERROR),
    ],
)
def test_status_and_doctor_internal_boundaries_use_fixed_private_safe_errors(
    command: str,
    patch_target: str,
    expected: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _setup_db(tmp_path)

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("PRIVATE_EXCEPTION_SENTINEL /home/private/VAULT_SENTINEL")

    monkeypatch.setattr(patch_target, fail)
    with caplog.at_level(logging.INFO):
        result = CliRunner().invoke(main, ["--root", str(tmp_path), command])

    payload = _assert_fixed_cli_error(result, expected)
    meta = payload["meta"]
    assert isinstance(meta, dict)
    assert str(meta["command"]).endswith(command)
    assert result.exception.__class__ is SystemExit
    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert f"cli.{command}.failed" in rendered_logs
    assert all(value not in rendered_logs for value in SENTINELS)


@pytest.mark.parametrize(
    ("command", "expected"),
    [("status", _FIXED_STATUS_ERROR), ("doctor", _FIXED_DOCTOR_ERROR)],
)
def test_status_and_doctor_success_serialization_failures_still_emit_one_fixed_json(
    command: str,
    expected: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _setup_db(tmp_path)

    def emit_json_with_private_success_failure(payload: dict[str, Any], pretty: bool = False) -> None:
        if payload["ok"]:
            raise TypeError("PRIVATE_EXCEPTION_SENTINEL")
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))

    monkeypatch.setattr("mdrack.cli.emit_json", emit_json_with_private_success_failure)
    result = CliRunner().invoke(main, ["--root", str(tmp_path), command])

    payload = _assert_fixed_cli_error(result, expected)
    meta = payload["meta"]
    assert isinstance(meta, dict)
    assert str(meta["command"]).endswith(command)


@pytest.mark.parametrize(
    ("status", "reason_code"),
    [
        ("ok", None),
        ("empty", "no_records"),
        ("degraded", "provider_unavailable"),
        ("failed", "validation_failed"),
        ("failed", "storage_failed"),
        ("failed", "provider_failed"),
        ("failed", "internal_failed"),
        ("failed", "cleanup_failed"),
    ],
)
def test_common_diagnostic_schema_covers_each_branch_without_passthrough(
    status: str,
    reason_code: str | None,
) -> None:
    check: dict[str, object] = {"code": "S8_CHECK", "status": status, "counts": {"attempted": 0}}
    if reason_code is not None:
        check["reason_code"] = reason_code
    payload = build_safe_diagnostic_record(
        generated_for="support",
        status=status,
        checks=[check],
    )
    _assert_safe(payload)


def test_eval_load_storage_internal_and_cleanup_failures_are_fixed_and_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    query_path = tmp_path / "private-query-set.yaml"
    query_path.write_text("CONTENT_SENTINEL: [", encoding="utf-8")
    load_result = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "eval", "retrieval", "--queries", str(query_path)],
    )
    assert load_result.exit_code == 1
    assert json.loads(load_result.output)["error"] == {
        "message": "Evaluation query set could not be loaded",
        "code": "EVAL_LOAD_ERROR",
    }

    query_path.write_text(
        "queries:\n"
        "  - id: QUERY_SENTINEL\n"
        "    query: CONTENT_SENTINEL\n"
        "    mode: text\n"
        "    expected:\n"
        "      content_contains: CONTENT_SENTINEL\n"
        "    metrics:\n"
        "      recall_at: 5\n",
        encoding="utf-8",
    )
    storage_result = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "eval", "retrieval", "--queries", str(query_path)],
    )
    assert storage_result.exit_code == 1
    assert json.loads(storage_result.output)["error"]["message"] == "Evaluation store is unavailable"

    _setup_db(tmp_path)

    class ClosingProvider:
        async def close(self) -> None:
            raise RuntimeError("PRIVATE_EXCEPTION_SENTINEL")

    monkeypatch.setattr(
        "mdrack.cli.commands.eval.create_embedding_provider",
        lambda *args, **kwargs: ClosingProvider(),
    )
    monkeypatch.setattr(
        "mdrack.cli.commands.eval.run_retrieval_eval",
        lambda *args, **kwargs: EvalReport(
            results=[],
            summary={"queries_total": 0, "private_summary": "CONTENT_SENTINEL"},
        ),
    )
    with caplog.at_level(logging.INFO):
        cleanup_result = CliRunner().invoke(
            main,
            ["--root", str(tmp_path), "eval", "retrieval", "--queries", str(query_path)],
        )
    assert cleanup_result.exit_code == 0
    assert cleanup_result.output.count("\n") == 1

    monkeypatch.setattr(
        "mdrack.cli.commands.eval.run_retrieval_eval",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("PRIVATE_EXCEPTION_SENTINEL")),
    )
    internal_result = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "eval", "retrieval", "--queries", str(query_path)],
    )
    assert internal_result.exit_code == 1
    assert json.loads(internal_result.output)["error"]["message"] == "Evaluation failed"

    combined = load_result.output + storage_result.output + cleanup_result.output + internal_result.output
    combined += "\n".join(record.getMessage() for record in caplog.records)
    assert all(value not in combined for value in SENTINELS)


def test_model_failure_cleanup_and_provider_body_are_not_serialized(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingClient:
        def list_models(self) -> list[object]:
            raise EmbeddingError("PROVIDER_BODY_SENTINEL PRIVATE_EXCEPTION_SENTINEL")

        async def close(self) -> None:
            raise RuntimeError("PRIVATE_EXCEPTION_SENTINEL")

    monkeypatch.setattr(
        "mdrack.cli.commands.model.create_model_control_client",
        lambda ctx: FailingClient(),
    )
    with caplog.at_level(logging.INFO):
        failure = CliRunner().invoke(main, ["model", "list"])
    assert failure.exit_code == 1
    _assert_safe(json.loads(failure.output))
    assert all(value not in "\n".join(record.getMessage() for record in caplog.records) for value in SENTINELS)

    class DownloadClient:
        def get_download_status(self) -> list[dict[str, object]]:
            return [
                {
                    "model": "safe-model",
                    "status": "failed",
                    "error": "PROVIDER_BODY_SENTINEL",
                    "provider_body": "PROVIDER_BODY_SENTINEL",
                }
            ]

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        "mdrack.cli.commands.model.create_model_control_client",
        lambda ctx: DownloadClient(),
    )
    status = CliRunner().invoke(main, ["model", "download-status"])
    assert status.exit_code == 0
    assert json.loads(status.output)["data"] == {
        "downloads": [{"model": "safe-model", "status": "failed"}]
    }
    _assert_safe(json.loads(status.output))
