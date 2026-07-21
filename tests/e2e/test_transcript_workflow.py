"""Installed-package offline transcript workflow parity."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from mdrack_media import resource_id


def test_installed_cli_and_engine_timed_transcript_parity(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    uv = shutil.which("uv")
    assert uv is not None
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["UV_OFFLINE"] = "1"

    wheel_dir = tmp_path / "wheels"
    subprocess.run(
        [uv, "build", "--wheel", "--all-packages", "--out-dir", str(wheel_dir)],
        cwd=repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = tuple(sorted(wheel_dir.glob("*.whl")))
    assert len(wheels) == 4
    virtualenv = tmp_path / "venv"
    subprocess.run(
        [uv, "venv", "--python", sys.executable, str(virtualenv)],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    python = virtualenv / "bin" / "python"
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--offline",
            *(str(path) for path in wheels),
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    source = tmp_path / "transcript.json"
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
    catalog = tmp_path / "catalog.sqlite3"
    subprocess.run(
        [
            python,
            "-c",
            (
                "from mdrack_sqlite import SQLiteCatalog; "
                f"catalog=SQLiteCatalog.create({str(catalog)!r}); catalog.close()"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    executable = virtualenv / "bin" / "mdrack"
    canonical_resource = resource_id("fixture", "installed-audio")
    ingested = subprocess.run(
        [
            executable,
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
            "--catalog",
            catalog,
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    cli_results: dict[str, Any] = {}
    for mode in ("text", "semantic", "hybrid"):
        command = [
            executable,
            "search",
            "transaction",
            "--mode",
            mode,
            "--catalog",
            str(catalog),
        ]
        if mode != "text":
            command.extend(("--provider", "fake"))
        if mode == "hybrid":
            command.extend(("--target", "resource"))
        searched = subprocess.run(
            command,
            cwd=tmp_path,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        cli_results[mode] = json.loads(searched.stdout)["data"]
    probe = subprocess.run(
        [
            python,
            "-c",
            (
                "import asyncio,json\n"
                "from pathlib import Path\n"
                "from mdrack.config.models import MDRackConfig\n"
                "from mdrack.embeddings.fake import FakeEmbeddingProvider\n"
                "from mdrack.public_api.engine import MDRackEngine\n"
                "from mdrack_sqlite import SQLiteCatalog\n"
                "class S:\n"
                "  def __init__(self,c): self.resource_store=c\n"
                "  def close(self): pass\n"
                f"c=SQLiteCatalog.open({str(catalog)!r})\n"
                "e=MDRackEngine(root=Path('.'),config=MDRackConfig(),embedding_provider=FakeEmbeddingProvider(dimensions=1024),storage=S(c))\n"
                "results={}\n"
                "for m in ('text','semantic','hybrid'):\n"
                "  target='resource' if m=='hybrid' else 'unit'\n"
                "  results[m]=asyncio.run(e.search_transcripts('transaction',mode=m,target=target)).to_dict()\n"
                "print(json.dumps(results))\n"
                "c.close()"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    ingest_data = json.loads(ingested.stdout)["data"]
    engine_results = json.loads(probe.stdout)
    assert ingest_data["resource_id"] == canonical_resource
    assert ingest_data["vector_count"] > 0
    assert cli_results == engine_results
    for mode in ("text", "semantic", "hybrid"):
        assert cli_results[mode]["results"]
        assert cli_results[mode]["degraded"] is False
        assert cli_results[mode]["results"][0]["evidence"][0]["start_ms"] == 0
        assert cli_results[mode]["results"][0]["evidence"][0]["end_ms"] == 2_000
    assert cli_results["hybrid"]["target"] == "resource"
    assert cli_results["hybrid"]["results"][0]["unit_id"] is None
    assert str(repository) not in probe.stdout

    lexical_replacement = subprocess.run(
        [
            executable,
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
            "--no-embeddings",
            "--catalog",
            catalog,
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(lexical_replacement.stdout)["data"]["vector_count"] == 0
    mismatch_config = tmp_path / "mismatch.toml"
    mismatch_config.write_text("[embedding]\ndimensions=16\n")

    degraded_cli: dict[str, Any] = {}
    for mode in ("semantic", "hybrid"):
        command = [
            executable,
            "--config-file",
            str(mismatch_config),
            "search",
            "transaction",
            "--mode",
            mode,
            "--provider",
            "fake",
            "--catalog",
            str(catalog),
        ]
        if mode == "hybrid":
            command.extend(("--target", "resource"))
        result = subprocess.run(
            command,
            cwd=tmp_path,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        degraded_cli[mode] = json.loads(result.stdout)["data"]

    degraded_probe = subprocess.run(
        [
            python,
            "-c",
            (
                "import asyncio,json\n"
                "from pathlib import Path\n"
                "from mdrack.config.models import MDRackConfig\n"
                "from mdrack.embeddings.fake import FakeEmbeddingProvider\n"
                "from mdrack.public_api.engine import MDRackEngine\n"
                "from mdrack_sqlite import SQLiteCatalog\n"
                "class S:\n"
                "  def __init__(self,c): self.resource_store=c\n"
                "  def close(self): pass\n"
                f"c=SQLiteCatalog.open({str(catalog)!r})\n"
                "config=MDRackConfig(embedding={'dimensions':16})\n"
                "e=MDRackEngine(root=Path('.'),config=config,embedding_provider=FakeEmbeddingProvider(dimensions=16),storage=S(c))\n"
                "results={}\n"
                "for m in ('semantic','hybrid'):\n"
                "  target='resource' if m=='hybrid' else 'unit'\n"
                "  results[m]=asyncio.run(e.search_transcripts('transaction',mode=m,target=target)).to_dict()\n"
                "print(json.dumps(results))\n"
                "c.close()"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    degraded_engine = json.loads(degraded_probe.stdout)
    assert degraded_cli == degraded_engine
    assert degraded_cli["semantic"]["results"] == []
    assert degraded_cli["semantic"]["degraded"] is True
    assert degraded_cli["hybrid"]["results"]
    assert degraded_cli["hybrid"]["degraded"] is True
    assert degraded_cli["hybrid"]["results"][0]["unit_id"] is None
