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
import tarfile
import tempfile
import tomllib
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
CANONICAL_LICENSE = (REPO_ROOT / "LICENSE").read_bytes()
LOCK_PATH = REPO_ROOT / "uv.lock"
THIRD_PARTY_LEDGER = REPO_ROOT / "THIRD_PARTY_NOTICES.md"
REQUIRED_PUBLICATION_OUTPUTS = {
    "docs/evidence/w5-offline-release-matrix.json",
    "docs/evidence/v0.4-release-packet.json",
    "docs/evidence/v0.4-release-packet.md",
    "scripts/check_release_docs.py",
}


def _run(command: list[str], *, cwd: Path, offline: bool = True) -> None:
    env = os.environ.copy()
    if offline:
        env["UV_OFFLINE"] = "1"
    else:
        env.pop("UV_OFFLINE", None)
    env.setdefault("SOURCE_DATE_EPOCH", "0")
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _candidate_contract(packet_path: Path) -> tuple[str, list[dict[str, str]], list[str]]:
    """Load and fail closed on the build/publication boundary."""
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    snapshot = packet.get("candidate_snapshot", {})
    base_revision = snapshot.get("committed_base_revision")
    build_inputs = snapshot.get("build_inputs")
    publication_outputs = snapshot.get("publication_outputs")
    excluded_paths = snapshot.get("excluded_paths")
    if (
        not isinstance(base_revision, str)
        or re.fullmatch(r"[0-9a-f]{40}", base_revision) is None
        or snapshot.get("verification_scope")
        != "committed_base_plus_content_addressed_build_inputs"
        or snapshot.get("build_input_status") != "uncommitted"
        or not isinstance(build_inputs, list)
        or not build_inputs
        or not isinstance(publication_outputs, list)
        or not publication_outputs
        or not isinstance(excluded_paths, list)
    ):
        raise ValueError("invalid candidate snapshot contract")
    normalized_inputs: list[dict[str, str]] = []
    build_paths: list[str] = []
    for item in build_inputs:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError("invalid candidate build input")
        path = item.get("path")
        digest = item.get("sha256")
        if (
            not isinstance(path, str)
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ValueError("invalid candidate build input")
        source = (REPO_ROOT / path).resolve()
        try:
            source.relative_to(REPO_ROOT)
        except ValueError as error:
            raise ValueError("candidate build input escapes repository") from error
        if not source.is_file() or _sha256(source).removeprefix("sha256:") != digest:
            raise ValueError(f"candidate build input digest mismatch: {path}")
        build_paths.append(path)
        normalized_inputs.append({"path": path, "sha256": digest})
    if len(build_paths) != len(set(build_paths)):
        raise ValueError("duplicate candidate build input")
    if any(not isinstance(path, str) for path in publication_outputs):
        raise ValueError("invalid publication output")
    if len(publication_outputs) != len(set(publication_outputs)):
        raise ValueError("duplicate publication output")
    if set(build_paths) & set(publication_outputs):
        raise ValueError("publication output cannot be a candidate build input")
    if not REQUIRED_PUBLICATION_OUTPUTS.issubset(publication_outputs):
        raise ValueError("candidate publication outputs are incomplete")
    if "docs/plans/2026-07-20-v1.1-implementation-plan.md" not in excluded_paths:
        raise ValueError("v1.1 plan must be excluded from the v0.4 candidate")
    subprocess.run(
        ["git", "cat-file", "-e", f"{base_revision}^{{commit}}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return base_revision, normalized_inputs, publication_outputs


def _materialize_candidate(packet_path: Path, destination: Path) -> None:
    """Create base + build inputs without copying publication outputs."""
    base_revision, build_inputs, publication_outputs = _candidate_contract(packet_path)
    destination.mkdir(parents=True, exist_ok=False)
    archive_path = destination.parent / f"{destination.name}.tar"
    try:
        with archive_path.open("wb") as stream:
            subprocess.run(
                ["git", "archive", "--format=tar", base_revision],
                cwd=REPO_ROOT,
                check=True,
                stdout=stream,
            )
        with tarfile.open(archive_path) as archive:
            for member in archive.getmembers():
                target = (destination / member.name).resolve()
                try:
                    target.relative_to(destination.resolve())
                except ValueError as error:
                    raise ValueError("candidate archive member escapes destination") from error
            archive.extractall(destination)
    finally:
        archive_path.unlink(missing_ok=True)
    for item in build_inputs:
        source = REPO_ROOT / item["path"]
        target = destination / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for path in publication_outputs:
        current = destination / path
        base = subprocess.run(
            ["git", "show", f"{base_revision}:{path}"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
        )
        if base.returncode == 0:
            if not current.is_file() or current.read_bytes() != base.stdout:
                raise RuntimeError(f"publication output changed candidate bytes: {path}")
        elif current.exists():
            raise RuntimeError(f"publication-only path entered candidate: {path}")
    excluded = destination / "docs/plans/2026-07-20-v1.1-implementation-plan.md"
    if excluded.exists():
        raise RuntimeError("v1.1 plan entered the v0.4 candidate")


def _metadata_text(artifact: Path) -> tuple[str, list[str]]:
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
    return text, members


def _metadata(artifact: Path) -> tuple[str, str, list[str], list[str]]:
    text, members = _metadata_text(artifact)
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


def _locked_runtime() -> tuple[dict[str, str], dict[str, str]]:
    """Return the locked non-dev runtime closure and its conditional markers."""
    lock = tomllib.loads(LOCK_PATH.read_text(encoding="utf-8"))
    packages = {item["name"]: item for item in lock["package"]}
    root = packages["mdrack"]["metadata"]["requires-dist"]
    pending = [(item["name"], item.get("marker", "")) for item in root]
    versions: dict[str, str] = {}
    markers: dict[str, str] = {}
    while pending:
        name, marker = pending.pop()
        if "extra == 'dev'" in marker:
            continue
        if name.startswith("mdrack"):
            # The four local distributions are audited separately from the
            # third-party licensing ledger.
            continue
        if name in versions:
            if marker:
                markers[name] = marker
            continue
        package = packages.get(name)
        if package is None or "version" not in package:
            raise RuntimeError(f"locked runtime dependency is missing: {name}")
        versions[name] = package["version"]
        if marker:
            markers[name] = marker
        for dependency in package.get("dependencies", []):
            pending.append((dependency["name"], dependency.get("marker", "")))
    return dict(sorted(versions.items())), dict(sorted(markers.items()))


def _runtime_ledger() -> dict[str, str]:
    """Read the exact resolver rows from the committed licensing ledger."""
    pattern = re.compile(r"^\| `([^`]+)` ([^ ]+) —")
    rows = {
        match.group(1): match.group(2)
        for line in THIRD_PARTY_LEDGER.read_text(encoding="utf-8").splitlines()
        if (match := pattern.match(line))
    }
    return rows


def _validate_runtime_ledger(runtime: dict[str, str]) -> None:
    ledger = _runtime_ledger()
    missing = sorted(set(runtime) - set(ledger))
    mismatched = sorted(name for name in runtime if ledger.get(name) != runtime[name])
    if missing or mismatched:
        raise RuntimeError(
            f"runtime ledger mismatch: missing={missing}, version_mismatch={mismatched}"
        )


def _install_graph(artifacts: list[dict[str, Any]]) -> dict[str, list[str]]:
    production_edges: set[str] = set()
    dev_extra_edges: set[str] = set()
    for artifact in artifacts:
        for dependency in artifact["dependencies"]:
            requirement = dependency.split(";", 1)[0].strip()
            dependency_name = re.split(r"[<>=!~]", requirement, maxsplit=1)[0].strip()
            edge = f"{artifact['distribution']} -> {dependency_name}"
            if "extra == 'dev'" in dependency:
                dev_extra_edges.add(edge)
            else:
                production_edges.add(edge)
    return {
        "production_edges": sorted(production_edges),
        "dev_extra_edges": sorted(dev_extra_edges),
    }


def _validate_installed_runtime(expected: dict[str, str], installed: dict[str, str]) -> None:
    """Reject drift or missing packages in the installed smoke environment."""
    if installed != expected:
        raise RuntimeError(
            f"installed runtime closure mismatch: expected={expected}, installed={installed}"
        )


def _license_payload(artifact: Path) -> bytes:
    """Return the sole packaged LICENSE file, failing closed when absent."""
    if artifact.suffix == ".whl":
        with ZipFile(artifact) as archive:
            candidates = [name for name in archive.namelist() if Path(name).name == "LICENSE"]
            if len(candidates) != 1:
                raise RuntimeError(f"expected one LICENSE in {artifact.name}, found {candidates}")
            return archive.read(candidates[0])
    with tarfile.open(artifact) as archive:
        candidates = [member for member in archive.getmembers() if Path(member.name).name == "LICENSE"]
        if len(candidates) != 1:
            raise RuntimeError(f"expected one LICENSE in {artifact.name}, found {len(candidates)}")
        payload = archive.extractfile(candidates[0])
        if payload is None:
            raise RuntimeError(f"LICENSE in {artifact.name} is not a regular file")
        return payload.read()


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
            metadata_text, _ = _metadata_text(artifact)
            if "License-Expression: MIT" not in metadata_text:
                raise RuntimeError(f"artifact {artifact.name} does not declare MIT")
            if _license_payload(artifact) != CANONICAL_LICENSE:
                raise RuntimeError(f"artifact {artifact.name} contains a non-canonical LICENSE")
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


def _installed_smoke(
    output_dir: Path,
    *,
    allow_index_provisioning: bool = False,
) -> dict[str, Any]:
    runtime, markers = _locked_runtime()
    _validate_runtime_ledger(runtime)
    expected_runtime = {
        name: version
        for name, version in runtime.items()
        if not markers.get(name) or "sys_platform == 'win32'" not in markers[name] or os.name == "nt"
    }
    cells: list[dict[str, Any]] = []
    for package, _ in PACKAGE_SPECS:
        for kind in ("wheel", "sdist"):
            cell_expected_runtime = expected_runtime if package == "mdrack" else {}
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
                constraints = Path(temp) / "runtime-constraints.txt"
                constraints.write_text(
                    "".join(f"{name}=={version}\n" for name, version in runtime.items()),
                    encoding="utf-8",
                )
                install_command = ["uv", "pip", "install"]
                if not allow_index_provisioning:
                    install_command.append("--offline")
                install_command.extend(
                    [
                        "--python", str(python), "--find-links", str(output_dir),
                        "--constraint", str(constraints), str(artifact),
                    ]
                )
                _run(
                    install_command,
                    cwd=REPO_ROOT,
                    offline=not allow_index_provisioning,
                )
                expected_version = _metadata(artifact)[1]
                repo_root_literal = repr(str(REPO_ROOT))
                smoke_code = (
                    "import importlib, importlib.metadata as m, json, pathlib, re; "
                    f"dist={package!r}; expected={expected_version!r}; mods={PACKAGE_IMPORTS[package]!r}; "
                    f"runtime_expected={cell_expected_runtime!r}; "
                    "loaded=[importlib.import_module(name) for name in mods]; "
                    "installed_runtime={re.sub(r'[-_.]+', '-', dist.metadata['Name']).lower(): dist.version "
                    "for dist in m.distributions() if not dist.metadata['Name'].lower().startswith('mdrack')}; "
                    "assert m.version(dist) == expected; "
                    "assert {name: m.version(name) for name in runtime_expected} == runtime_expected; "
                    "assert all(not str(pathlib.Path(mod.__file__).resolve()).startswith("
                    f"{repo_root_literal}) for mod in loaded); "
                    "print(json.dumps({'version': m.version(dist), 'modules': mods, "
                    "'files': sorted(str(pathlib.Path(mod.__file__).resolve()) for mod in loaded), "
                    "'installed_runtime': installed_runtime}))"
                )
                result = subprocess.run(
                    [str(python), "-c", smoke_code],
                    cwd=temp,
                    env={**os.environ, "UV_OFFLINE": "1", "PYTHONPATH": ""},
                    check=False, capture_output=True, text=True,
                )
                if result.returncode:
                    raise RuntimeError(f"installed smoke failed for {artifact.name}: {result.stderr[-2_000:]}")
                installed = json.loads(result.stdout)
                _validate_installed_runtime(cell_expected_runtime, installed["installed_runtime"])
                if package == "mdrack" and kind == "wheel":
                    result = subprocess.run(
                        [str(python), str(REPO_ROOT / "scripts" / "check_installed_package.py")],
                        cwd=temp,
                        env={**os.environ, "UV_OFFLINE": "1", "PYTHONPATH": ""},
                        check=False, capture_output=True, text=True,
                    )
                    if result.returncode:
                        raise RuntimeError(f"installed package smoke failed: {result.stderr[-2_000:]}")
                cells.append(
                    {
                        "distribution": package,
                        "kind": kind,
                        "artifact": artifact.name,
                        "status": "ok",
                        "stdout_sha256": f"sha256:{hashlib.sha256(result.stdout.encode()).hexdigest()}",
                    }
                )
    return {"status": "ok", "cells": cells, "cell_count": len(cells)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--candidate-packet",
        type=Path,
        help="materialize the packet's base + build inputs before building",
    )
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument(
        "--allow-index-for-smoke-provisioning",
        action="store_true",
        help="allow only the constrained dependency-install step to use the package index",
    )
    parser.add_argument("--expected-manifest", type=Path)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    _validate_output_dir(output_dir)
    if args.candidate_packet:
        packet_path = args.candidate_packet.resolve()
        with tempfile.TemporaryDirectory(prefix="mdrack-release-candidate-") as temp:
            candidate = Path(temp) / "candidate"
            _materialize_candidate(packet_path, candidate)
            command = [
                sys.executable,
                str(candidate / "scripts/offline_release_matrix.py"),
                "--output-dir",
                str(output_dir),
            ]
            if args.skip_build:
                command.append("--skip-build")
            if args.smoke:
                command.append("--smoke")
            if args.allow_index_for_smoke_provisioning:
                command.append("--allow-index-for-smoke-provisioning")
            if args.expected_manifest:
                command.extend(
                    ["--expected-manifest", str(args.expected_manifest.resolve())]
                )
            _run(command, cwd=candidate)
        return
    if not args.skip_build:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        _build(output_dir)
    manifest = _audit_artifacts(output_dir)
    if args.expected_manifest:
        _check_expected_hashes(manifest, args.expected_manifest.resolve())
    runtime, _ = _locked_runtime()
    _validate_runtime_ledger(runtime)
    manifest["locked_runtime"] = runtime
    manifest["install_graph"] = {
        "nodes": sorted({artifact["distribution"] for artifact in manifest["artifacts"]}),
        **_install_graph(manifest["artifacts"]),
    }
    if args.smoke:
        manifest["installed_smoke"] = _installed_smoke(
            output_dir,
            allow_index_provisioning=args.allow_index_for_smoke_provisioning,
        )
    manifest.update(
        {
            "schema_version": 1,
            "generated_for": "offline-release-matrix",
            "network": {
                "allowed": args.allow_index_for_smoke_provisioning,
                "dependency_provisioning": (
                    "constrained_package_index"
                    if args.allow_index_for_smoke_provisioning
                    else "offline_cache_or_find_links"
                ),
                "telemetry": "not_measured",
                "controls": ["UV_OFFLINE=1", "installed-smoke-socket-block"],
            },
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
