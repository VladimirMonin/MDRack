from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mdrack.eval.privacy import scan_privacy
from tests.evaluation.v1_1.offline_runner import safe_report_json

pytestmark = pytest.mark.privacy

ROOT = Path(__file__).resolve().parents[3]


def test_q1_runtime_report_is_safe_on_every_captured_evidence_surface() -> None:
    sentinels = json.loads(
        (ROOT / "tests/privacy/v1_1/sentinels.json").read_text(encoding="utf-8")
    )
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_v11_offline_e2e.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    report = json.loads(completed.stdout)
    text = safe_report_json(report)

    assert set(report["privacy"]["surfaces_checked"]) == {
        "api",
        "cli_stderr",
        "cli_stdout",
        "disk",
        "eval",
        "log",
        "provider",
        "report",
    }
    assert scan_privacy(
        json.loads(text),
        forbidden_values=list(sentinels["forbidden_values"].values()),
        forbidden_keys=sentinels["forbidden_keys"],
    ).safe
    assert all(value not in text for value in sentinels["forbidden_values"].values())
    assert all(key not in text for key in sentinels["forbidden_keys"])
    ledger = report["privacy"]["capture_ledger"]
    assert all(entry["captured"] is True for entry in ledger)
    assert all(entry["violations"] == 0 for entry in ledger)
