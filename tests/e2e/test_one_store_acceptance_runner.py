"""Black-box contract for the bounded one-store acceptance runner."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPOSITORY_ROOT / "scripts" / "run_one_store_acceptance.py"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "one_store_v1"


def _run_runner(
    evidence_root: Path,
    *,
    skip_installed_package: bool = True,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object] | None, Path]:
    command = [sys.executable, str(RUNNER), "--evidence-root", str(evidence_root)]
    if skip_installed_package:
        command.append("--skip-installed-package")
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.stdout.count("\n") == 1
    payload = json.loads(result.stdout)
    data = payload.get("data")
    assert data is None or isinstance(data, dict)
    return result, data, evidence_root / "latest"


def test_one_store_runner_replaces_bounded_latest_evidence_and_keeps_it_private(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    first_result, first, latest = _run_runner(evidence_root)

    assert first_result.returncode == 0, first_result.stderr
    assert first is not None
    assert first["installed_package"] == {"status": "skipped"}
    assert first["fixture"] == {"schema": "mdrack.one-store-fixture.v1", "required_similarity_cells": 16}
    assert latest.is_dir()
    assert {path.name for path in latest.iterdir()} == {"manifest.json", "summary.json", "runner.log"}
    assert {path.name for path in evidence_root.iterdir()} == {"latest"}

    manifest = json.loads((latest / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((latest / "summary.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "mdrack.one-store-acceptance-evidence.v1"
    assert manifest["manifest_self_hash"] == "excluded_to_avoid_self_reference"
    assert manifest["privacy_scan"] == {"safe": True, "findings_count": 0}
    assert summary["checks"]["pytest"]["status"] == "passed"
    assert summary["checks"]["pytest"]["tests_passed"] >= 25
    stages = summary["checks"]["lifecycle_stages"]
    assert isinstance(stages, list)
    assert {(stage["operation"], stage["status"]) for stage in stages} >= {
        ("catalog", "completed"),
        ("indexing", "completed"),
        ("ingestion", "completed"),
        ("retrieval", "completed"),
        ("retrieval", "degraded"),
        ("retrieval", "failed"),
        ("retrieval", "recovered"),
    }
    assert all(stage["tests_passed"] >= 1 for stage in stages)
    assert all(isinstance(stage["elapsed_ms"], int) and stage["elapsed_ms"] >= 0 for stage in stages)
    assert summary["checks"]["fixture_source_hashes"] == {
        relative_path: "sha256:" + hashlib.sha256((FIXTURE_ROOT / relative_path).read_bytes()).hexdigest()
        for relative_path in sorted(manifest["fixture_source_hashes"])
    }
    for entry in manifest["files"]:
        artifact = latest / entry["path"]
        assert artifact.is_file()
        assert entry["sha256"] == "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()

    sentinels = json.loads((FIXTURE_ROOT / "privacy-sentinels.json").read_text(encoding="utf-8"))["sentinels"]
    rendered = "\n".join(path.read_text(encoding="utf-8") for path in latest.iterdir())
    assert all(sentinel not in rendered for sentinel in sentinels)

    lifecycle_events = [
        json.loads(line.split(" ", maxsplit=1)[1])
        for line in (latest / "runner.log").read_text(encoding="utf-8").splitlines()
        if line.startswith("core.lifecycle.")
    ]
    assert {event["status"] for event in lifecycle_events} >= {
        "started",
        "completed",
        "degraded",
        "failed",
        "recovered",
    }
    assert len({event["execution_id"] for event in lifecycle_events}) == 1
    assert summary["execution_id"] == lifecycle_events[0]["execution_id"]
    assert {event["operation"] for event in lifecycle_events} >= {
        "acceptance",
        "catalog",
        "indexing",
        "ingestion",
        "retrieval",
    }
    terminal_events = [
        event
        for event in lifecycle_events
        if event["status"] in {"completed", "degraded", "failed", "recovered"}
    ]
    assert {event["operation"] for event in terminal_events} >= {
        "acceptance", "catalog", "indexing", "ingestion", "retrieval"
    }
    assert all(isinstance(event["elapsed_ms"], int) and event["elapsed_ms"] >= 0 for event in terminal_events)
    assert all(isinstance(event["result_count"], int) and event["result_count"] >= 1 for event in terminal_events)

    second_result, second, latest_after_second_run = _run_runner(evidence_root)
    assert second_result.returncode == 0, second_result.stderr
    assert second is not None
    assert latest_after_second_run == latest
    assert second["evidence_manifest_sha256"] != first["evidence_manifest_sha256"]
    assert {path.name for path in evidence_root.iterdir()} == {"latest"}


def test_one_store_runner_exercises_default_installed_wheel_origin(tmp_path: Path) -> None:
    result, data, latest = _run_runner(
        tmp_path / "evidence",
        skip_installed_package=False,
    )

    assert result.returncode == 0, result.stderr
    assert data is not None
    assert data["installed_package"] == {
        "status": "passed",
        "distribution_count": 4,
        "origin": "temporary-wheel-target",
    }
    summary = json.loads((latest / "summary.json").read_text(encoding="utf-8"))
    assert summary["checks"]["installed_package"] == data["installed_package"]


def test_failed_rerun_replaces_older_success_with_bounded_failure_evidence(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    success, _, latest = _run_runner(evidence_root)
    assert success.returncode == 0, (
        success.stdout,
        (latest / "summary.json").read_text(encoding="utf-8"),
    )
    successful_summary = json.loads((latest / "summary.json").read_text(encoding="utf-8"))
    assert successful_summary["status"] == "passed"

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_uv.chmod(0o755)
    known_failed, _, _ = _run_runner(
        evidence_root,
        skip_installed_package=False,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )
    assert known_failed.returncode == 1
    known_failure_summary = json.loads((latest / "summary.json").read_text(encoding="utf-8"))
    assert known_failure_summary["checks"]["acceptance"]["reason"] == "dependency_failed"
    assert known_failure_summary["checks"]["acceptance"]["previous_latest_invalidated"] is True

    failed, data, failed_latest = _run_runner(
        evidence_root,
        skip_installed_package=False,
        env={**os.environ, "PATH": ""},
    )

    assert failed.returncode == 1
    assert failed.stderr == ""
    assert data is None
    assert json.loads(failed.stdout) == {
        "error": {"code": "ACCEPTANCE_FAILED", "message": "One-store acceptance failed"},
        "meta": {"command": "one-store-acceptance"},
        "ok": False,
    }
    assert failed_latest == latest
    failure_summary = json.loads((failed_latest / "summary.json").read_text(encoding="utf-8"))
    assert failure_summary["status"] == "failed"
    failure_check = failure_summary["checks"]["acceptance"]
    assert failure_check["status"] == "failed"
    assert isinstance(failure_check["elapsed_ms"], int) and failure_check["elapsed_ms"] >= 0
    assert failure_check["previous_latest_invalidated"] is True
    assert "reason" not in failure_check
    assert failure_summary["execution_id"] != successful_summary["execution_id"]
    assert {path.name for path in failed_latest.iterdir()} == {
        "manifest.json", "summary.json", "runner.log"
    }
    assert {path.name for path in evidence_root.iterdir()} == {"latest"}


def test_one_store_runner_returns_a_generic_safe_error_when_evidence_root_is_not_directory(
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "occupied-evidence-root"
    evidence_root.write_text("not a directory", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--evidence-root",
            str(evidence_root),
            "--skip-installed-package",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stderr == ""
    assert result.stdout.count("\n") == 1
    assert json.loads(result.stdout) == {
        "error": {"code": "ACCEPTANCE_FAILED", "message": "One-store acceptance failed"},
        "meta": {"command": "one-store-acceptance"},
        "ok": False,
    }
    assert str(evidence_root) not in result.stdout
