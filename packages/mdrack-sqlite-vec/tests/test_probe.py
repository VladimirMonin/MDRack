"""Tests for the fail-closed sqlite-vec compatibility probe."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mdrack_sqlite_vec import ProbeOutcomeStatus, ProbeStatus, SQLiteVecCompatibilityProbe


def test_construction_and_import_are_network_free(tmp_path: Path) -> None:
    script = """
import socket

def blocked(*args, **kwargs):
    raise AssertionError("network access attempted")

socket.create_connection = blocked
socket.socket.connect = blocked
import mdrack_sqlite_vec
mdrack_sqlite_vec.SQLiteVecCompatibilityProbe()
print("ok")
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        check=True,
        capture_output=True,
        cwd=tmp_path,
        text=True,
    )
    assert completed.stdout == "ok\n"


def test_probe_exercises_supported_cells_and_keeps_builtin_for_tie_boundary() -> None:
    report = SQLiteVecCompatibilityProbe().run()
    outcomes = {outcome.name: outcome for outcome in report.outcomes}

    assert report.status is ProbeStatus.FAIL
    assert report.observed_version == "0.1.9"
    assert report.failure_codes == ("tie_boundary_requires_full_scan",)
    assert all(
        outcomes[name].status is ProbeOutcomeStatus.PASS
        for name in (
            "installed_extension",
            "float32_dimensions",
            "metrics",
            "metadata_scope",
            "delete",
            "transactions",
            "extensionless_reopen",
            "platform_matrix",
        )
    )
    tie = outcomes["tie_boundary"]
    assert tie.status is ProbeOutcomeStatus.FAIL
    assert tie.failure_code == "tie_boundary_requires_full_scan"
    assert tie.facts["candidate_limit_one_count"] == 1
    assert tie.facts["tie_cohort_count_at_k2"] == 2
    assert tie.facts["distance_constraint_count"] == 1
    assert tie.facts["near_tie_distinct"] is True


def test_module_output_is_machine_readable_and_nonzero_for_promotion_failure(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-I", "-m", "mdrack_sqlite_vec"],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stderr == ""
    report = json.loads(completed.stdout)
    assert report["contract"] == "mdrack.sqlite-vec-compatibility-probe-v1"
    assert report["status"] == "fail"
    assert report["decision"] == {
        "action": "keep_builtin",
        "backend_id": "builtin-exact-v1",
        "failure_codes": ["tie_boundary_requires_full_scan"],
        "promotion_allowed": False,
    }
