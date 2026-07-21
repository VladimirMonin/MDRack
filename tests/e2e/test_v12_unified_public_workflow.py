"""Stage B public API and CLI contracts for MDRack 1.2 unified text retrieval."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.application.compatibility import create_application_storage
from mdrack.application.store_generations import (
    ActiveGenerationPointer,
    GenerationContractKind,
    GenerationState,
    StoreGeneration,
)
from mdrack.cli import main
from mdrack.config.models import EmbeddingConfig, MDRackConfig, PathsConfig
from mdrack.ingestion.frame_captions import read_frame_captions
from mdrack.ingestion.transcripts import read_transcript
from mdrack.public_api import MDRackEngine
from mdrack.public_api.models import UnifiedTextSearchResult, UnifiedTextSimilarityResult
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import (
    EXPECTED_MIGRATION_MANIFEST_DIGEST,
    EXPECTED_MIGRATION_VERSION,
    apply_candidate_migrations,
    get_migrations_dir,
)
from mdrack_core import (
    EmbeddingSpaceRecord,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)
from mdrack_media import ProducerFingerprint, resource_id


def _ready_generation(store_dir: Path) -> None:
    generation_id = "g-v12-public"
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
        created_at="2026-07-22T00:00:00+00:00",
        verified_at="2026-07-22T00:00:01+00:00",
    )
    (generations / f"generation-{generation_id}.json").write_bytes(generation.to_bytes())
    (store_dir / "active-generation.json").write_bytes(
        ActiveGenerationPointer(generation_id, GenerationContractKind.RESOURCE_CORE_V1).to_bytes()
    )


def _config(root: Path, store_dir: Path) -> MDRackConfig:
    return MDRackConfig(
        paths=PathsConfig(root=".", store=str(store_dir)),
        embedding=EmbeddingConfig(dimensions=8),
    )


def _write_config(root: Path, store_dir: Path) -> Path:
    config_path = root / "mdrack.toml"
    config_path.write_text(
        f'[paths]\nstore = "{store_dir}"\n[embedding]\ndimensions = 8\n',
        encoding="utf-8",
    )
    return config_path


def _video_manifest(resource: str) -> bytes:
    transcript = read_transcript(
        json.dumps(
            {"segments": [{"start": 0, "end": 2, "text": "video needle transcript"}]}
        ).encode(),
        resource_id=resource,
        producer_fingerprint=ProducerFingerprint.from_payload({"producer": "v12-transcript"}),
    ).artifact
    frames = read_frame_captions(
        json.dumps(
            {
                "schema": "mdrack.frame-captions.v1",
                "resource_id": resource,
                "producer_fingerprint": ProducerFingerprint.from_payload(
                    {"producer": "v12-frames"}
                ).value,
                "normalization_fingerprint": None,
                "metadata": {},
                "frames": [
                    {
                        "frame_id": "v12-frame-1",
                        "timestamp_ms": 1_000,
                        "caption": "video needle frame",
                        "metadata": {},
                    }
                ],
            }
        ).encode()
    ).artifact
    return json.dumps(
        {
            "schema": "mdrack.video-resource.v1",
            "resource": {
                "resource_id": resource,
                "media_type": "video/mp4",
                "source_namespace": "fixture",
                "locator": {"kind": "external_record", "payload": {"source_ref": "video-v12"}},
                "source_metadata": {},
                "title": None,
            },
            "transcript": transcript.to_dict(),
            "frame_captions": frames.to_dict(),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _whole_resource_batch(
    resource_id_value: str,
    *,
    vector: tuple[float, float],
    text: str,
) -> PreparedResourceBatch:
    representation_id = f"representation-{resource_id_value}"
    unit_id = f"unit-{resource_id_value}"
    relative_path = f"public-{resource_id_value}.md"
    metadata = {
        "aggregation": "direct_text_v1",
        "similarity_basis": "markdown_retrieval_text",
    }
    return PreparedResourceBatch(
        ResourceRecord(
            resource_id_value,
            "document",
            "text/markdown",
            "fixture",
            Locator("whole_resource", {"relative_path": relative_path}),
        ),
        (
            RepresentationRecord(
                representation_id,
                resource_id_value,
                "whole_resource_text",
                "text",
                text,
                metadata=metadata,
            ),
        ),
        (
            SearchUnitRecord(
                unit_id,
                resource_id_value,
                representation_id,
                "whole_resource",
                "text",
                text,
                Locator("whole_resource", {"relative_path": relative_path}),
                0,
                metadata=metadata,
            ),
        ),
        (EmbeddingSpaceRecord("v12-space", 2, "cosine", "v12-fingerprint", {}),),
        (VectorRecord(unit_id, "v12-space", vector),),
    )


def test_active_store_unified_search_finds_all_five_text_material_classes_with_cli_api_parity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "PRIVATE_ROOT_SENTINEL"
    root.mkdir()
    store_dir = tmp_path / "store"
    _ready_generation(store_dir)
    config_path = _write_config(root, store_dir)
    config = _config(root, store_dir)
    note = root / "note.md"
    note_before = b"# Note\n\nPRIVATE_NOTE_CONTENT needle from Markdown.\n"
    note.write_bytes(note_before)

    with MDRackEngine(root=root, config=config) as engine:
        indexed = engine.scan(force_reindex=True)
    assert indexed.status == "success"

    audio_source = root / "audio.json"
    audio_before = json.dumps(
        {"segments": [{"start": 0, "end": 2, "text": "audio needle transcript"}]},
        separators=(",", ":"),
    ).encode()
    audio_source.write_bytes(audio_before)
    audio_resource = resource_id("fixture", "v12-audio")
    video_resource = resource_id("fixture", "v12-video")
    video_source = root / "video.json"
    video_before = _video_manifest(video_resource)
    video_source.write_bytes(video_before)
    image_path = root / "image.png"
    image_path.write_bytes(b"direct image fixture")

    runner = CliRunner()
    common = ["--root", str(root), "--config-file", str(config_path)]
    transcript = runner.invoke(
        main,
        [
            *common,
            "ingest",
            "transcript",
            str(audio_source),
            "--resource-id",
            audio_resource,
            "--kind",
            "audio",
            "--media-type",
            "audio/wav",
            "--namespace",
            "fixture",
            "--source-ref",
            "audio-v12",
            "--no-embeddings",
        ],
    )
    video = runner.invoke(
        main,
        [*common, "ingest", "video", str(video_source), "--no-embeddings"],
    )
    image = runner.invoke(
        main,
        [
            *common,
            "image",
            "ingest",
            str(image_path),
            "--resource-id",
            "image-v12",
            "--source-namespace",
            "fixture",
            "--source-ref",
            "image-v12",
            "--caption",
            "image needle caption",
            "--provider",
            "fake",
        ],
    )
    assert transcript.exit_code == video.exit_code == image.exit_code == 0, (
        transcript.output + video.output + image.output
    )
    assert audio_source.read_bytes() == audio_before
    assert video_source.read_bytes() == video_before
    assert note.read_bytes() == note_before

    expected = {
        "notes": ("document", "text_chunk"),
        "audio": ("audio", "time_segment"),
        "video": ("video", "time_segment"),
        "frames": ("video", "frame"),
        "images": ("image", "whole_resource"),
    }
    with MDRackEngine(root=root, config=config) as engine:
        all_result = asyncio.run(engine.search_unified("needle", scope="all", mode="text", limit=10))
        assert isinstance(all_result, UnifiedTextSearchResult)
        all_data = all_result.to_dict()
        scope_data = {
            scope: asyncio.run(engine.search_unified("needle", scope=scope, mode="text", limit=10)).to_dict()
            for scope in expected
        }

    assert {item["resource_kind"] for item in all_data["results"]} == {
        "document",
        "audio",
        "video",
        "image",
    }
    video_item = next(item for item in all_data["results"] if item["resource_kind"] == "video")
    assert {evidence["unit_kind"] for evidence in video_item["evidence"]} >= {"time_segment", "frame"}
    for scope, (resource_kind, unit_kind) in expected.items():
        cli = runner.invoke(
            main,
            [*common, "search", "needle", "--mode", "text", "--scope", scope, "--limit", "10"],
        )
        assert cli.exit_code == 0, cli.output
        payload = json.loads(cli.output)
        assert payload["data"] == scope_data[scope]
        assert payload["data"]["degraded"] is False
        assert {item["resource_kind"] for item in payload["data"]["results"]} == {resource_kind}
        assert unit_kind in {
            evidence["unit_kind"]
            for item in payload["data"]["results"]
            for evidence in item["evidence"]
        }
        assert "PRIVATE_ROOT_SENTINEL" not in cli.output
        assert "PRIVATE_NOTE_CONTENT" not in cli.output


def test_active_store_find_similar_matches_engine_and_keeps_evidence_portable(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    store_dir = tmp_path / "store"
    _ready_generation(store_dir)
    config_path = _write_config(root, store_dir)
    config = _config(root, store_dir)
    storage = create_application_storage(root, config)
    try:
        catalog = storage.resource_store  # type: ignore[attr-defined]
        catalog.replace_resource(_whole_resource_batch("query-v12", vector=(1.0, 0.0), text="query"))
        catalog.replace_resource(_whole_resource_batch("near-v12", vector=(0.9, 0.1), text="near"))
        catalog.replace_resource(_whole_resource_batch("far-v12", vector=(0.1, 0.9), text="far"))
    finally:
        storage.close()

    with MDRackEngine(root=root, config=config) as engine:
        api_result = engine.find_similar_resource("query-v12", scope="notes", limit=5)
        assert isinstance(api_result, UnifiedTextSimilarityResult)
        api = api_result.to_dict()

    cli = CliRunner().invoke(
        main,
        [
            "--root",
            str(root),
            "--config-file",
            str(config_path),
            "find-similar",
            "query-v12",
            "--scope",
            "notes",
            "--limit",
            "5",
        ],
    )
    assert cli.exit_code == 0, cli.output
    assert json.loads(cli.output) == {
        "ok": True,
        "data": api,
        "meta": {"command": "find-similar"},
    }
    assert [item["resource_id"] for item in api["results"]] == ["near-v12", "far-v12"]
    assert all(item["resource_kind"] == "document" for item in api["results"])
    assert {
        evidence["unit_kind"]
        for item in api["results"]
        for evidence in item["evidence"]
    } == {"whole_resource"}
    assert str(root) not in cli.output


def test_unified_cli_rejects_incompatible_scope_combinations_with_fixed_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    search = runner.invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "search",
            "needle",
            "--scope",
            "notes",
            "--preset",
            "balanced",
        ],
    )
    similarity = runner.invoke(
        main,
        ["--root", str(tmp_path), "find-similar", "resource-v12", "--scope", "frames"],
    )

    assert search.exit_code == similarity.exit_code == 1
    assert json.loads(search.output) == {
        "ok": False,
        "error": {
            "message": "Unified search options are invalid",
            "code": "VALIDATION_ERROR",
        },
        "meta": {"command": "search"},
    }
    assert json.loads(similarity.output) == {
        "ok": False,
        "error": {
            "message": "Unified similarity options are invalid",
            "code": "VALIDATION_ERROR",
        },
        "meta": {"command": "find-similar"},
    }
