"""Public CLI and embedded API contracts for explicit image operations."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import mdrack.cli.commands.images as image_commands
from mdrack.application.store_generations import (
    ActiveGenerationPointer,
    GenerationContractKind,
    GenerationState,
    StoreGeneration,
)
from mdrack.cli import main
from mdrack.config.models import EmbeddingConfig, MDRackConfig, PathsConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.ingestion.images import ExtractedImageText, StaticImageExtractor
from mdrack.public_api import MDRackEngine
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import (
    EXPECTED_MIGRATION_MANIFEST_DIGEST,
    EXPECTED_MIGRATION_VERSION,
    apply_candidate_migrations,
    get_migrations_dir,
)


def _ready_generation(store_dir: Path) -> Path:
    generation_id = "g-s9-test"
    generations = store_dir / "generations"
    generations.mkdir(parents=True)
    database_path = generations / f"generation-{generation_id}.sqlite3"
    connection = get_connection(database_path)
    apply_candidate_migrations(connection, get_migrations_dir())
    connection.close()
    generation = StoreGeneration(
        generation_id=generation_id,
        contract_kind=GenerationContractKind.RESOURCE_CORE_V1,
        migration_manifest_digest=EXPECTED_MIGRATION_MANIFEST_DIGEST,
        schema_version=EXPECTED_MIGRATION_VERSION,
        state=GenerationState.READY,
        created_at="2026-07-18T00:00:00+00:00",
        verified_at="2026-07-18T00:00:01+00:00",
    )
    (generations / f"generation-{generation_id}.json").write_bytes(generation.to_bytes())
    (store_dir / "active-generation.json").write_bytes(
        ActiveGenerationPointer(generation_id, GenerationContractKind.RESOURCE_CORE_V1).to_bytes()
    )
    return database_path


def _assert_private_ingest_failure(
    *,
    stdout: str,
    stderr: str,
    logs: str,
    sentinels: tuple[str, ...],
    expected_stderr_lines: tuple[str, ...] = (),
) -> None:
    assert json.loads(stdout) == {
        "ok": False,
        "error": {
            "message": "Image ingestion failed",
            "code": "IMAGE_INGEST_ERROR",
        },
        "meta": {"command": "image ingest"},
    }
    assert tuple(stderr.splitlines()) == expected_stderr_lines
    captured = stdout + stderr + logs
    for sentinel in sentinels:
        assert sentinel not in captured


def test_image_cli_and_engine_share_stable_scoped_results_and_delete(tmp_path: Path) -> None:
    root = tmp_path / "PRIVATE_ROOT_SENTINEL"
    root.mkdir()
    image = root / "PRIVATE_PATH_SENTINEL.png"
    image.write_bytes(b"explicit local fixture")
    store_dir = tmp_path / "store"
    database_path = _ready_generation(store_dir)
    config_path = root / "mdrack.toml"
    config_path.write_text(
        f'[paths]\nstore = "{store_dir}"\n[embedding]\ndimensions = 8\n',
        encoding="utf-8",
    )
    runner = CliRunner()
    common = ["--root", str(root), "--config-file", str(config_path)]
    caption = "PRIVATE_CONTENT_SENTINEL searchable architecture diagram"

    ingested = runner.invoke(
        main,
        [
            *common,
            "image",
            "ingest",
            str(image),
            "--resource-id",
            "image-logical-1",
            "--source-namespace",
            "fixture",
            "--source-ref",
            "public-image-ref",
            "--caption",
            caption,
            "--provider",
            "fake",
        ],
    )
    assert ingested.exit_code == 0, ingested.output
    ingest_payload = json.loads(ingested.output)
    assert ingest_payload["ok"] is True
    assert ingest_payload["meta"]["command"] == "image ingest"
    assert ingest_payload["data"]["resource_id"] == "image-logical-1"
    assert ingest_payload["data"]["representation_ids"]
    assert ingest_payload["data"]["unit_ids"]
    for private in (str(image), "PRIVATE_PATH_SENTINEL", "PRIVATE_ROOT_SENTINEL", caption):
        assert private not in ingested.output

    config = MDRackConfig(
        paths=PathsConfig(root=".", store=str(store_dir)),
        embedding=EmbeddingConfig(dimensions=8),
    )
    engine = MDRackEngine(
        root=root,
        config=config,
        embedding_provider=FakeEmbeddingProvider(dimensions=8),
    )
    try:
        for mode in ("text", "semantic", "hybrid"):
            cli = runner.invoke(
                main,
                [
                    *common,
                    "image",
                    "search",
                    "architecture",
                    "--mode",
                    mode,
                    "--provider",
                    "fake",
                    "--limit",
                    "5",
                ],
            )
            assert cli.exit_code == 0, cli.output
            payload = json.loads(cli.output)
            if mode == "text":
                embedded = engine.search_images_text("architecture", limit=5).to_dict()
            elif mode == "semantic":
                embedded = asyncio.run(engine.search_images_semantic("architecture", limit=5)).to_dict()
            else:
                embedded = asyncio.run(engine.search_images_hybrid("architecture", limit=5)).to_dict()
            assert payload["meta"]["command"] == "image search"
            assert payload["data"] == embedded
            assert payload["data"]["results"][0]["resource_id"] == "image-logical-1"
            assert payload["data"]["results"][0]["source_ref"] == "public-image-ref"
            assert "sqlite" not in cli.output.lower()
            assert caption not in cli.output
    finally:
        engine.close()

    deleted = runner.invoke(main, [*common, "image", "delete", "image-logical-1"])
    assert deleted.exit_code == 0, deleted.output
    assert json.loads(deleted.output) == {
        "ok": True,
        "data": {"resource_id": "image-logical-1", "status": "deleted"},
        "meta": {"command": "image delete"},
    }

    api_engine = MDRackEngine(
        root=root,
        config=config,
        embedding_provider=FakeEmbeddingProvider(dimensions=8),
        image_extractor=StaticImageExtractor(
            (ExtractedImageText("caption_text", "embedded API image caption", "caption-fake-v1"),)
        ),
    )
    try:
        api_result = asyncio.run(
            api_engine.ingest_image(
                image,
                resource_id="api-image-logical-1",
                source_namespace="fixture",
                source_ref="api-public-image-ref",
            )
        )
        assert api_result.resource_id == "api-image-logical-1"
        assert [
            item.resource_id for item in api_engine.search_images_text("embedded", limit=5).results
        ] == ["api-image-logical-1"]
        api_engine.delete_image("api-image-logical-1")
    finally:
        api_engine.close()

    connection = get_connection(database_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM core_resources WHERE resource_id='image-logical-1'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_image_cli_fails_closed_without_ready_resource_generation(tmp_path: Path) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"fixture")
    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "image",
            "ingest",
            str(image),
            "--resource-id",
            "image-logical-1",
            "--source-namespace",
            "fixture",
            "--source-ref",
            "public-ref",
            "--caption",
            "caption",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["error"] == {
        "message": "Image ingestion failed",
        "code": "IMAGE_INGEST_ERROR",
    }
    assert payload["meta"]["command"] == "image ingest"


def test_image_cli_missing_and_unavailable_paths_are_privacy_safe(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "PRIVATE_ROOT_SENTINEL"
    root.mkdir()
    missing = root / "PRIVATE_MISSING_IMAGE_SENTINEL.png"
    unavailable = root / "PRIVATE_UNAVAILABLE_IMAGE_SENTINEL.png"
    unavailable.mkdir()
    store_dir = tmp_path / "store"
    _ready_generation(store_dir)
    config_path = root / "mdrack.toml"
    config_path.write_text(
        f'[paths]\nstore = "{store_dir}"\n[embedding]\ndimensions = 8\n',
        encoding="utf-8",
    )
    common = ["--root", str(root), "--config-file", str(config_path)]
    runner = CliRunner()
    caplog.set_level(logging.DEBUG)

    for path in (missing, unavailable):
        caplog.clear()
        result = runner.invoke(
            main,
            [
                *common,
                "image",
                "ingest",
                str(path),
                "--resource-id",
                "image-logical-private-failure",
                "--source-namespace",
                "fixture",
                "--source-ref",
                "public-image-ref",
                "--caption",
                "PRIVATE_CONTENT_SENTINEL",
                "--provider",
                "fake",
            ],
        )
        assert result.exit_code == 1
        _assert_private_ingest_failure(
            stdout=result.stdout,
            stderr=result.stderr,
            logs=caplog.text,
            sentinels=(
                str(path),
                "PRIVATE_ROOT_SENTINEL",
                "PRIVATE_MISSING_IMAGE_SENTINEL",
                "PRIVATE_UNAVAILABLE_IMAGE_SENTINEL",
                "PRIVATE_CONTENT_SENTINEL",
                "PRIVATE_EXCEPTION_SENTINEL",
            ),
        )


def test_offline_installed_wheel_missing_and_unavailable_paths_are_privacy_safe(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[2]
    uv = shutil.which("uv")
    assert uv is not None
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["UV_OFFLINE"] = "1"
    wheel_dir = tmp_path / "wheel"
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = tuple(wheel_dir.glob("mdrack-*.whl"))
    assert len(wheels) == 1

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
        [uv, "pip", "install", "--python", str(python), "--offline", str(wheels[0])],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    root = tmp_path / "PRIVATE_INSTALLED_ROOT_SENTINEL"
    root.mkdir()
    missing = root / "PRIVATE_INSTALLED_MISSING_IMAGE_SENTINEL.png"
    unavailable = root / "PRIVATE_INSTALLED_UNAVAILABLE_IMAGE_SENTINEL.png"
    unavailable.mkdir()
    store_dir = tmp_path / "store"
    _ready_generation(store_dir)
    config_path = root / "mdrack.toml"
    config_path.write_text(
        f'[paths]\nstore = "{store_dir}"\n[embedding]\ndimensions = 8\n',
        encoding="utf-8",
    )
    installed_origin = subprocess.run(
        [python, "-c", "import mdrack; print(mdrack.__file__)"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "site-packages" in installed_origin.stdout
    assert str(repository) not in installed_origin.stdout

    executable = virtualenv / "bin" / "mdrack"
    common = [str(executable), "--root", str(root), "--config-file", str(config_path)]
    for path in (missing, unavailable):
        completed = subprocess.run(
            [
                *common,
                "image",
                "ingest",
                str(path),
                "--resource-id",
                "image-logical-installed-private-failure",
                "--source-namespace",
                "fixture",
                "--source-ref",
                "public-image-ref",
                "--caption",
                "PRIVATE_INSTALLED_CONTENT_SENTINEL",
                "--provider",
                "fake",
            ],
            cwd=tmp_path,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 1
        _assert_private_ingest_failure(
            stdout=completed.stdout,
            stderr=completed.stderr,
            logs="",
            expected_stderr_lines=("cli.image.ingest.failed",),
            sentinels=(
                str(path),
                "PRIVATE_INSTALLED_ROOT_SENTINEL",
                "PRIVATE_INSTALLED_MISSING_IMAGE_SENTINEL",
                "PRIVATE_INSTALLED_UNAVAILABLE_IMAGE_SENTINEL",
                "PRIVATE_INSTALLED_CONTENT_SENTINEL",
                "PRIVATE_INSTALLED_EXCEPTION_SENTINEL",
            ),
        )

    image = root / "installed-score.png"
    image.write_bytes(b"installed image score fixture")
    ingested = subprocess.run(
        [
            *common,
            "image",
            "ingest",
            str(image),
            "--resource-id",
            "installed-image-score",
            "--source-namespace",
            "fixture",
            "--source-ref",
            "installed-public-ref",
            "--caption",
            "installed searchable caption",
            "--provider",
            "fake",
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert ingested.returncode == 0, ingested.stdout + ingested.stderr
    for mode in ("text", "semantic", "hybrid"):
        searched = subprocess.run(
            [
                *common,
                "image",
                "search",
                "searchable",
                "--mode",
                mode,
                "--provider",
                "fake",
                "--limit",
                "1",
            ],
            cwd=tmp_path,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert searched.returncode == 0, searched.stdout + searched.stderr
        item = json.loads(searched.stdout)["data"]["results"][0]
        if mode == "hybrid":
            assert item["score"] == pytest.approx(2.0 / 61)
        else:
            assert item["score"] == item["evidence"][0]["score"]


def test_image_cli_cleanup_failure_is_privacy_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "PRIVATE_ROOT_SENTINEL"
    root.mkdir()
    image = root / "PRIVATE_PATH_SENTINEL.png"
    image.write_bytes(b"explicit local fixture")
    store_dir = tmp_path / "store"
    _ready_generation(store_dir)
    config_path = root / "mdrack.toml"
    config_path.write_text(
        f'[paths]\nstore = "{store_dir}"\n[embedding]\ndimensions = 8\n',
        encoding="utf-8",
    )

    async def fail_close(resource: object) -> None:
        del resource
        raise RuntimeError("PRIVATE_CLEANUP_EXCEPTION_SENTINEL")

    monkeypatch.setattr(image_commands, "close_async_resource", fail_close)
    caplog.set_level(logging.DEBUG)
    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(root),
            "--config-file",
            str(config_path),
            "image",
            "ingest",
            str(image),
            "--resource-id",
            "image-logical-cleanup",
            "--source-namespace",
            "fixture",
            "--source-ref",
            "public-image-ref",
            "--caption",
            "PRIVATE_CONTENT_SENTINEL",
            "--provider",
            "fake",
        ],
    )
    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["ok"] is True
    for sentinel in (
        "PRIVATE_ROOT_SENTINEL",
        "PRIVATE_PATH_SENTINEL",
        "PRIVATE_CONTENT_SENTINEL",
        "PRIVATE_CLEANUP_EXCEPTION_SENTINEL",
        str(image),
    ):
        assert sentinel not in result.output
        assert sentinel not in caplog.text
