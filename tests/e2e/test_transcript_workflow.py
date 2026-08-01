"""Installed-package offline transcript workflow through one fixed catalog."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from mdrack_media import resource_id


def _run(command: list[str | Path], *, cwd: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_installed_cli_and_engine_timed_transcript_parity(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    uv = shutil.which("uv")
    assert uv is not None
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["UV_OFFLINE"] = "1"

    wheel_dir = tmp_path / "wheels"
    _run([uv, "build", "--wheel", "--all-packages", "--out-dir", wheel_dir], cwd=repository, environment=environment)
    wheels = tuple(sorted(wheel_dir.glob("*.whl")))
    assert len(wheels) == 4
    virtualenv = tmp_path / "venv"
    _run([uv, "venv", "--python", sys.executable, virtualenv], cwd=tmp_path, environment=environment)
    python = virtualenv / "bin" / "python"
    source_site_packages = (
        Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    )
    target_site_packages = (
        virtualenv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    )
    assert source_site_packages.is_dir()
    (target_site_packages / "mdrack-test-dependencies.pth").write_text(
        f"{source_site_packages}\n",
        encoding="utf-8",
    )
    _run(
        [uv, "pip", "install", "--python", python, "--no-deps", "--offline", *wheels],
        cwd=tmp_path,
        environment=environment,
    )

    root = tmp_path / "workspace"
    root.mkdir()
    source = root / "transcript.json"
    source.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 0, "end": 1, "text": "opening words"},
                    {"start": 1, "end": 2, "text": "transaction boundary"},
                ]
            },
            separators=(",", ":"),
        )
    )
    executable = virtualenv / "bin" / "mdrack"
    _run([executable, "--root", root, "init"], cwd=root, environment=environment)
    canonical_resource = resource_id("fixture", "installed-audio")
    ingested = _run(
        [
            executable,
            "--root",
            root,
            "ingest",
            "transcript",
            source,
            "--resource-id",
            canonical_resource,
            "--kind",
            "audio",
            "--media-type",
            "audio/wav",
            "--namespace",
            "fixture",
            "--source-ref",
            "installed-audio",
            "--provider",
            "fake",
        ],
        cwd=root,
        environment=environment,
    )
    cli_results: dict[str, Any] = {}
    for mode in ("text", "semantic", "hybrid"):
        command: list[str | Path] = [
            executable,
            "--root",
            root,
            "search",
            "transaction",
            "--mode",
            mode,
            "--scope",
            "audio",
        ]
        if mode != "text":
            command.extend(("--provider", "fake"))
        cli_results[mode] = json.loads(_run(command, cwd=root, environment=environment).stdout)["data"]
    probe = _run(
        [
            python,
            "-c",
            (
                "import asyncio,json\n"
                "from pathlib import Path\n"
                "from mdrack.config.models import MDRackConfig\n"
                "from mdrack.embeddings.fake import FakeEmbeddingProvider\n"
                "from mdrack.public_api.engine import MDRackEngine\n"
                "e=MDRackEngine(root=Path('.'),config=MDRackConfig(),"
                "embedding_provider=FakeEmbeddingProvider(dimensions=1024))\n"
                "results={}\n"
                "for m in ('text','semantic','hybrid'):\n"
                "  results[m]=asyncio.run(e.search_unified('transaction',scope='audio',mode=m)).to_dict()\n"
                "print(json.dumps(results))\n"
                "e.close()"
            ),
        ],
        cwd=root,
        environment=environment,
    )

    ingest_data = json.loads(ingested.stdout)["data"]
    engine_results = json.loads(probe.stdout)
    assert ingest_data["resource_id"] == canonical_resource
    assert ingest_data["vector_count"] > 0
    assert cli_results == engine_results
    for mode in ("text", "semantic", "hybrid"):
        assert cli_results[mode]["results"]
        assert cli_results[mode]["degraded"] is False
        evidence = cli_results[mode]["results"][0]["evidence"][0]
        assert evidence["locator"]["payload"]["start_ms"] == 0
        assert evidence["locator"]["payload"]["end_ms"] == 2_000
    assert cli_results["hybrid"]["target"] == "resource"
    assert (root / ".mdrack" / "catalog.sqlite3").is_file()
    assert not (root / ".mdrack" / "knowledge.db").exists()
    assert str(repository) not in probe.stdout
