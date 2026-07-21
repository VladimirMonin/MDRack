"""CLI contracts for complete video manifest ingestion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.ingestion.frame_captions import read_frame_captions
from mdrack.ingestion.transcripts import read_transcript
from mdrack_media import ProducerFingerprint, frame_id, resource_id
from mdrack_sqlite import SQLiteCatalog


def _video_manifest(path: Path) -> bytes:
    resource = resource_id("fixture", "video-cli")
    transcript = read_transcript(
        json.dumps({"segments": [{"start": 0, "end": 1, "text": "private speech"}]}).encode(),
        resource_id=resource,
        producer_fingerprint=ProducerFingerprint.from_payload({"producer": "cli-transcript"}),
    ).artifact
    producer = ProducerFingerprint.from_payload({"producer": "cli-frame"})
    frames = read_frame_captions(
        json.dumps(
            {
                "schema": "mdrack.frame-captions.v1",
                "resource_id": resource,
                "producer_fingerprint": producer.value,
                "normalization_fingerprint": None,
                "metadata": {},
                "frames": [
                    {
                        "frame_id": "frame-1",
                        "timestamp_ms": 500,
                        "caption": "private frame caption",
                        "metadata": {},
                    },
                    {
                        "frame_id": "frame-2",
                        "timestamp_ms": 750,
                        "caption": "private closing caption",
                        "metadata": {},
                    }
                ],
            }
        ).encode()
    ).artifact
    payload = json.dumps(
        {
            "schema": "mdrack.video-resource.v1",
            "resource": {
                "resource_id": resource,
                "media_type": "video/mp4",
                "source_namespace": "fixture",
                "locator": {"kind": "external_record", "payload": {"source_ref": "video-cli"}},
                "source_metadata": {},
                "title": None,
            },
            "transcript": transcript.to_dict(),
            "frame_captions": frames.to_dict(),
        },
        separators=(",", ":"),
    ).encode()
    path.write_bytes(payload)
    return payload


def test_cli_video_dry_run_is_provider_free_and_does_not_mutate(tmp_path: Path) -> None:
    source = tmp_path / "PRIVATE_VIDEO.json"
    before = _video_manifest(source)
    database = tmp_path / "catalog.sqlite3"
    with SQLiteCatalog.create(database):
        pass

    result = CliRunner().invoke(
        main,
        ["ingest", "video", str(source), "--dry-run", "--catalog", str(database)],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["persisted"] is False
    assert data["transcript_unit_count"] == 1
    assert data["frame_unit_count"] == 2
    with SQLiteCatalog.open(database) as catalog:
        assert catalog.verify().resources == 0
    assert source.read_bytes() == before
    assert str(source) not in result.stdout + result.stderr


def test_cli_video_invalid_manifest_failure_is_fixed_and_private(tmp_path: Path) -> None:
    source = tmp_path / "PRIVATE_BAD_VIDEO.json"
    source.write_text('{"private":"PRIVATE_CAPTION_SENTINEL"}')
    database = tmp_path / "PRIVATE_CATALOG.sqlite3"
    with SQLiteCatalog.create(database):
        pass

    result = CliRunner().invoke(
        main,
        ["ingest", "video", str(source), "--no-embeddings", "--catalog", str(database)],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == {
        "message": "Video manifest could not be read",
        "code": "VIDEO_MANIFEST_INVALID",
    }
    captured = result.stdout + result.stderr
    assert "PRIVATE_CAPTION_SENTINEL" not in captured
    assert str(source) not in captured
    assert str(database) not in captured


@pytest.mark.parametrize("defect", ["duplicate_identity", "duplicate_pair"])
def test_cli_video_rejects_duplicate_frames_without_mutating_existing_graph(
    tmp_path: Path,
    defect: str,
) -> None:
    source = tmp_path / "PRIVATE_DUPLICATE_VIDEO.json"
    original = _video_manifest(source)
    payload = json.loads(original)
    frames = payload["frame_captions"]
    first, second = frames["observations"]
    if defect == "duplicate_identity":
        second["observation_identity"] = first["observation_identity"]
    else:
        second["timestamp_ms"] = first["timestamp_ms"]
        second["caption"] = first["caption"]
        second["content_fingerprint"] = first["content_fingerprint"]
        second["token_count"] = first["token_count"]
    second["frame_id"] = frame_id(
        frames["resource_id"],
        frames["producer_fingerprint"],
        1,
        second["timestamp_ms"],
        second["observation_identity"],
    )
    source.write_text(json.dumps(payload))
    database = tmp_path / "catalog.sqlite3"
    with SQLiteCatalog.create(database) as catalog:
        before = tuple(catalog.connection.iterdump())

    result = CliRunner().invoke(
        main,
        ["ingest", "video", str(source), "--provider", "fake", "--catalog", str(database)],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == {
        "message": "Video manifest could not be read",
        "code": "VIDEO_MANIFEST_INVALID",
    }
    with SQLiteCatalog.open(database) as catalog:
        after = tuple(catalog.connection.iterdump())
    assert after == before
    assert "private frame caption" not in result.stdout + result.stderr


def test_cli_video_rejects_forbidden_frame_metadata_with_private_fixed_error(
    tmp_path: Path,
) -> None:
    source = tmp_path / "PRIVATE_METADATA_VIDEO.json"
    payload = json.loads(_video_manifest(source))
    payload["frame_captions"]["metadata"] = {
        "provider_payload": "PRIVATE_PROVIDER_BODY"
    }
    payload["frame_captions"]["observations"][0]["metadata"] = {
        "nested": {"frame_path": "/PRIVATE/frame.png"}
    }
    source.write_text(json.dumps(payload))
    database = tmp_path / "catalog.sqlite3"
    with SQLiteCatalog.create(database):
        pass

    result = CliRunner().invoke(
        main,
        ["ingest", "video", str(source), "--provider", "fake", "--catalog", str(database)],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == {
        "message": "Video manifest could not be read",
        "code": "VIDEO_MANIFEST_INVALID",
    }
    captured = result.stdout + result.stderr
    assert "PRIVATE_PROVIDER_BODY" not in captured
    assert "/PRIVATE/frame.png" not in captured
    with SQLiteCatalog.open(database) as catalog:
        assert catalog.verify().resources == 0
