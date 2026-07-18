#!/usr/bin/env python3
"""Enforce the standalone, provider/storage-neutral ``mdrack_core`` boundary."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

CORE_RELATIVE_PATH = Path("packages/mdrack-core/src/mdrack_core")

# These stdlib modules provide infrastructure behavior that the pure core must not own.
FORBIDDEN_STDLIB_IMPORT_ROOTS = {
    "ftplib",
    "http",
    "os",
    "pathlib",
    "shutil",
    "socket",
    "sqlite3",
    "subprocess",
    "tempfile",
    "urllib",
}

# Concrete application/integration packages are forbidden even if installed locally.
FORBIDDEN_IMPORT_ROOTS = {
    "click",
    "httpx",
    "markdown_it",
    "mdrack",
    "psycopg",
    "requests",
    *FORBIDDEN_STDLIB_IMPORT_ROOTS,
}

# Scan executable identifiers only. Comments, docstrings, and ordinary string values are
# intentionally ignored so contracts can explain the boundary without false positives.
FORBIDDEN_IDENTIFIER_PARTS = {
    "click",
    "database",
    "endpoint",
    "filesystem",
    "http",
    "lmstudio",
    "markdown",
    "model",
    "network",
    "parser",
    "postgres",
    "provider",
    "sqlite",
    "storage",
    "url",
}
FORBIDDEN_CALLS = {"open"}
_DYNAMIC_IMPORT_CALLS = {"__import__", "import_module"}


@dataclass(frozen=True, order=True)
class Violation:
    """One deterministic, content-free core-boundary finding."""

    path: str
    line: int
    column: int
    category: str
    detail: str

    def render(self) -> str:
        return f"{self.path}:{self.line}:{self.column}: {self.category}: {self.detail}"


def _identifier_parts(identifier: str) -> set[str]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", identifier)
    return {part for part in re.split(r"[^A-Za-z0-9]+", expanded.lower()) if part}


def _import_root(module: str) -> str:
    return module.split(".", 1)[0].lower()


def _node_location(node: ast.AST) -> tuple[int, int]:
    return getattr(node, "lineno", 1), getattr(node, "col_offset", 0) + 1


def _violation(path: Path, node: ast.AST, category: str, detail: str) -> Violation:
    line, column = _node_location(node)
    return Violation(path.as_posix(), line, column, category, detail)


def _check_import(path: Path, node: ast.AST, module: str) -> list[Violation]:
    root = _import_root(module)
    if root == "mdrack":
        return [_violation(path, node, "reverse-import", "mdrack_core must not import mdrack")]
    if root in FORBIDDEN_IMPORT_ROOTS:
        return [_violation(path, node, "infrastructure-import", f"forbidden import root '{root}'")]
    if root not in sys.stdlib_module_names and root != "mdrack_core":
        return [_violation(path, node, "third-party-import", f"non-stdlib import root '{root}'")]
    return []


def _iter_identifiers(tree: ast.AST) -> list[tuple[ast.AST, str]]:
    identifiers: list[tuple[ast.AST, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            identifiers.append((node, node.name))
        elif isinstance(node, ast.arg):
            identifiers.append((node, node.arg))
        elif isinstance(node, ast.keyword) and node.arg:
            identifiers.append((node, node.arg))
        elif isinstance(node, ast.Name):
            identifiers.append((node, node.id))
        elif isinstance(node, ast.Attribute):
            identifiers.append((node, node.attr))
        elif isinstance(node, ast.alias):
            identifiers.append((node, node.name))
            if node.asname:
                identifiers.append((node, node.asname))
    return identifiers


def _dynamic_import_aliases(tree: ast.AST) -> set[str]:
    aliases = set(_DYNAMIC_IMPORT_CALLS)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level != 0 or node.module != "importlib":
            continue
        for alias in node.names:
            if alias.name == "import_module":
                aliases.add(alias.asname or alias.name)
    return aliases


def _constant_import_name(node: ast.Call) -> str | None:
    candidate: ast.expr | None = node.args[0] if node.args else None
    if candidate is None:
        candidate = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "name"),
            None,
        )
    if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
        return candidate.value
    return None


def check_python_file(path: Path, *, display_path: Path | None = None) -> list[Violation]:
    """Return boundary violations for one core Python file."""
    shown = display_path or path
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=shown.as_posix())
    except UnicodeDecodeError:
        return [Violation(shown.as_posix(), 1, 1, "invalid-source", "source is not UTF-8")]
    except SyntaxError as exc:
        return [
            Violation(
                shown.as_posix(),
                exc.lineno or 1,
                exc.offset or 1,
                "invalid-source",
                "source does not parse",
            )
        ]

    violations: list[Violation] = []
    dynamic_import_aliases = _dynamic_import_aliases(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                violations.extend(_check_import(shown, node, alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            # Relative imports stay inside mdrack_core. A named ``mdrack`` escape is
            # still rejected, while ``from .domain import ...`` remains valid.
            if node.level == 0 or _import_root(node.module) == "mdrack":
                violations.extend(_check_import(shown, node, node.module))
        elif isinstance(node, ast.Call):
            call_name = ""
            is_dynamic_import = False
            if isinstance(node.func, ast.Name):
                call_name = node.func.id
                is_dynamic_import = call_name in dynamic_import_aliases
            elif isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
                is_dynamic_import = call_name in _DYNAMIC_IMPORT_CALLS
            if call_name in FORBIDDEN_CALLS:
                violations.append(
                    _violation(shown, node, "infrastructure-call", f"forbidden call '{call_name}'")
                )
            if is_dynamic_import:
                import_name = _constant_import_name(node)
                if import_name and not import_name.startswith("."):
                    violations.extend(_check_import(shown, node, import_name))

    for node, identifier in _iter_identifiers(tree):
        forbidden = sorted(_identifier_parts(identifier) & FORBIDDEN_IDENTIFIER_PARTS)
        if forbidden:
            violations.append(
                _violation(
                    shown,
                    node,
                    "forbidden-identifier",
                    f"identifier '{identifier}' contains forbidden part(s): {', '.join(forbidden)}",
                )
            )
    return sorted(set(violations))


def check_repository(root: Path = Path(".")) -> list[Violation]:
    """Check only the standalone core source; application imports remain allowed."""
    root = root.resolve()
    core_root = root / CORE_RELATIVE_PATH
    if not core_root.exists():
        return []
    if not core_root.is_dir():
        return [
            Violation(
                CORE_RELATIVE_PATH.as_posix(),
                1,
                1,
                "invalid-layout",
                "core import root must be a directory",
            )
        ]

    violations: list[Violation] = []
    for path in sorted(core_root.rglob("*.py")):
        display_path = path.relative_to(root)
        violations.extend(check_python_file(path, display_path=display_path))
    return sorted(violations)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root")
    args = parser.parse_args(argv)

    violations = check_repository(args.root)
    if violations:
        print("CORE BOUNDARY VIOLATIONS FOUND:")
        for violation in violations:
            print(f"  - {violation.render()}")
        return 1

    core_root = args.root / CORE_RELATIVE_PATH
    state = "present" if core_root.is_dir() else "absent; pre-implementation guard active"
    print(f"Core boundary check passed ({CORE_RELATIVE_PATH.as_posix()} {state}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
