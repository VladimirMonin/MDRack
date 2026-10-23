"""Subprocess dispatch contract; expensive tools are controlled external edges."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def wrapper_project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    for name in ("verify.sh", "verify.ps1", "check_documentation.py"):
        shutil.copyfile(ROOT / "scripts" / name, root / "scripts" / name)
    stub = tmp_path / "stub.py"
    stub.write_text('''import json, os, pathlib, subprocess, sys
args=sys.argv[1:]
with open(os.environ['CALLS'], 'a', encoding='utf-8') as f: f.write(json.dumps(args)+'\\n')
if any('check_documentation.py' in a for a in args):
    if os.environ.get('REAL_DOCS'):
        sys.exit(subprocess.run([sys.executable, '-I', '-B', 'scripts/check_documentation.py', '--check']).returncode)
    sys.exit(int(os.environ.get('DOC_EXIT', '0')))
if any('check_v13_release_packet.py' in a for a in args): sys.exit(23)
if 'offline_release_matrix.py' in ' '.join(args):
    p=pathlib.Path(args[args.index('--output-dir')+1]); p.mkdir(exist_ok=True)
sys.exit(0)
''', encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uv.cmd").write_text(f'@"{sys.executable}" "{stub}" %*\n@exit /b %errorlevel%\n', encoding="utf-8")
    (bin_dir / "uv").write_text(
        f'#!/usr/bin/env bash\nexec "{Path(sys.executable).as_posix()}" "{stub.as_posix()}" "$@"\n', encoding="utf-8")
    (bin_dir / "uv").chmod(0o755)
    # Only this disposable fixture is initialized; no commits/remotes or real index changes.
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    # Git Bash prepends its own /mingw64/bin during startup; restore controlled edges.
    bash_env = tmp_path / "bash-env"
    bash_env.write_text(f'export PATH="{bin_dir.as_posix()}:$PATH"\n', encoding="utf-8")
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
           "BASH_ENV": str(bash_env), "CALLS": str(tmp_path / "calls.jsonl")}
    # Only this disposable shell/native boundary needs ordinary MSYS path conversion.
    env.pop("MSYS2_ARG_CONV_EXCL", None)
    env.pop("MSYS_NO_PATHCONV", None)
    return root, env


def invoke(root: Path, env: dict[str, str], shell: str, *args: str) -> subprocess.CompletedProcess[str]:
    if shell == "powershell":
        executable = shutil.which("powershell") or shutil.which("pwsh")
        if not executable:
            pytest.skip("PowerShell unavailable on this host")
        command = [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "scripts/verify.ps1")]
    else:
        git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        executable = str(git_bash) if git_bash.is_file() else shutil.which("bash")
        if not executable:
            pytest.skip("Bash unavailable on this host")
        command = [executable, str(root / "scripts/verify.sh")]
    return subprocess.run([*command, *args], cwd=root.parent, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def calls(env: dict[str, str]) -> list[list[str]]:
    p = Path(env["CALLS"])
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_development_skips_only_packet_and_keeps_all_other_gates(wrapper_project, shell: str) -> None:
    root, env = wrapper_project
    result = invoke(root, env, shell, "--development")
    assert result.returncode == 0, result.stdout + result.stderr
    invoked = "\n".join(" ".join(a) for a in calls(env))
    for gate in ("check_documentation.py --check", "ruff", "mypy", "pytest", "-m privacy", "offline_release_matrix.py"):
        assert gate in invoked
    assert "check_v13_release_packet.py" not in invoked
    assert "SKIPPED" in result.stdout


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("mode", [(), ("--development",)])
def test_documentation_failure_is_raw_failfast(wrapper_project, shell: str, mode: tuple[str, ...]) -> None:
    root, env = wrapper_project
    env["DOC_EXIT"] = "37"
    result = invoke(root, env, shell, *mode)
    assert result.returncode == 37, result.stdout + result.stderr
    assert len(calls(env)) == 1  # Docs gate is first, no sync/build/test after it.


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_real_invalid_documentation_propagates_to_wrapper(wrapper_project, shell: str) -> None:
    root, env = wrapper_project
    env["REAL_DOCS"] = "1"  # Missing scope: real checker exit 2, no expensive commands.
    result = invoke(root, env, shell, "--development")
    assert result.returncode == 2, result.stdout + result.stderr
    assert len(calls(env)) == 1


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_default_strict_still_calls_packet_and_returns_failure(wrapper_project, shell: str) -> None:
    root, env = wrapper_project
    result = invoke(root, env, shell)
    assert result.returncode == 23, result.stdout + result.stderr
    commands = calls(env)
    matrix = next(a for a in commands if "scripts/offline_release_matrix.py" in a)
    packet = next(a for a in commands if "scripts/check_v13_release_packet.py" in a)
    assert matrix[matrix.index("--output-dir") + 1] == packet[packet.index("--artifacts-dir") + 1]


def test_ci_pr_and_strict_dispatch_keep_docs_gate_on_both_routes() -> None:
    source = (ROOT / ".github/workflows/offline-release-matrix.yml").read_text(encoding="utf-8")
    assert "check_documentation.py --check" in source
    assert "github.event_name == 'workflow_dispatch'" in source
    assert "SKIPPED" in source and "historical release identity" in source


def test_ci_documentation_command_rejects_real_bad_input_on_pr_and_dispatch(wrapper_project) -> None:
    root, env = wrapper_project
    workflow = yaml.safe_load((ROOT / ".github/workflows/offline-release-matrix.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["release"]["steps"]
    docs = next(step for step in steps if "check_documentation.py --check" in step.get("run", ""))
    assert "if" not in docs  # Same mandatory docs gate on PR-development and strict dispatch.
    assert docs["shell"] == "bash"
    packet_steps = [s for s in steps if "check_v13_release_packet.py" in s.get("run", "")]
    assert len(packet_steps) == 2
    assert all("github.event_name == 'workflow_dispatch'" in s["if"] for s in packet_steps)
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    if not bash:
        pytest.skip("CI bash route unavailable locally")
    env["REAL_DOCS"] = "1"
    result = subprocess.run([bash, "-c", docs["run"]], cwd=root, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "input.invalid" in result.stdout
