"""Offline verification and LIVE-entrypoint guards."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_verify_scripts_contain_required_offline_gates() -> None:
    shell = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")
    powershell = (ROOT / "scripts" / "verify.ps1").read_text(encoding="utf-8")
    required = ("pytest", "ruff check src/ tests/", "check_no_forbidden_deps.py")
    assert all(item in shell for item in required)
    assert all(item in powershell for item in required)
    assert "live_lmstudio_eval.py" not in shell
    assert "live_lmstudio_eval.py" not in powershell


def test_live_entrypoint_default_is_offline_and_does_not_import_httpx() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "live_lmstudio_eval.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert '"status": "live_confirmation_required"' in completed.stdout
    assert "httpx" not in completed.stdout


def test_windows_packaging_contract_includes_all_migrations() -> None:
    spec = (ROOT / "mdrack.spec").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts" / "build_windows_exe.ps1").read_text(encoding="utf-8")
    assert 'glob("*.sql")' in spec
    assert "pyinstaller>=6.16,<7" in build_script
    assert "dist\\mdrack\\mdrack.exe" in build_script
