"""CLI contracts for transcript ingestion and timed search."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main
from mdrack_media import resource_id
from mdrack_sqlite import SQLiteCatalog


def _source(path: Path) -> bytes:
    payload = json.dumps(
        {
            "segments": [
                {"start": 0, "end": 1, "text": "opening words"},
                {"start": 1, "end": 2, "text": "transaction boundary"},
            ]
        },
        separators=(",", ":"),
    ).encode()
    path.write_bytes(payload)
    return payload


def _ingest_args(source: Path, catalog: Path, *extra: str) -> list[str]:
    return [
        "ingest",
        "transcript",
        str(source),
        "--resource-id",
        resource_id("fixture", "audio-cli"),
        "--kind",
        "audio",
        "--media-type",
        "audio/wav",
        "--namespace",
        "fixture",
        "--source-ref",
        "audio-cli",
        "--catalog",
        str(catalog),
        *extra,
    ]


def test_cli_ingest_transcript_lexical_search_and_source_immutability(tmp_path: Path) -> None:
    source = tmp_path / "PRIVATE_TRANSCRIPT_PATH.json"
    catalog = tmp_path / "catalog.sqlite3"
    before = _source(source)
    with SQLiteCatalog.create(catalog):
        pass
    runner = CliRunner()

    ingested = runner.invoke(main, _ingest_args(source, catalog, "--no-embeddings"))
    searched = runner.invoke(
        main,
        [
            "search",
            "transaction",
            "--mode",
            "text",
            "--kind",
            "audio",
            "--catalog",
            str(catalog),
        ],
    )

    assert ingested.exit_code == 0, ingested.output
    assert searched.exit_code == 0, searched.output
    ingest_payload = json.loads(ingested.stdout)
    search_payload = json.loads(searched.stdout)
    assert ingest_payload["data"]["persisted"] is True
    assert ingest_payload["data"]["vector_count"] == 0
    assert search_payload["data"]["results"][0]["evidence"][0] == {
        "unit_id": search_payload["data"]["results"][0]["evidence"][0]["unit_id"],
        "representation_id": search_payload["data"]["results"][0]["evidence"][0][
            "representation_id"
        ],
        "start_ms": 0,
        "end_ms": 2_000,
        "track": "audio",
        "timestamp_unit": "ms",
    }
    assert source.read_bytes() == before
    assert str(source) not in ingested.stdout + ingested.stderr
    assert "transaction boundary" not in ingested.stderr


def test_cli_ready_vectors_enable_semantic_and_hybrid_search(tmp_path: Path) -> None:
    source = tmp_path / "transcript.json"
    catalog = tmp_path / "catalog.sqlite3"
    _source(source)
    with SQLiteCatalog.create(catalog):
        pass
    runner = CliRunner()

    ingested = runner.invoke(
        main,
        _ingest_args(source, catalog, "--provider", "fake"),
    )
    semantic = runner.invoke(
        main,
        [
            "search",
            "transaction boundary",
            "--mode",
            "semantic",
            "--provider",
            "fake",
            "--catalog",
            str(catalog),
        ],
    )
    hybrid = runner.invoke(
        main,
        [
            "search",
            "transaction boundary",
            "--mode",
            "hybrid",
            "--provider",
            "fake",
            "--target",
            "resource",
            "--catalog",
            str(catalog),
        ],
    )

    assert ingested.exit_code == semantic.exit_code == hybrid.exit_code == 0
    assert json.loads(ingested.stdout)["data"]["vector_count"] > 0
    semantic_data = json.loads(semantic.stdout)["data"]
    hybrid_data = json.loads(hybrid.stdout)["data"]
    assert semantic_data["results"] and semantic_data["degraded"] is False
    assert hybrid_data["results"] and hybrid_data["target"] == "resource"
    assert hybrid_data["results"][0]["unit_id"] is None


def test_cli_transcript_failures_are_fixed_and_private(tmp_path: Path) -> None:
    source = tmp_path / "PRIVATE_BAD_TRANSCRIPT.json"
    source.write_text('{"segments":[{"text":"PRIVATE_CONTENT_SENTINEL"}]}')
    catalog = tmp_path / "PRIVATE_CATALOG.sqlite3"
    with SQLiteCatalog.create(catalog):
        pass

    result = CliRunner().invoke(
        main,
        _ingest_args(source, catalog, "--no-embeddings"),
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == {
        "message": "Transcript could not be read",
        "code": "TRANSCRIPT_INVALID",
    }
    captured = result.stdout + result.stderr
    assert "PRIVATE_CONTENT_SENTINEL" not in captured
    assert str(source) not in captured
    assert str(catalog) not in captured
