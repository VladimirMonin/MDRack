#!/usr/bin/env python3
"""Write the privacy-safe MDRack 1.1 Q1 evaluation result."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_v11_offline_e2e.py"),
            "--output-dir",
            str(args.output_dir),
        ],
        cwd=ROOT,
        check=False,
        text=True,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
