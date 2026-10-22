#!/usr/bin/env python3
"""Evaluate an explicit quality-v2 ranked input, or validate/read a v2 report.

No ranker, extraction, provider or network is invoked. Legacy media_quality_eval
and frozen v1 inputs/reports are intentionally not migrated by this command.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from mdrack.eval.quality import QualityEvaluationError
from mdrack.eval.quality_io import dumps_quality_report, loads_quality_input, loads_quality_report
from mdrack.eval.quality_v2 import evaluate_quality_v2

MAX_INPUT_BYTES = 8 * 1024 * 1024


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise QualityEvaluationError("invalid quality arguments")


def _read(path: Path) -> str:
    with path.open("rb") as stream:
        data = stream.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise QualityEvaluationError("quality input exceeds limit")
    return data.decode("utf-8")


def _write(path: Path, text: str) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".quality-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text.encode("utf-8"))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    try:
        parser = _Parser(description=__doc__)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--input", type=Path, help="schema-2 input with caller-ranked results")
        group.add_argument("--report", type=Path, help="read and validate an existing schema-2 report")
        parser.add_argument("--output", type=Path, help="optional atomic report destination (default: stdout)")
        args = parser.parse_args()
        if args.input:
            report = evaluate_quality_v2(loads_quality_input(_read(args.input)))
        else:
            report = loads_quality_report(_read(args.report))
        encoded = dumps_quality_report(report)
        if args.output:
            _write(args.output, encoded)
            print(json.dumps({"ok": True, "cases": report["cases"], "schema_version": 2}))
        else:
            sys.stdout.write(encoded)
        return 0
    except (QualityEvaluationError, OSError, UnicodeError, RecursionError):
        print('{"ok":false,"error":"quality_evaluation_failed"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
