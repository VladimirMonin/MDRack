"""M3 exact metadata CLI and embedded API contracts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from mdrack.application.compatibility import create_application_storage, prepared_file_to_resource_batch
from mdrack.application.metadata_filters import MetadataFilter, MetadataFilters
from mdrack.application.metadata_projection import MetadataProjection, MetadataProjectionPolicy
from mdrack.application.store_generations import (
    ActiveGenerationPointer,
    GenerationContractKind,
    GenerationState,
    StoreGeneration,
)
from mdrack.cli import main
from mdrack.config.models import MDRackConfig, PathsConfig
from mdrack.domain.indexing import PreparedFile, StoredChunk, StoredSection
from mdrack.public_api import MDRackEngine
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import (
    EXPECTED_MIGRATION_MANIFEST_DIGEST,
    EXPECTED_MIGRATION_VERSION,
    apply_candidate_migrations,
    get_migrations_dir,
)


def _ready_generation(store_dir: Path) -> None:
    generation_id = "g-m3-test"
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
        created_at="2026-07-20T00:00:00+00:00",
        verified_at="2026-07-20T00:00:01+00:00",
    )
    (generations / f"generation-{generation_id}.json").write_bytes(generation.to_bytes())
    (store_dir / "active-generation.json").write_bytes(
        ActiveGenerationPointer(generation_id, GenerationContractKind.RESOURCE_CORE_V1).to_bytes()
    )


def _prepared() -> PreparedFile:
    return PreparedFile(
        record_id="row",
        logical_id="resource-metadata",
        root_id="vault",
        relative_path="metadata.md",
        title="Fallback",
        source_hash="abc123",
        indexed_at="2026-07-20T00:00:00+00:00",
        parser_name="markdown_it",
        parser_version="1",
        chunk_strategy_name="structural",
        chunk_strategy_version="1",
        index_run_id="run",
        sections=(
            StoredSection("section", "section-logical", "Body", ("Body",), 1, 1, 2, None),
        ),
        chunks=(
            StoredChunk(
                "chunk",
                "body-unit",
                "section",
                "ordinary body",
                "text",
                0,
                ("Body",),
                None,
                None,
                "ordinary body",
                "hash",
                2,
                2,
                "block",
            ),
        ),
        source_metadata={
            "aliases": ["rare-alias"],
            "status": "ready",
            "tags": ["python"],
            "secret": "PRIVATE_METADATA_SENTINEL",
        },
    )


def _setup(root: Path) -> tuple[Path, MDRackConfig]:
    store_dir = root / "store"
    _ready_generation(store_dir)
    config_path = root / "mdrack.toml"
    config_path.write_text(
        f"""[paths]
store = "{store_dir}"

[[metadata.projections]]
path = "/aliases"
mode = "lexical_text"

[[metadata.projections]]
path = "/status"
mode = "facet"
namespace = "status"

[[metadata.projections]]
path = "/tags"
mode = "facet_many"
namespace = "tag"

[[metadata.projections]]
path = "/secret"
mode = "store_only"
""",
        encoding="utf-8",
    )
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir)))
    policy = MetadataProjectionPolicy(
        (
            MetadataProjection("/aliases", "lexical_text"),
            MetadataProjection("/status", "facet", "status"),
            MetadataProjection("/tags", "facet_many", "tag"),
            MetadataProjection("/secret", "store_only"),
        )
    )
    storage = create_application_storage(root, config)
    try:
        storage.resource_store.replace_resource(  # type: ignore[attr-defined]
            prepared_file_to_resource_batch(_prepared(), metadata_policy=policy)
        )
    finally:
        storage.close()
    return config_path, config


def test_metadata_show_facets_and_resource_search_have_cli_engine_parity(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config_path, config = _setup(root)
    engine = MDRackEngine(root=root, config=config)
    try:
        inspection = engine.get_resource_metadata("resource-metadata").to_dict()
        facets = [item.to_dict() for item in engine.list_metadata_facets()]
        search = engine.search_resources_text(
            "rare-alias",
            metadata_filters=MetadataFilters(
                all=(MetadataFilter("status", "ready"), MetadataFilter("tag", "python")),
            ),
        ).to_dict()
    finally:
        engine.close()

    common = ["--root", str(root), "--config-file", str(config_path)]
    runner = CliRunner()
    shown = runner.invoke(main, [*common, "metadata", "show", "resource-metadata"])
    listed = runner.invoke(main, [*common, "metadata", "facets"])
    searched = runner.invoke(
        main,
        [
            *common,
            "search",
            "rare-alias",
            "--mode",
            "text",
            "--target",
            "resource",
            "--meta",
            '/status="ready"',
            "--tag",
            "python",
        ],
    )

    assert shown.exit_code == listed.exit_code == searched.exit_code == 0
    assert json.loads(shown.output) == {
        "ok": True,
        "data": inspection,
        "meta": {"command": "metadata show"},
    }
    assert json.loads(listed.output) == {
        "ok": True,
        "data": {"facets": facets},
        "meta": {"command": "metadata facets"},
    }
    assert json.loads(searched.output) == {
        "ok": True,
        "data": search,
        "meta": {"command": "search"},
    }
    assert inspection["source"]["secret"] == "PRIVATE_METADATA_SENTINEL"
    assert "PRIVATE_METADATA_SENTINEL" not in searched.output + searched.stderr
    assert search["results"][0]["resource_id"] == "resource-metadata"


def test_projection_check_is_read_only_and_reports_paths_without_store_only_values(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config_path, _ = _setup(root)
    note = root / "check.md"
    note.write_text(
        "---\naliases: [one, two]\nstatus: ready\nsecret: PRIVATE_METADATA_SENTINEL\n---\n# Body\n",
        encoding="utf-8",
    )
    before = note.read_bytes()

    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(root),
            "--config-file",
            str(config_path),
            "metadata",
            "projection-check",
            str(note),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["meta"] == {"command": "metadata projection-check"}
    assert payload["data"]["lexical_paths"] == ["/aliases"]
    assert payload["data"]["facet_paths"] == [
        {"namespace": "status", "path": "/status", "value_type": "string"}
    ]
    assert payload["data"]["store_only_paths"] == ["/secret"]
    assert "PRIVATE_METADATA_SENTINEL" not in result.output + result.stderr
    assert note.read_bytes() == before


def test_metadata_search_rejects_invalid_options_without_echoing_values(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config_path, _ = _setup(root)
    common = ["--root", str(root), "--config-file", str(config_path), "search", "query"]
    runner = CliRunner()

    invalid_filter = runner.invoke(
        main,
        [*common, "--mode", "text", "--meta", '/private=\"PRIVATE_FILTER_SENTINEL\"'],
    )
    invalid_target_mode = runner.invoke(
        main,
        [*common, "--mode", "semantic", "--target", "resource"],
    )

    for result in (invalid_filter, invalid_target_mode):
        assert result.exit_code == 1
        assert json.loads(result.output) == {
            "ok": False,
            "error": {
                "message": "Metadata search options are invalid",
                "code": "VALIDATION_ERROR",
            },
            "meta": {"command": "search"},
        }
        assert "PRIVATE_FILTER_SENTINEL" not in result.output + result.stderr


def test_installed_wheels_metadata_cli_and_engine_have_exact_parity(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    uv = shutil.which("uv")
    assert uv is not None
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["UV_OFFLINE"] = "1"

    root = tmp_path / "root"
    root.mkdir()
    config_path, _ = _setup(root)
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
        [uv, "pip", "install", "--python", str(python), "--offline", *(str(path) for path in wheels)],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    executable = virtualenv / "bin" / "mdrack"
    common = [str(executable), "--root", str(root), "--config-file", str(config_path)]
    shown = subprocess.run(
        [*common, "metadata", "show", "resource-metadata"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    facets = subprocess.run(
        [*common, "metadata", "facets"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    searched = subprocess.run(
        [
            *common,
            "search",
            "rare-alias",
            "--mode",
            "text",
            "--target",
            "resource",
            "--meta",
            '/status="ready"',
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    api = subprocess.run(
        [
            python,
            "-c",
            (
                "import json,mdrack; from pathlib import Path; "
                "from mdrack.config.loader import load_config; "
                "from mdrack.public_api import MDRackEngine; "
                f"root=Path({str(root)!r}); config=load_config("
                f"toml_path=Path({str(config_path)!r}),root=root); "
                "engine=MDRackEngine(root=root,config=config); "
                "print(json.dumps({'origin':mdrack.__file__,"
                "'show':engine.get_resource_metadata('resource-metadata').to_dict(),"
                "'facets':[item.to_dict() for item in engine.list_metadata_facets()],"
                "'search':engine.search_resources_text('rare-alias').to_dict()})); engine.close()"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    api_data = json.loads(api.stdout)
    assert json.loads(shown.stdout)["data"] == api_data["show"]
    assert json.loads(facets.stdout)["data"] == {"facets": api_data["facets"]}
    assert json.loads(searched.stdout)["data"] == api_data["search"]
    assert "site-packages" in api_data["origin"]
    assert str(repository) not in api_data["origin"]
    captured = "".join(result.stdout + result.stderr for result in (shown, facets, searched, api))
    assert "PRIVATE_METADATA_SENTINEL" not in searched.stdout + searched.stderr
    assert str(repository) not in captured
