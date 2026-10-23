"""Check the unchanged original v1 freeze in an isolated original-input view.

The four named v2 schemas share a directory, not the historical v1 contract.
Nothing in the source tree is generated or rewritten by this adapter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "tests/evaluation/v1_1/freeze-manifest.json"
GENERATOR = "scripts/build_v11_test_assets.py"
DIGEST = "sha256:8e75cc894c8cc373e70b7f9778a8e806daca65922c471ae9efd965b4d99f780b"
V2_ADDITIONS = frozenset(
    "tests/evaluation/schemas/" + name + ".schema.json"
    for name in ("native-corpus-v2", "native-experiment-v2", "quality-input-v2", "quality-report-v2")
)
DIRECTORIES = (
    "tests/evaluation/corpus-v1", "tests/evaluation/queries-v1", "tests/evaluation/benchmark-v1",
    "tests/evaluation/schemas", "tests/assets/v1_1",
)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _path(root: Path, name: str) -> Path:
    parts = PurePosixPath(name)
    if not name or "\\" in name or parts.is_absolute() or ":" in name or ".." in parts.parts:
        raise ValueError("input_path_invalid")
    if parts.as_posix() != name:
        raise ValueError("input_path_invalid")
    path = root / name
    if not path.resolve().is_relative_to(root) or any(
        p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root)
    ):
        raise ValueError("input_path_escape")
    return path


def check(root: Path, *, temporary_parent: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    manifest_bytes = _path(root, MANIFEST).read_bytes()
    manifest = json.loads(manifest_bytes, object_pairs_hook=_pairs)
    inputs = manifest["input_sha256"]
    if not isinstance(inputs, dict) or len(inputs) != 66:
        raise ValueError("input_inventory_invalid")
    folded = {name.casefold() for name in inputs}
    if len(folded) != len(inputs):
        raise ValueError("duplicate_input_path")
    # Reject escapes and duplicate spellings before any input body is read.
    paths = {name: _path(root, name) for name in inputs}
    payload = {key: value for key, value in manifest.items() if key != "contract_digest"}
    canonical = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    actual_digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    if manifest.get("contract_digest") != DIGEST or actual_digest != DIGEST:
        raise ValueError("manifest_digest_invalid")
    observed = {name for name in inputs if not any(name.startswith(d + "/") for d in DIRECTORIES)}
    for directory in DIRECTORIES:
        for path in _path(root, directory).rglob("*"):
            if "__pycache__" in path.parts:
                continue
            if path.is_symlink():
                raise ValueError("input_path_escape")
            if path.is_file():
                observed.add(path.relative_to(root).as_posix())
    if observed - set(inputs) != V2_ADDITIONS or set(inputs) - observed:
        raise ValueError("input_inventory_changed")
    bodies = {name: path.read_bytes() for name, path in paths.items()}
    if any("sha256:" + hashlib.sha256(body).hexdigest() != inputs[name] for name, body in bodies.items()):
        raise ValueError("input_digest_changed")
    with tempfile.TemporaryDirectory(prefix="mdrack-v1-freeze-", dir=temporary_parent) as directory:
        view = Path(directory)
        for name, body in {**bodies, MANIFEST: manifest_bytes}.items():
            path = view / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-S", str(view / GENERATOR), "--check"],
            cwd=view, capture_output=True, text=True, encoding="utf-8", check=False,
        )
        if completed.returncode:
            raise ValueError("original_generator_failed")
        summary = json.loads(completed.stdout)
        if summary.get("contract_digest") != DIGEST or summary.get("input_files") != 66:
            raise ValueError("original_generator_identity_invalid")
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        summary = check(args.root)
    except (OSError, ValueError, KeyError, TypeError):
        print('{"status":"invalid","reason":"v1_input_freeze_refused"}')
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
