#!/usr/bin/env python3
"""Enforce the standalone ``mdrack_sqlite`` dependency boundary."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PACKAGE_ROOT = Path("packages/mdrack-sqlite/src/mdrack_sqlite")
ALLOWED_NON_STDLIB_ROOTS = {"mdrack_core", "mdrack_sqlite"}


def violations(root: Path = Path(".")) -> list[str]:
    """Return deterministic import-boundary violations."""
    findings: list[str] = []
    package_root = root.resolve() / PACKAGE_ROOT
    for path in sorted(package_root.rglob("*.py")):
        display = path.relative_to(root.resolve()).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=display)
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.append(node.module)
            line = getattr(node, "lineno", 1)
            for module in modules:
                import_root = module.split(".", 1)[0]
                if import_root == "mdrack":
                    findings.append(f"{display}:{line}: reverse import mdrack")
                elif import_root not in sys.stdlib_module_names and import_root not in ALLOWED_NON_STDLIB_ROOTS:
                    findings.append(f"{display}:{line}: third-party import {import_root}")
    return sorted(set(findings))


def main() -> int:
    findings = violations()
    if findings:
        print("SQLITE BOUNDARY VIOLATIONS FOUND:")
        for finding in findings:
            print(f"  - {finding}")
        return 1
    print(f"SQLite boundary check passed ({PACKAGE_ROOT.as_posix()}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
