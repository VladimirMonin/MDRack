#!/usr/bin/env python3
"""Build and verify standalone ``mdrack-core`` artifacts without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from email.parser import Parser
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_SOURCE = REPO_ROOT / "packages" / "mdrack-core" / "src" / "mdrack_core"
EXTERNAL_SMOKE = (
    REPO_ROOT
    / "tests"
    / "external_core_consumer"
    / "test_memory_catalog_consumer.py"
)
EXPECTED_DISTRIBUTION_VERSION = "1.0.0rc1"
EXPECTED_CONTRACT_VERSION = "1.0.0-rc.1"


def _run(command: list[str], *, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "UV_OFFLINE": "1"}
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _single(path: Path, pattern: str) -> Path:
    matches = sorted(path.glob(pattern))
    if len(matches) != 1:
        raise AssertionError(f"expected one {pattern} artifact, found {len(matches)}")
    return matches[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _wheel_metadata(archive: zipfile.ZipFile) -> Any:
    metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
    if len(metadata_names) != 1:
        raise AssertionError("wheel must contain exactly one METADATA file")
    return Parser().parsestr(archive.read(metadata_names[0]).decode("utf-8"))


def _verify_core_archives(wheel: Path, sdist: Path) -> dict[str, int]:
    source_files = {
        path.relative_to(CORE_SOURCE).as_posix()
        for path in CORE_SOURCE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        package_files = {
            name.removeprefix("mdrack_core/")
            for name in names
            if name.startswith("mdrack_core/") and not name.endswith("/")
        }
        metadata = _wheel_metadata(archive)
        assert metadata["Name"] == "mdrack-core"
        assert metadata["Version"] == EXPECTED_DISTRIBUTION_VERSION
        assert metadata.get_all("Requires-Dist") in (None, [])
        assert package_files == source_files
        assert not any(name.startswith("mdrack/") for name in names)
        wheel_file_count = len(names)

    with tarfile.open(sdist, "r:gz") as archive:
        names = {member.name for member in archive.getmembers() if member.isfile()}
    roots = {name.split("/", 1)[0] for name in names}
    assert len(roots) == 1
    root = next(iter(roots))
    expected = {
        f"{root}/.gitignore",
        f"{root}/API.md",
        f"{root}/CHANGELOG.md",
        f"{root}/PKG-INFO",
        f"{root}/README.md",
        f"{root}/pyproject.toml",
        *(f"{root}/src/mdrack_core/{relative}" for relative in source_files),
    }
    assert names == expected
    return {
        "sdist_files": len(names),
        "source_files": len(source_files),
        "wheel_files": wheel_file_count,
    }


def _verify_app_archives(app_wheel: Path, app_sdist: Path) -> None:
    with zipfile.ZipFile(app_wheel) as archive:
        names = set(archive.namelist())
        metadata = _wheel_metadata(archive)
    assert not any(name.startswith("mdrack_core/") for name in names)
    requirements = metadata.get_all("Requires-Dist") or []
    assert "mdrack-core==1.0.0rc1" in requirements
    with tarfile.open(app_sdist, "r:gz") as archive:
        sdist_names = {member.name for member in archive.getmembers()}
    assert not any("/packages/mdrack-core/" in name for name in sdist_names)
    assert not any("/src/mdrack_core/" in name for name in sdist_names)


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _new_venv(path: Path) -> Path:
    _run(["uv", "venv", str(path), "--python", sys.executable])
    return _venv_python(path)


def _install_no_deps(python: Path, *artifacts: Path) -> None:
    _run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--no-deps",
            *(str(path) for path in artifacts),
        ]
    )


def _verify_core_install(python: Path, workdir: Path) -> str:
    _run([str(python), str(EXTERNAL_SMOKE)], cwd=workdir)
    probe = _run(
        [
            str(python),
            "-c",
            (
                "import importlib.metadata as m, json, mdrack_core; "
                "d=m.distribution('mdrack-core'); "
                "assert d.version=='1.0.0rc1'; "
                "assert not d.requires; "
                "assert mdrack_core.CORE_CONTRACT_VERSION=='1.0.0-rc.1'; "
                "assert 'site-packages' in str(mdrack_core.__file__); "
                "print(json.dumps({'version':d.version,'requires':d.requires or []},sort_keys=True))"
            ),
        ],
        cwd=workdir,
    )
    assert json.loads(probe.stdout) == {"requires": [], "version": EXPECTED_DISTRIBUTION_VERSION}
    tree = _run(["uv", "pip", "tree", "--python", str(python)], cwd=workdir).stdout.strip()
    assert tree == f"mdrack-core v{EXPECTED_DISTRIBUTION_VERSION}"
    return tree


def _verify_co_install(python: Path, workdir: Path) -> None:
    _run(
        [
            str(python),
            "-c",
            (
                "import importlib.metadata as m, mdrack, mdrack_core; "
                "assert m.version('mdrack')=='1.1.0'; "
                "assert m.version('mdrack-core')=='1.0.0rc1'; "
                "owners=m.packages_distributions(); "
                "assert owners['mdrack']==['mdrack']; "
                "assert owners['mdrack_core']==['mdrack-core']; "
                "assert mdrack.__version__=='1.1.0'; "
                "assert mdrack_core.CORE_CONTRACT_VERSION=='1.0.0-rc.1'"
            ),
        ],
        cwd=workdir,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "dist" / "core",
        help="directory for preserved core wheel and sdist",
    )
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    if output_dir == REPO_ROOT or REPO_ROOT not in output_dir.parents:
        raise SystemExit("output directory must be inside the repository")
    shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True)

    _run(
        [
            "uv",
            "build",
            "--offline",
            "--package",
            "mdrack-core",
            "--out-dir",
            str(output_dir),
        ]
    )
    core_wheel = _single(output_dir, "mdrack_core-*.whl")
    core_sdist = _single(output_dir, "mdrack_core-*.tar.gz")
    archive_counts = _verify_core_archives(core_wheel, core_sdist)

    with tempfile.TemporaryDirectory(prefix="mdrack-core-check-") as temporary:
        temporary_root = Path(temporary)
        app_dist = temporary_root / "app-dist"
        app_dist.mkdir()
        _run(
            [
                "uv",
                "build",
                "--offline",
                "--package",
                "mdrack",
                "--out-dir",
                str(app_dist),
            ]
        )
        app_wheel = _single(app_dist, "mdrack-*.whl")
        app_sdist = _single(app_dist, "mdrack-*.tar.gz")
        _verify_app_archives(app_wheel, app_sdist)

        core_python = _new_venv(temporary_root / "core-venv")
        _install_no_deps(core_python, core_wheel)
        dependency_tree = _verify_core_install(core_python, temporary_root)

        co_python = _new_venv(temporary_root / "co-venv")
        _install_no_deps(co_python, core_wheel, app_wheel)
        _verify_co_install(co_python, temporary_root)

    result = {
        "ok": True,
        "contract_version": EXPECTED_CONTRACT_VERSION,
        "distribution_version": EXPECTED_DISTRIBUTION_VERSION,
        "artifacts": [
            {
                "file": core_wheel.relative_to(REPO_ROOT).as_posix(),
                "sha256": _sha256(core_wheel),
            },
            {
                "file": core_sdist.relative_to(REPO_ROOT).as_posix(),
                "sha256": _sha256(core_sdist),
            },
        ],
        "dependency_tree": dependency_tree,
        "external_memory_catalog": "passed",
        "app_core_co_install": "passed",
        **archive_counts,
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
