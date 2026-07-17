#!/usr/bin/env python3
"""Check that no forbidden ML/vector-DB dependencies are present."""
import ast
import sys
import tomllib
from pathlib import Path

try:
    from scripts.check_core_boundaries import check_repository as check_core_repository
except ModuleNotFoundError:  # Direct execution puts scripts/ first on sys.path.
    from check_core_boundaries import check_repository as check_core_repository

FORBIDDEN = {
    "torch", "transformers", "sentence-transformers",
    "qdrant-client", "chromadb", "lancedb",
    "faiss", "faiss-cpu", "faiss-gpu",
    "tensorflow", "keras", "onnxruntime",
}

def normalize(name: str) -> str:
    """Normalize package name for comparison."""
    return name.lower().replace("_", "-").split("[")[0].split(">")[0].split("<")[0].split("=")[0].strip()

def check_pyproject() -> list[str]:
    violations = []
    pyproject = Path("pyproject.toml")
    if not pyproject.exists():
        return violations
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    deps = data.get("project", {}).get("dependencies", [])
    dev_deps = data.get("project", {}).get("optional-dependencies", {}).get("dev", [])
    for dep in deps + dev_deps:
        norm = normalize(dep)
        if norm in FORBIDDEN:
            violations.append(f"pyproject.toml: forbidden dependency '{norm}'")
    return violations

def check_imports() -> list[str]:
    violations = []
    src_dir = Path("src/mdrack")
    if not src_dir.exists():
        return violations
    for py_file in src_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in FORBIDDEN:
                        violations.append(f"{py_file}: forbidden import '{alias.name}'")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    if top in FORBIDDEN:
                        violations.append(f"{py_file}: forbidden import '{node.module}'")
    return violations

def check_core_boundaries() -> list[str]:
    """Include the mandatory provider/storage-neutral core boundary gate."""
    return [f"core boundary: {violation.render()}" for violation in check_core_repository()]


def main() -> int:
    violations = check_pyproject() + check_imports() + check_core_boundaries()
    if violations:
        print("FORBIDDEN DEPENDENCIES FOUND:")
        for v in violations:
            print(f"  ✗ {v}")
        return 1
    print("No forbidden dependencies found.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
