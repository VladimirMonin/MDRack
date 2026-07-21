#!/usr/bin/env python3
"""Run MDRack 1.1 Q1 offline evidence under an external syscall observer."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.evaluation.v1_1.offline_runner import (  # noqa: E402
    OfflineEvaluationError,
    capture_cli_transport,
    execute_twice,
    safe_candidate_json,
    safe_report_json,
    write_report,
)


def _external_network_syscalls(trace_dir: Path) -> list[str]:
    findings: list[str] = []
    for path in sorted(trace_dir.glob("network.trace*")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "AF_INET" in line or "AF_INET6" in line or "sockaddr_in" in line:
                findings.append(line.split("(", 1)[0])
    return findings


def _worker() -> int:
    sys.stdout.write(safe_candidate_json(execute_twice()))
    return 0


def run_observed() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="mdrack-v11-q1-strace-") as temporary:
        trace_dir = Path(temporary)
        completed = subprocess.run(
            [
                "strace",
                "-ff",
                "-qq",
                "-e",
                "trace=%network",
                "-o",
                str(trace_dir / "network.trace"),
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if completed.returncode != 0:
            raise OfflineEvaluationError("observed Q1 worker failed")
        findings = _external_network_syscalls(trace_dir)
        if findings:
            raise OfflineEvaluationError("external network syscall detected")
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise OfflineEvaluationError("Q1 worker returned an invalid report")
        captured = capture_cli_transport(
            payload,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        execution = captured.get("execution")
        if not isinstance(execution, dict):
            raise OfflineEvaluationError("Q1 worker returned invalid execution evidence")
        execution["network_syscalls"] = 0
        execution["network_syscalls_observed"] = True
        return captured


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.worker:
        return _worker()
    report = run_observed()
    if args.output_dir is not None:
        report = write_report(report, args.output_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="mdrack-v11-q1-report-") as temporary:
            report = write_report(report, Path(temporary))
    sys.stdout.write(safe_report_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
