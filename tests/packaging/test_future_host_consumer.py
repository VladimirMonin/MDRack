from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "future_host"
PACKAGES = (
    REPO_ROOT / "packages" / "mdrack-core",
    REPO_ROOT / "packages" / "mdrack-media",
    REPO_ROOT / "packages" / "mdrack-sqlite",
)


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=True)


def test_future_host_fixture_has_exact_pins_and_no_app_import() -> None:
    pyproject = (FIXTURE / "pyproject.toml").read_text(encoding="utf-8")
    assert '"mdrack-core==1.0.0rc1"' in pyproject
    assert '"mdrack-media==1.0.0rc1"' in pyproject
    assert '"mdrack-sqlite==1.0.0rc1"' in pyproject
    source = (FIXTURE / "host_consumer.py").read_text(encoding="utf-8")
    assert "import mdrack" not in source
    assert "from mdrack import" not in source
    assert "sqlite3" not in source
    assert "httpx" not in source


@pytest.mark.e2e
def test_future_host_fixture_runs_from_installed_wheels(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    for package in PACKAGES:
        _run(["uv", "build", "--wheel", "--out-dir", str(wheel_dir), str(package)], cwd=REPO_ROOT)

    venv_dir = tmp_path / "venv"
    _run(["uv", "venv", "--python", "python3.11", str(venv_dir)], cwd=REPO_ROOT)
    wheels = sorted(str(path) for path in wheel_dir.glob("*.whl"))
    _run(
        ["uv", "pip", "install", "--python", str(venv_dir / "bin" / "python"), "--no-index", *wheels],
        cwd=REPO_ROOT,
    )

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [str(venv_dir / "bin" / "python"), "host_consumer.py", str(tmp_path / "catalog.db")],
        cwd=FIXTURE,
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    evidence: dict[str, Any] = json.loads(result.stdout)
    assert evidence["contract"] == "future-host-consumer-v1"
    assert evidence["before_delete"] == 2
    assert evidence["after_rebuild"] == 2
    assert evidence["resolved"] == {"source_id": "frame-b", "timestamp_ms": 900}
    assert evidence["schema_id"] == "mdrack_sqlite_catalog_v1"
    assert all("/packages/" not in path for path in evidence["installed_modules"].values())
    assert all("site-packages" in path for path in evidence["installed_modules"].values())
