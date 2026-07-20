#!/usr/bin/env python3
"""Build and audit MDRack distributions without contacting package indexes.

The command is intentionally offline-first.  It produces a privacy-safe manifest
that records artifact hashes, dependency metadata, and package-shadowing checks.
The optional installed smoke runs in a temporary virtual environment and never
falls back to source-tree imports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from typing import Any
from zipfile import ZipFile

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SPECS = (
    ("mdrack", REPO_ROOT),
    ("mdrack-core", REPO_ROOT / "packages" / "mdrack-core"),
    ("mdrack-media", REPO_ROOT / "packages" / "mdrack-media"),
    ("mdrack-sqlite", REPO_ROOT / "packages" / "mdrack-sqlite"),
)
EXPECTED_LOCAL_DEPENDENCIES = {
    "mdrack-core": (),
    "mdrack-media": ("mdrack-core==1.0.0rc1",),
    "mdrack-sqlite": ("mdrack-core==1.0.0rc1",),
}
VERSION_RE = re.compile(r"^Version: (?P<version>.+)$", re.MULTILINE)
NAME_RE = re.compile(r"^Name: (?P<name>.+)$", re.MULTILINE)
PACKAGE_IMPORTS = {
    "mdrack": ("mdrack",),
    "mdrack-core": ("mdrack_core",),
    "mdrack-media": ("mdrack_media",),
    "mdrack-sqlite": ("mdrack_sqlite",),
}


def _run(command: list[str], *, cwd: Path) -> None:
    env = os.environ.copy()
    env["UV_OFFLINE"] = "1"
    env.setdefault("SOURCE_DATE_EPOCH", "0")
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _metadata(artifact: Path) -> tuple[str, str, list[str], list[str]]:
    if artifact.suffix == ".whl":
        with ZipFile(artifact) as archive:
            metadata_path = next(name for name in archive.namelist() if name.endswith("/METADATA"))
            members = archive.namelist()
            text = archive.read(metadata_path).decode("utf-8")
    else:
        import tarfile

        with tarfile.open(artifact) as archive:
            metadata_member = next(member for member in archive.getmembers() if member.name.endswith("/PKG-INFO"))
            payload = archive.extractfile(metadata_member)
            assert payload is not None
            text = payload.read().decode("utf-8")
            members = [member.name for member in archive.getmembers()]
    name = NAME_RE.search(text)
    version = VERSION_RE.search(text)
    if name is None or version is None:
        raise ValueError(f"missing package metadata in {artifact.name}")
    dependencies = [
        line.removeprefix("Requires-Dist: ")
        for line in text.splitlines()
        if line.startswith("Requires-Dist: ")
    ]
    return name.group("name"), version.group("version"), dependencies, members


def _audit_artifacts(output_dir: Path) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    package_names: set[str] = set()
    for package, _ in PACKAGE_SPECS:
        candidates = sorted(output_dir.glob(f"{package.replace('-', '_')}-*"))
        if len(candidates) != 2 or {path.suffix for path in candidates} != {".whl", ".gz"}:
            raise RuntimeError(f"expected wheel and sdist for {package}, found {[path.name for path in candidates]}")
        for artifact in candidates:
            name, version, dependencies, members = _metadata(artifact)
            package_names.add(name)
            if name != package:
                raise RuntimeError(f"artifact {artifact.name} declares {name}, expected {package}")
            if package in {"mdrack-core", "mdrack-media", "mdrack-sqlite"} and any(
                member.startswith("mdrack/") for member in members
            ):
                raise RuntimeError(f"standalone artifact {artifact.name} shadows the app package")
            local_dependencies = tuple(
                dependency
                for dependency in dependencies
                if dependency.split(";", 1)[0].strip().startswith("mdrack")
            )
            if (
                package in EXPECTED_LOCAL_DEPENDENCIES
                and local_dependencies != EXPECTED_LOCAL_DEPENDENCIES[package]
            ):
                raise RuntimeError(f"unexpected local dependencies for {package}: {local_dependencies}")
            artifacts.append(
                {
                    "distribution": name,
                    "version": version,
                    "kind": "wheel" if artifact.suffix == ".whl" else "sdist",
                    "filename": artifact.name,
                    "sha256": _sha256(artifact),
                    "dependencies": sorted(dependencies),
                }
            )
    if package_names != {package for package, _ in PACKAGE_SPECS}:
        raise RuntimeError(f"unexpected distributions: {sorted(package_names)}")
    return {"artifacts": artifacts, "artifact_count": len(artifacts)}


def _build(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for _, directory in PACKAGE_SPECS:
        _run(["uv", "build", "--offline", "--out-dir", str(output_dir)], cwd=directory)


def _validate_output_dir(output_dir: Path) -> None:
    """Reject artifact output inside the source checkout."""
    try:
        output_dir.relative_to(REPO_ROOT)
    except ValueError:
        return
    raise ValueError(f"output directory must be outside the repository: {output_dir}")


def _check_expected_hashes(manifest: dict[str, Any], expected_path: Path) -> None:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected_hashes = {
        (item["distribution"], item["kind"]): item["sha256"]
        for item in expected.get("artifacts", [])
    }
    actual_hashes = {
        (item["distribution"], item["kind"]): item["sha256"]
        for item in manifest["artifacts"]
    }
    if expected_hashes != actual_hashes:
        raise RuntimeError(
            f"artifact hash mismatch: expected {expected_hashes}, actual {actual_hashes}"
        )


def _installed_smoke(output_dir: Path) -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    for package, _ in PACKAGE_SPECS:
        for kind in ("wheel", "sdist"):
            with tempfile.TemporaryDirectory(prefix="mdrack-release-smoke-") as temp:
                environment = Path(temp) / "venv"
                venv.EnvBuilder(with_pip=False).create(environment)
                python = environment / ("Scripts" if os.name == "nt" else "bin") / (
                    "python.exe" if os.name == "nt" else "python"
                )
                artifact_prefix = f"{package}-" if package == "mdrack" else f"{package.replace('-', '_')}-"
                artifact = next(
                    path for path in output_dir.iterdir()
                    if path.name.startswith(artifact_prefix)
                    and (path.suffix == ".whl" if kind == "wheel" else path.suffix == ".gz")
                )
                _run(
                    [
                        "uv", "pip", "install", "--offline", "--python", str(python),
                        "--find-links", str(output_dir), str(artifact),
                    ],
                    cwd=REPO_ROOT,
                )
                expected_version = _metadata(artifact)[1]
                repo_root_literal = repr(str(REPO_ROOT))
                smoke_code = (
                    "import importlib, importlib.metadata as m, json, pathlib; "
                    f"dist={package!r}; expected={expected_version!r}; mods={PACKAGE_IMPORTS[package]!r}; "
                    "loaded=[importlib.import_module(name) for name in mods]; "
                    "assert m.version(dist) == expected; "
                    "assert all(not str(pathlib.Path(mod.__file__).resolve()).startswith("
                    f"{repo_root_literal}) for mod in loaded); "
                    "print(json.dumps({'version': m.version(dist), 'modules': mods, "
                    "'files': sorted(str(pathlib.Path(mod.__file__).resolve()) for mod in loaded)}))"
                )
                result = subprocess.run(
                    [str(python), "-c", smoke_code],
                    cwd=temp,
                    env={**os.environ, "UV_OFFLINE": "1", "PYTHONPATH": ""},
                    check=False, capture_output=True, text=True,
                )
                if result.returncode:
                    raise RuntimeError(f"installed smoke failed for {artifact.name}: {result.stderr[-2_000:]}")
                if package == "mdrack" and kind == "wheel":
                    result = subprocess.run(
                        [str(python), str(REPO_ROOT / "scripts" / "check_installed_package.py")],
                        cwd=temp,
                        env={**os.environ, "UV_OFFLINE": "1", "PYTHONPATH": ""},
                        check=False, capture_output=True, text=True,
                    )
                    if result.returncode:
                        raise RuntimeError(f"installed package smoke failed: {result.stderr[-2_000:]}")
                cells.append({
                    "distribution": package, "kind": kind, "artifact": artifact.name,
                    "status": "ok", "stdout_sha256": f"sha256:{hashlib.sha256(result.stdout.encode()).hexdigest()}",
                })
    return {"status": "ok", "cells": cells, "cell_count": len(cells)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--expected-manifest", type=Path)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    _validate_output_dir(output_dir)
    if not args.skip_build:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        _build(output_dir)
    manifest = _audit_artifacts(output_dir)
    if args.expected_manifest:
        _check_expected_hashes(manifest, args.expected_manifest.resolve())
    manifest["install_graph"] = {
        "nodes": sorted({artifact["distribution"] for artifact in manifest["artifacts"]}),
        "edges": sorted(
            {
                f"{artifact['distribution']} -> {dependency.split(';', 1)[0].split('==', 1)[0]}"
                for artifact in manifest["artifacts"]
                for dependency in artifact["dependencies"]
                if not dependency.startswith("mdrack") or dependency.split(";", 1)[0].strip()
                in {"mdrack-core==1.0.0rc1", "mdrack-media==1.0.0rc1", "mdrack-sqlite==1.0.0rc1"}
            }
        ),
    }
    if args.smoke:
        manifest["installed_smoke"] = _installed_smoke(output_dir)
    manifest.update(
        {
            "schema_version": 1,
            "generated_for": "offline-release-matrix",
            "network": {"allowed": False, "attempts": 0},
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "platform": sys.platform,
        }
    )
    output_dir.joinpath("manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": True, "data": manifest}, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
