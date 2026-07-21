from __future__ import annotations

import copy
import hashlib
import json
import socket
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from tests.evaluation.v1_1 import offline_runner

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "configs/eval-v11.toml"
RUNTIME_CONTRACT = ROOT / "tests/evaluation/v1_1/runtime-contract.json"


@pytest.fixture(scope="session")
def q1_report() -> dict[str, Any]:
    return offline_runner.execute_twice()


@pytest.fixture(scope="session")
def q1_accepted_report(
    q1_report: dict[str, Any],
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Any]:
    captured = offline_runner.capture_cli_transport(
        copy.deepcopy(q1_report),
        stdout=json.dumps(q1_report, ensure_ascii=False, sort_keys=True),
        stderr="",
    )
    return offline_runner.write_report(
        captured,
        tmp_path_factory.mktemp("q1-accepted-report"),
    )


def _mutate(
    report: dict[str, Any],
    action: str,
    path: tuple[str | int, ...],
    value: object = None,
) -> dict[str, Any]:
    mutated = copy.deepcopy(report)
    target: Any = mutated
    for part in path[:-1]:
        target = target[part]
    key = path[-1]
    if action == "set":
        target[key] = value
    elif action == "delete":
        del target[key]
    elif action == "append":
        target[key].append(value)
    else:  # pragma: no cover - test table construction guard
        raise AssertionError(f"unknown mutation action: {action}")
    return mutated


def _hybrid_path(*parts: str, run: int = 0) -> tuple[str | int, ...]:
    return ("runs", run, "ablations", "hybrid_profiles", *parts)


def _ledger_path(*parts: str | int) -> tuple[str | int, ...]:
    return ("privacy", "capture_ledger", *parts)


def test_f1_input_freeze_is_unchanged_and_q1_has_a_distinct_runtime_contract() -> None:
    contract = json.loads(RUNTIME_CONTRACT.read_text(encoding="utf-8"))
    digest = "sha256:" + hashlib.sha256(CONFIG.read_bytes()).hexdigest()

    assert digest == contract["f1_config_sha256"]
    assert contract["contract"] == "mdrack.v11-q1-runtime-result"
    assert contract["required_execution"]["repeats"] == 2
    assert contract["required_execution"]["network_syscalls"] == 0
    assert set(contract["required_hybrid_profiles"]) == {
        "configured",
        "equal",
        "lexical_only",
        "semantic_only",
    }
    assert set(contract["required_privacy_surfaces"]) == offline_runner.PRIVACY_SURFACES


def test_q1_runs_all_application_scenarios_twice_with_identical_logical_evidence(
    q1_report: dict[str, Any],
) -> None:
    execution = q1_report["execution"]
    assert execution["repeats"] == 2
    assert execution["fresh_disposable_catalogs"] == 2
    assert execution["deterministic_logical_evidence"] is True
    assert execution["source_hashes_unchanged"] is True
    assert execution["disposable_catalogs_removed"] is True
    assert execution["network_attempts"] == 0

    assert len(q1_report["runs"]) == 2
    for run in q1_report["runs"]:
        assert set(run["scenarios"]) == {
            "A_metadata",
            "B_audio",
            "C_video",
            "D_degradation",
        }
        assert set(run["ablations"]) == {
            "chunk_profiles",
            "frame_profiles",
            "metadata_profiles",
            "hybrid_profiles",
        }
        assert run["scenarios"]["A_metadata"]["typed_filter_hit"] is True
        assert run["scenarios"]["B_audio"]["timestamps_valid"] is True
        assert run["scenarios"]["B_audio"]["textual_similarity_hit"] is True
        assert run["scenarios"]["C_video"]["resource_grouping"] is True
        assert run["scenarios"]["C_video"]["frame_timestamp_valid"] is True
        assert run["scenarios"]["D_degradation"]["hybrid_lexical_fallback"] is True
        assert run["quality"]["summary"]["cases"] == 170


def test_all_hybrid_profiles_are_executed_metric_objects_over_the_same_cases(
    q1_report: dict[str, Any],
) -> None:
    expected_weights = {
        name: (lexical_weight, semantic_weight)
        for name, lexical_weight, semantic_weight in offline_runner.HYBRID_PROFILES
    }
    required_metrics = {
        "cases",
        "temporal_cases",
        "recall_at_5",
        "recall_at_10",
        "mrr",
        "ndcg",
    }
    for run in q1_report["runs"]:
        profiles = run["ablations"]["hybrid_profiles"]
        assert set(profiles) == set(expected_weights)
        case_counts = set()
        metric_keys = set()
        for name, cell in profiles.items():
            assert isinstance(cell, Mapping)
            assert (cell["lexical_weight"], cell["semantic_weight"]) == expected_weights[name]
            metrics = cell["metrics"]
            assert isinstance(metrics, Mapping)
            assert required_metrics <= set(metrics)
            case_counts.add(metrics["cases"])
            metric_keys.add(tuple(sorted(metrics)))
        assert case_counts == {30}
        assert len(metric_keys) == 1


@pytest.mark.parametrize(
    ("action", "path", "value"),
    [
        pytest.param("set", ("runs",), {}, id="runs-wrong-type"),
        pytest.param("delete", ("runs", 1), None, id="missing-repeat"),
        pytest.param(
            "delete", _hybrid_path("semantic_only"), None, id="missing-profile"
        ),
        pytest.param("set", _hybrid_path("extra"), {}, id="extra-profile"),
        pytest.param(
            "set",
            _hybrid_path("lexical_only"),
            "placeholder",
            id="profile-wrong-type",
        ),
        pytest.param(
            "delete",
            _hybrid_path("lexical_only", "metrics"),
            None,
            id="profile-missing-key",
        ),
        pytest.param(
            "set", _hybrid_path("lexical_only", "extra"), 0, id="profile-extra-key"
        ),
        pytest.param(
            "set",
            _hybrid_path("lexical_only", "lexical_weight"),
            True,
            id="weight-bool",
        ),
        pytest.param(
            "set",
            _hybrid_path("lexical_only", "lexical_weight"),
            "1.0",
            id="weight-string",
        ),
        pytest.param(
            "set",
            _hybrid_path("lexical_only", "lexical_weight"),
            0.9,
            id="weight-wrong-value",
        ),
        pytest.param(
            "set",
            _hybrid_path("lexical_only", "lexical_weight"),
            float("inf"),
            id="weight-non-finite",
        ),
        pytest.param(
            "set", _hybrid_path("equal", "metrics"), [], id="metrics-wrong-type"
        ),
        pytest.param(
            "delete",
            _hybrid_path("equal", "metrics", "mrr"),
            None,
            id="metric-missing",
        ),
        pytest.param(
            "set",
            _hybrid_path("equal", "metrics", "extra"),
            0.0,
            id="metric-extra",
        ),
        pytest.param(
            "set",
            _hybrid_path("equal", "metrics", "mrr"),
            "0.5",
            id="metric-string",
        ),
        pytest.param(
            "set",
            _hybrid_path("equal", "metrics", "mrr"),
            False,
            id="metric-bool",
        ),
        pytest.param(
            "set",
            _hybrid_path("equal", "metrics", "mrr"),
            float("nan"),
            id="metric-non-finite",
        ),
        pytest.param(
            "set",
            _hybrid_path("equal", "metrics", "cases"),
            0,
            id="cases-nonpositive",
        ),
        pytest.param(
            "set",
            _hybrid_path("equal", "metrics", "cases"),
            29,
            id="cases-profile-mismatch",
        ),
        pytest.param(
            "set",
            _hybrid_path("equal", "metrics", "cases", run=1),
            29,
            id="cases-repeat-drift",
        ),
    ],
)
def test_finalize_report_rejects_every_hybrid_matrix_mutation(
    q1_accepted_report: dict[str, Any],
    action: str,
    path: tuple[str | int, ...],
    value: object,
) -> None:
    mutated = _mutate(q1_accepted_report, action, path, value)

    with pytest.raises(offline_runner.OfflineEvaluationError):
        offline_runner.finalize_report(mutated)


@pytest.mark.parametrize(
    ("action", "path", "value"),
    [
        pytest.param("set", ("privacy",), [], id="privacy-wrong-type"),
        pytest.param("set", _ledger_path(), {}, id="ledger-wrong-type"),
        pytest.param("delete", _ledger_path(6), None, id="missing-surface"),
        pytest.param(
            "append",
            _ledger_path(),
            {
                "surface": "extra",
                "captured": True,
                "payload_type": "json",
                "violations": 0,
            },
            id="extra-surface",
        ),
        pytest.param(
            "append",
            _ledger_path(),
            {
                "surface": "api",
                "captured": True,
                "payload_type": "json",
                "violations": 0,
            },
            id="duplicate-surface",
        ),
        pytest.param("set", _ledger_path(0), "api", id="entry-wrong-type"),
        pytest.param(
            "delete", _ledger_path(0, "payload_type"), None, id="entry-missing-key"
        ),
        pytest.param("set", _ledger_path(0, "extra"), 0, id="entry-extra-key"),
        pytest.param("set", _ledger_path(0, "surface"), 1, id="surface-wrong-type"),
        pytest.param(
            "set", _ledger_path(0, "captured"), False, id="captured-false"
        ),
        pytest.param(
            "set", _ledger_path(0, "captured"), 1, id="captured-wrong-type"
        ),
        pytest.param(
            "set", _ledger_path(0, "violations"), 1, id="entry-violations-nonzero"
        ),
        pytest.param(
            "set",
            _ledger_path(0, "violations"),
            "0",
            id="entry-violations-wrong-type",
        ),
        pytest.param(
            "set",
            _ledger_path(0, "payload_type"),
            "unknown",
            id="payload-type-invalid",
        ),
        pytest.param(
            "set",
            ("privacy", "surfaces_checked"),
            ["api"],
            id="summary-surfaces-incomplete",
        ),
        pytest.param(
            "set",
            ("privacy", "surfaces_checked"),
            sorted(offline_runner.PRIVACY_SURFACES) + ["api"],
            id="summary-surfaces-duplicate",
        ),
        pytest.param(
            "set", ("privacy", "violations"), 1, id="summary-violations-nonzero"
        ),
        pytest.param(
            "set",
            ("privacy", "violations"),
            "0",
            id="summary-violations-wrong-type",
        ),
    ],
)
def test_finalize_report_rejects_every_privacy_ledger_mutation(
    q1_accepted_report: dict[str, Any],
    action: str,
    path: tuple[str | int, ...],
    value: object,
) -> None:
    mutated = _mutate(q1_accepted_report, action, path, value)

    with pytest.raises(offline_runner.OfflineEvaluationError):
        offline_runner.finalize_report(mutated)


def test_canonical_accepted_report_remains_byte_deterministic(
    q1_accepted_report: dict[str, Any],
) -> None:
    first = offline_runner.safe_report_json(q1_accepted_report)
    second = offline_runner.safe_report_json(q1_accepted_report)

    assert first == second


@pytest.mark.parametrize("operation", ["dns", "connect", "udp"])
def test_python_network_guard_negative_controls_are_non_vacuous(operation: str) -> None:
    guard = offline_runner._NetworkGuard()
    sock = socket.socket() if operation != "dns" else None
    try:
        with guard, pytest.raises(offline_runner.OfflineEvaluationError):
            if operation == "dns":
                socket.getaddrinfo("example.invalid", 443)
            elif operation == "connect":
                assert sock is not None
                sock.connect(("127.0.0.1", 9))
            else:
                assert sock is not None
                sock.sendto(b"negative-control", ("127.0.0.1", 9))
    finally:
        if sock is not None:
            sock.close()
    assert guard.attempts == 1


def test_privacy_gate_rejects_before_disk_write_and_safe_report_round_trips(
    q1_accepted_report: dict[str, Any],
    tmp_path: Path,
) -> None:
    sentinels = json.loads(
        (ROOT / "tests/privacy/v1_1/sentinels.json").read_text(encoding="utf-8")
    )
    unsafe = json.loads(json.dumps(q1_accepted_report))
    unsafe["unsafe"] = sentinels["forbidden_values"]["provider_body"]
    output = tmp_path / "unsafe"

    with pytest.raises(offline_runner.OfflineEvaluationError):
        offline_runner.write_report(unsafe, output)
    assert not (output / "summary.json").exists()

    safe_output = tmp_path / "safe"
    written = offline_runner.write_report(q1_accepted_report, safe_output)
    assert json.loads((safe_output / "summary.json").read_text(encoding="utf-8"))[
        "artifact_digest"
    ] == written["artifact_digest"]


def test_cli_runner_observes_zero_external_network_syscalls() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_v11_offline_e2e.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    report = json.loads(completed.stdout)
    assert report["execution"]["network_syscalls_observed"] is True
    assert report["execution"]["network_syscalls"] == 0
    assert report["privacy"]["violations"] == 0
    assert set(report["privacy"]["surfaces_checked"]) == offline_runner.PRIVACY_SURFACES
    assert {
        entry["surface"] for entry in report["privacy"]["capture_ledger"]
    } == offline_runner.PRIVACY_SURFACES


@pytest.mark.parametrize("surface", sorted(offline_runner.PRIVACY_SURFACES))
def test_capture_ledger_fails_closed_for_every_surface(surface: str) -> None:
    sentinels = json.loads(
        (ROOT / "tests/privacy/v1_1/sentinels.json").read_text(encoding="utf-8")
    )
    ledger = offline_runner.PrivacyCaptureLedger.create()

    with pytest.raises(offline_runner.OfflineEvaluationError):
        ledger.capture(surface, {"captured": sentinels["forbidden_values"]["provider_body"]})
    assert surface not in ledger.entries


@pytest.mark.parametrize("surface", ["api", "eval", "log", "provider"])
def test_runtime_capture_boundaries_fail_closed(surface: str) -> None:
    sentinels = json.loads(
        (ROOT / "tests/privacy/v1_1/sentinels.json").read_text(encoding="utf-8")
    )
    values: dict[str, Any] = {
        "api": {"status": "ok"},
        "provider": {"status": "ok"},
        "evaluation": {"cases": 1},
        "logs": "",
    }
    argument = "evaluation" if surface == "eval" else "logs" if surface == "log" else surface
    values[argument] = sentinels["forbidden_values"]["provider_body"]

    with pytest.raises(offline_runner.OfflineEvaluationError):
        offline_runner.capture_runtime_surfaces(**values)


@pytest.mark.parametrize("surface", ["cli_stdout", "cli_stderr"])
def test_cli_transport_fails_closed_for_each_channel(
    surface: str,
    q1_report: dict[str, Any],
) -> None:
    sentinels = json.loads(
        (ROOT / "tests/privacy/v1_1/sentinels.json").read_text(encoding="utf-8")
    )
    channels = {"stdout": "{}", "stderr": ""}
    channels[surface.removeprefix("cli_")] = sentinels["forbidden_values"]["provider_body"]

    with pytest.raises(offline_runner.OfflineEvaluationError):
        offline_runner.capture_cli_transport(
            json.loads(json.dumps(q1_report)),
            stdout=channels["stdout"],
            stderr=channels["stderr"],
        )


def test_disk_readback_fails_closed_before_final_file_replacement(
    q1_report: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinels = json.loads(
        (ROOT / "tests/privacy/v1_1/sentinels.json").read_text(encoding="utf-8")
    )
    output = tmp_path / "disk-injected"
    original_read_text = Path.read_text

    def injected_read_text(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        text = original_read_text(path, encoding=encoding, errors=errors)
        if path == output / ".summary.json.tmp":
            payload = json.loads(text)
            payload["captured"] = sentinels["forbidden_values"]["provider_body"]
            return json.dumps(payload)
        return text

    monkeypatch.setattr(Path, "read_text", injected_read_text)
    with pytest.raises(offline_runner.OfflineEvaluationError):
        offline_runner.write_report(q1_report, output)
    assert not (output / "summary.json").exists()
