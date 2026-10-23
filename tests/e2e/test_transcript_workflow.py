"""Installed-package offline transcript workflow through one fixed catalog."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
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
    python = virtualenv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    source_site_packages = Path(sysconfig.get_path("purelib"))
    layout = json.loads(_run(
        [python, "-I", "-B", "-S", "-c",
         "import json,sysconfig;print(json.dumps({k:sysconfig.get_path(k) for k in ('purelib','scripts')}))"],
        cwd=tmp_path, environment=environment,
    ).stdout)
    target_site_packages = Path(layout["purelib"])
    assert source_site_packages.is_dir()
    # Explicit isolated dependency overlay; never .pth/source fallback for project imports.
    entry = tmp_path / "installed_entry.py"
    entry.write_text(
        "import importlib.metadata,json,sys\nfrom pathlib import Path\n"
        f"sys.path[:0]=[{str(target_site_packages)!r},{str(source_site_packages)!r}]\n"
        "import mdrack,mdrack_core,mdrack_media,mdrack_sqlite\n"
        "origins={m.__name__:m.__file__ for m in (mdrack,mdrack_core,mdrack_media,mdrack_sqlite)}\n"
        f"assert all(Path(p).is_relative_to(Path({str(target_site_packages)!r})) for p in origins.values())\n"
        f"Path({str(tmp_path / 'installed-origins.json')!r}).write_text(json.dumps(origins))\n"
        "next(e for e in importlib.metadata.distribution('mdrack').entry_points "
        "if e.group=='console_scripts' and e.name=='mdrack').load()()\n",
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
    executable = Path(layout["scripts"]) / ("mdrack.exe" if os.name == "nt" else "mdrack")
    assert executable.is_file()
    cli = [python, "-I", "-B", "-S", entry]
    _run([*cli, "--root", root, "init"], cwd=root, environment=environment)
    canonical_resource = resource_id("fixture", "installed-audio")
    ingested = _run(
        [
            *cli,
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
            *cli,
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
            "-I", "-B", "-S", "-c",
            (
                "import asyncio,json,sys\n"
                "from pathlib import Path\n"
                f"sys.path[:0]=[{str(target_site_packages)!r},{str(source_site_packages)!r}]\n"
                "import mdrack,mdrack_core,mdrack_media,mdrack_sqlite\n"
                f"assert all(Path(m.__file__).is_relative_to(Path({str(target_site_packages)!r})) "
                "for m in (mdrack,mdrack_core,mdrack_media,mdrack_sqlite))\n"
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

    origins = json.loads((tmp_path / "installed-origins.json").read_text())
    assert set(origins) == {"mdrack", "mdrack_core", "mdrack_media", "mdrack_sqlite"}
    assert all(Path(p).is_relative_to(target_site_packages) for p in origins.values())
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
