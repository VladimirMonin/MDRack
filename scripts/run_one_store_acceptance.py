#!/usr/bin/env python3
"""Run bounded, privacy-safe acceptance evidence for the one-store fixture.

The runner owns only ``<evidence-root>/latest``. It keeps no raw pytest output,
source content, paths, vectors, endpoints, or private fixture values in the
published evidence. Logs use the frozen core safe-event envelope and stdout is
one final JSON object.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mdrack_core.observability import (
    LifecycleOperation,
    LifecycleReason,
    LifecycleStatus,
    SafeEvent,
    emit_event,
    safe_fingerprint,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "one_store_v1"
FIXTURE_MANIFEST = FIXTURE_ROOT / "manifest.json"
EVIDENCE_SCHEMA = "mdrack.one-store-acceptance-evidence.v1"
PYTEST_TARGETS = (
    "tests/e2e/test_one_store_contract.py",
    "tests/cli/test_cli_resource_lifecycle.py",
    "tests/e2e/test_cli_embedded_retrieval_parity.py",
)


@dataclass(frozen=True)
class AcceptanceStage:
    """One controlled pytest stage and the lifecycle state it demonstrates."""

    operation: LifecycleOperation
    status: LifecycleStatus
    targets: tuple[str, ...]
    reason: LifecycleReason | None = None


CONTROLLED_STAGES = (
    AcceptanceStage(
        LifecycleOperation.CATALOG,
        LifecycleStatus.COMPLETED,
        ("tests/e2e/test_one_store_contract.py::test_first_init_creates_only_canonical_catalog",),
    ),
    AcceptanceStage(
        LifecycleOperation.INDEXING,
        LifecycleStatus.COMPLETED,
        ("tests/e2e/test_one_store_contract.py::test_engine_scan_then_text_read_uses_the_same_catalog",),
    ),
    AcceptanceStage(
        LifecycleOperation.INGESTION,
        LifecycleStatus.COMPLETED,
        ("tests/cli/test_cli_resource_lifecycle.py::test_resource_lifecycle_uses_the_configured_catalog_and_matches_engine",),
    ),
    AcceptanceStage(
        LifecycleOperation.RETRIEVAL,
        LifecycleStatus.COMPLETED,
        (
            "tests/e2e/test_cli_embedded_retrieval_parity.py"
            "::test_cli_and_embedded_results_are_byte_for_byte_equivalent[text]",
        ),
    ),
    AcceptanceStage(
        LifecycleOperation.RETRIEVAL,
        LifecycleStatus.DEGRADED,
        (
            "tests/unit/test_unified_text_resources.py"
            "::test_unified_text_search_filters_by_alias_and_degrades_hybrid_without_provider",
        ),
        LifecycleReason.DEPENDENCY_UNAVAILABLE,
    ),
    AcceptanceStage(
        LifecycleOperation.RETRIEVAL,
        LifecycleStatus.FAILED,
        (
            "tests/e2e/test_one_store_contract.py"
            "::test_json_outputs_logs_diagnostics_and_evidence_hide_privacy_sentinels",
        ),
        LifecycleReason.DEPENDENCY_FAILED,
    ),
    AcceptanceStage(
        LifecycleOperation.RETRIEVAL,
        LifecycleStatus.RECOVERED,
        (
            "tests/e2e/test_cli_embedded_retrieval_parity.py"
            "::test_cli_and_embedded_results_are_byte_for_byte_equivalent[hybrid]",
        ),
    ),
)


class AcceptanceFailure(RuntimeError):
    """A known failure category safe to retain in acceptance evidence."""

    def __init__(self, reason: LifecycleReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AcceptanceFailure(LifecycleReason.INTEGRITY_FAILED, "required JSON is unavailable") from error
    if not isinstance(payload, dict):
        raise AcceptanceFailure(LifecycleReason.INTEGRITY_FAILED, "expected a JSON object")
    return payload


def _privacy_sentinels() -> list[str]:
    manifest = _load_json(FIXTURE_MANIFEST)
    policy = manifest.get("privacy_policy")
    if not isinstance(policy, dict) or not isinstance(policy.get("sentinel_file"), str):
        raise AcceptanceFailure(LifecycleReason.INTEGRITY_FAILED, "fixture privacy policy is invalid")
    privacy_policy = _load_json(FIXTURE_ROOT / policy["sentinel_file"])
    sentinels = privacy_policy.get("sentinels")
    if not isinstance(sentinels, list) or not all(isinstance(value, str) for value in sentinels):
        raise AcceptanceFailure(LifecycleReason.INTEGRITY_FAILED, "fixture privacy sentinel list is invalid")
    return sentinels


def _fixture_source_hashes() -> dict[str, str]:
    manifest = _load_json(FIXTURE_MANIFEST)
    payload_hashes = manifest.get("payload_sha256")
    if not isinstance(payload_hashes, dict):
        raise AcceptanceFailure(LifecycleReason.INTEGRITY_FAILED, "fixture does not have a payload-hash map")
    try:
        hashes = {
            relative_path: _sha256(FIXTURE_ROOT / relative_path)
            for relative_path in sorted(payload_hashes)
            if isinstance(relative_path, str)
        }
    except OSError as error:
        raise AcceptanceFailure(LifecycleReason.INTEGRITY_FAILED, "fixture payload is unavailable") from error
    if hashes != payload_hashes:
        raise AcceptanceFailure(
            LifecycleReason.INTEGRITY_FAILED,
            "one-store fixture source hashes do not match its manifest",
        )
    return hashes


def _event(
    logger: logging.Logger,
    *,
    name: str,
    execution_id: str,
    operation: LifecycleOperation,
    status: LifecycleStatus,
    reason: LifecycleReason | None = None,
    result_count: int | None = None,
    elapsed_ms: int | None = None,
) -> None:
    fields: dict[str, object] = {
        "execution_id": safe_fingerprint(execution_id),
        "operation": operation,
        "status": status,
    }
    if reason is not None:
        fields["reason"] = reason
    if result_count is not None:
        fields["result_count"] = result_count
    if elapsed_ms is not None:
        fields["elapsed_ms"] = elapsed_ms
    emit_event(logger, SafeEvent(name, fields))


def _run_pytest(targets: tuple[str, ...]) -> tuple[int, int]:
    started_at = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *targets],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
        env={**os.environ, "PYTHONPATH": ""},
    )
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    if result.returncode != 0:
        raise AcceptanceFailure(LifecycleReason.VALIDATION_FAILED, "one-store contract tests failed")
    match = re.search(r"(\d+) passed", result.stdout)
    if match is None:
        raise AcceptanceFailure(
            LifecycleReason.VALIDATION_FAILED,
            "pytest did not report a passed-test count",
        )
    return int(match.group(1)), elapsed_ms


def _event_name(status: LifecycleStatus) -> str:
    return f"core.lifecycle.{status.value}"


def _run_controlled_stages(logger: logging.Logger, execution_id: str) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for stage in CONTROLLED_STAGES:
        _event(
            logger,
            name="core.lifecycle.started",
            execution_id=execution_id,
            operation=stage.operation,
            status=LifecycleStatus.STARTED,
        )
        tests_passed, elapsed_ms = _run_pytest(stage.targets)
        _event(
            logger,
            name=_event_name(stage.status),
            execution_id=execution_id,
            operation=stage.operation,
            status=stage.status,
            reason=stage.reason,
            result_count=tests_passed,
            elapsed_ms=elapsed_ms,
        )
        stage_evidence: dict[str, object] = {
            "operation": stage.operation.value,
            "status": stage.status.value,
            "targets": list(stage.targets),
            "tests_passed": tests_passed,
            "elapsed_ms": elapsed_ms,
        }
        if stage.reason is not None:
            stage_evidence["reason"] = stage.reason.value
        evidence.append(stage_evidence)
    return evidence


def _build_wheels(output_dir: Path) -> list[Path]:
    package_dirs = (
        REPOSITORY_ROOT / "packages" / "mdrack-core",
        REPOSITORY_ROOT / "packages" / "mdrack-media",
        REPOSITORY_ROOT / "packages" / "mdrack-sqlite",
        REPOSITORY_ROOT,
    )
    for package_dir in package_dirs:
        result = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(output_dir)],
            cwd=package_dir,
            capture_output=True,
            check=False,
            text=True,
            env={**os.environ, "UV_OFFLINE": "1"},
        )
        if result.returncode != 0:
            raise AcceptanceFailure(LifecycleReason.DEPENDENCY_FAILED, "wheel build failed")
    wheels = sorted(output_dir.glob("*.whl"))
    if len(wheels) < len(package_dirs):
        raise AcceptanceFailure(
            LifecycleReason.INTEGRITY_FAILED,
            "wheel build did not produce every workspace distribution",
        )
    return wheels


def _check_installed_package_origin() -> dict[str, object]:
    """Install workspace wheels into a temporary target and check import origins."""

    with tempfile.TemporaryDirectory(prefix="mdrack-one-store-wheel-") as directory:
        temporary_root = Path(directory)
        wheel_dir = temporary_root / "wheels"
        install_dir = temporary_root / "installed"
        wheel_dir.mkdir()
        wheels = _build_wheels(wheel_dir)
        installed = subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                sys.executable,
                "--target",
                str(install_dir),
                "--no-deps",
                *(str(wheel) for wheel in wheels),
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            check=False,
            text=True,
            env={**os.environ, "UV_OFFLINE": "1"},
        )
        if installed.returncode != 0:
            raise AcceptanceFailure(
                LifecycleReason.DEPENDENCY_FAILED,
                "temporary installed-package setup failed",
            )
        probe = (
            "import json,mdrack,mdrack_core,mdrack_media,mdrack_sqlite; "
            "print(json.dumps({name: module.__file__ for name,module in "
            "{'mdrack':mdrack,'mdrack_core':mdrack_core,'mdrack_media':mdrack_media,"
            "'mdrack_sqlite':mdrack_sqlite}.items()},sort_keys=True))"
        )
        checked = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=temporary_root,
            capture_output=True,
            check=False,
            text=True,
            env={**os.environ, "PYTHONPATH": str(install_dir)},
        )
        if checked.returncode != 0:
            raise AcceptanceFailure(
                LifecycleReason.DEPENDENCY_FAILED,
                "temporary installed-package import failed",
            )
        try:
            origins = json.loads(checked.stdout)
        except json.JSONDecodeError as error:
            raise AcceptanceFailure(
                LifecycleReason.INTEGRITY_FAILED,
                "temporary installed-package import inventory is invalid",
            ) from error
        if not isinstance(origins, dict) or set(origins) != {
            "mdrack",
            "mdrack_core",
            "mdrack_media",
            "mdrack_sqlite",
        }:
            raise AcceptanceFailure(
                LifecycleReason.INTEGRITY_FAILED,
                "temporary installed-package import inventory is incomplete",
            )
        install_root = install_dir.resolve()
        for origin in origins.values():
            if not isinstance(origin, str) or not Path(origin).resolve().is_relative_to(install_root):
                raise AcceptanceFailure(
                    LifecycleReason.INTEGRITY_FAILED,
                    "workspace package was not imported from the wheel target",
                )
    return {"status": "passed", "distribution_count": 4, "origin": "temporary-wheel-target"}


def _safe_artifact_text(paths: tuple[Path, ...], sentinels: list[str]) -> None:
    for path in paths:
        rendered = path.read_text(encoding="utf-8")
        if any(sentinel in rendered for sentinel in sentinels):
            raise AcceptanceFailure(
                LifecycleReason.INTEGRITY_FAILED,
                "generated evidence contains a privacy sentinel",
            )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _replace_latest(evidence_root: Path, stage: Path) -> Path:
    latest = evidence_root / "latest"
    previous = evidence_root / ".previous-latest"
    if previous.exists() or previous.is_symlink():
        if previous.is_dir() and not previous.is_symlink():
            shutil.rmtree(previous)
        else:
            previous.unlink()
    if latest.exists() or latest.is_symlink():
        latest.replace(previous)
    try:
        stage.replace(latest)
    except Exception:
        if previous.exists() or previous.is_symlink():
            previous.replace(latest)
        raise
    if previous.exists() or previous.is_symlink():
        if previous.is_dir() and not previous.is_symlink():
            shutil.rmtree(previous)
        else:
            previous.unlink()
    return latest


def _publish_evidence(
    evidence_root: Path,
    *,
    summary: dict[str, object],
    log_text: str,
    fixture_source_hashes: dict[str, str] | None,
    sentinels: list[str],
) -> tuple[Path, str]:
    evidence_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".latest-", dir=evidence_root))
    try:
        summary_path = stage / "summary.json"
        log_path = stage / "runner.log"
        _write_json(summary_path, summary)
        log_path.write_text(log_text, encoding="utf-8")
        _safe_artifact_text((summary_path, log_path), sentinels)

        files = [
            {
                "path": path.name,
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in sorted((summary_path, log_path), key=lambda item: item.name)
        ]
        evidence_manifest: dict[str, object] = {
            "schema": EVIDENCE_SCHEMA,
            "manifest_self_hash": "excluded_to_avoid_self_reference",
            "runner_source_sha256": _sha256(Path(__file__).resolve()),
            "files": files,
            "privacy_scan": {"safe": True, "findings_count": 0},
        }
        if fixture_source_hashes is not None:
            evidence_manifest["fixture_source_hashes"] = fixture_source_hashes
        manifest_path = stage / "manifest.json"
        _write_json(manifest_path, evidence_manifest)
        _safe_artifact_text((manifest_path, summary_path, log_path), sentinels)
        manifest_hash = _sha256(manifest_path)
        return _replace_latest(evidence_root, stage), manifest_hash
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _write_success_evidence(
    evidence_root: Path,
    *,
    execution_id: str,
    fixture_source_hashes: dict[str, str],
    tests_passed: int,
    pytest_elapsed_ms: int,
    lifecycle_stages: list[dict[str, object]],
    installed_package: dict[str, object],
    log_text: str,
) -> tuple[Path, str]:
    sentinels = _privacy_sentinels()
    summary: dict[str, object] = {
        "schema": EVIDENCE_SCHEMA,
        "status": "passed",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "execution_id": safe_fingerprint(execution_id).value,
        "checks": {
            "fixture_source_hashes": fixture_source_hashes,
            "pytest": {
                "status": "passed",
                "tests_passed": tests_passed,
                "elapsed_ms": pytest_elapsed_ms,
                "targets": list(PYTEST_TARGETS),
            },
            "lifecycle_stages": lifecycle_stages,
            "installed_package": installed_package,
            "privacy": {"status": "passed", "sentinel_count": len(sentinels)},
        },
    }
    return _publish_evidence(
        evidence_root,
        summary=summary,
        log_text=log_text,
        fixture_source_hashes=fixture_source_hashes,
        sentinels=sentinels,
    )


def _write_failure_evidence(
    evidence_root: Path,
    *,
    execution_id: str,
    reason: LifecycleReason | None,
    elapsed_ms: int,
    log_text: str,
) -> tuple[Path, str]:
    try:
        sentinels = _privacy_sentinels()
    except AcceptanceFailure:
        sentinels = []
    previous_latest_present = (evidence_root / "latest").exists() or (
        evidence_root / "latest"
    ).is_symlink()
    acceptance: dict[str, object] = {
        "status": "failed",
        "elapsed_ms": elapsed_ms,
        "previous_latest_invalidated": previous_latest_present,
    }
    if reason is not None:
        acceptance["reason"] = reason.value
    summary: dict[str, object] = {
        "schema": EVIDENCE_SCHEMA,
        "status": "failed",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "execution_id": safe_fingerprint(execution_id).value,
        "checks": {"acceptance": acceptance},
    }
    return _publish_evidence(
        evidence_root,
        summary=summary,
        log_text=log_text,
        fixture_source_hashes=None,
        sentinels=sentinels,
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-root",
        type=Path,
        required=True,
        help="Directory whose generated latest/ child this runner owns.",
    )
    parser.add_argument(
        "--skip-installed-package",
        action="store_true",
        help="Skip the isolated workspace-wheel origin check (development only).",
    )
    return parser.parse_args()


def _result_payload(installed_package: dict[str, object], manifest_hash: str) -> dict[str, object]:
    return {
        "ok": True,
        "data": {
            "fixture": {
                "schema": "mdrack.one-store-fixture.v1",
                "required_similarity_cells": 16,
            },
            "installed_package": installed_package,
            "evidence_manifest_sha256": manifest_hash,
            "evidence_layout": "latest",
        },
        "meta": {"command": "one-store-acceptance"},
    }


def _failure_payload() -> dict[str, object]:
    return {
        "ok": False,
        "error": {"message": "One-store acceptance failed", "code": "ACCEPTANCE_FAILED"},
        "meta": {"command": "one-store-acceptance"},
    }


def _print_payload(payload: dict[str, object]) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def main() -> int:
    args = _arguments()
    execution_id = str(uuid.uuid4())
    acceptance_started_at = time.monotonic()
    logger = logging.getLogger("mdrack.acceptance.one_store")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    stream = tempfile.SpooledTemporaryFile(mode="w+t", encoding="utf-8", max_size=64 * 1024)
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    try:
        _event(
            logger,
            name="core.lifecycle.started",
            execution_id=execution_id,
            operation=LifecycleOperation.ACCEPTANCE,
            status=LifecycleStatus.STARTED,
        )
        fixture_source_hashes = _fixture_source_hashes()
        tests_passed, pytest_elapsed_ms = _run_pytest(PYTEST_TARGETS)
        lifecycle_stages = _run_controlled_stages(logger, execution_id)
        if _fixture_source_hashes() != fixture_source_hashes:
            raise AcceptanceFailure(
                LifecycleReason.INTEGRITY_FAILED,
                "one-store fixture source changed during acceptance",
            )
        installed_package: dict[str, object] = (
            {"status": "skipped"}
            if args.skip_installed_package
            else _check_installed_package_origin()
        )
        stage_test_counts = [stage.get("tests_passed") for stage in lifecycle_stages]
        if not all(type(value) is int for value in stage_test_counts):
            raise AcceptanceFailure(
                LifecycleReason.INTEGRITY_FAILED,
                "controlled stage count is invalid",
            )
        total_tests_passed = tests_passed + sum(
            value for value in stage_test_counts if type(value) is int
        )
        acceptance_elapsed_ms = int((time.monotonic() - acceptance_started_at) * 1000)
        _event(
            logger,
            name="core.lifecycle.completed",
            execution_id=execution_id,
            operation=LifecycleOperation.ACCEPTANCE,
            status=LifecycleStatus.COMPLETED,
            result_count=total_tests_passed,
            elapsed_ms=acceptance_elapsed_ms,
        )
        stream.seek(0)
        latest, manifest_hash = _write_success_evidence(
            args.evidence_root,
            execution_id=execution_id,
            fixture_source_hashes=fixture_source_hashes,
            tests_passed=tests_passed,
            pytest_elapsed_ms=pytest_elapsed_ms,
            lifecycle_stages=lifecycle_stages,
            installed_package=installed_package,
            log_text=stream.read(),
        )
        del latest
        _print_payload(_result_payload(installed_package, manifest_hash))
        return 0
    except Exception as error:
        reason = error.reason if isinstance(error, AcceptanceFailure) else None
        elapsed_ms = int((time.monotonic() - acceptance_started_at) * 1000)
        _event(
            logger,
            name="core.lifecycle.failed",
            execution_id=execution_id,
            operation=LifecycleOperation.ACCEPTANCE,
            status=LifecycleStatus.FAILED,
            reason=reason,
            result_count=0,
            elapsed_ms=elapsed_ms,
        )
        try:
            stream.seek(0)
            _write_failure_evidence(
                args.evidence_root,
                execution_id=execution_id,
                reason=reason,
                elapsed_ms=elapsed_ms,
                log_text=stream.read(),
            )
        except Exception:
            pass
        _print_payload(_failure_payload())
        return 1
    finally:
        logger.removeHandler(handler)
        stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
