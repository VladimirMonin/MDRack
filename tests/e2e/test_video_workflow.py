"""Offline CLI and embedded API parity for complete video manifests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.application.video_composition import VideoCompositionService
from mdrack.cli import main
from mdrack.config.models import MDRackConfig
from mdrack.ingestion.frame_captions import FrameCaptionManifestError, read_frame_captions
from mdrack.ingestion.media_manifests import MediaManifestError, read_video_resource_manifest
from mdrack.ingestion.transcripts import read_transcript
from mdrack.public_api import MDRackEngine
from mdrack_media import FrameCaptionArtifact, ProducerFingerprint, frame_id, resource_id
from mdrack_sqlite import SQLiteCatalog


class _EngineStorage:
    def __init__(self, catalog: SQLiteCatalog) -> None:
        self.resource_store = catalog

    def close(self) -> None:
        pass


def _manifest_bytes() -> tuple[bytes, str]:
    resource = resource_id("fixture", "video-e2e")
    transcript = read_transcript(
        json.dumps(
            {"segments": [{"start": 0, "end": 2, "text": "speech transaction boundary"}]}
        ).encode(),
        resource_id=resource,
        producer_fingerprint=ProducerFingerprint.from_payload({"producer": "e2e-transcript"}),
    ).artifact
    frame_producer = ProducerFingerprint.from_payload({"producer": "e2e-frame"})
    frames = read_frame_captions(
        json.dumps(
            {
                "schema": "mdrack.frame-captions.v1",
                "resource_id": resource,
                "producer_fingerprint": frame_producer.value,
                "normalization_fingerprint": None,
                "metadata": {},
                "frames": [
                    {
                        "frame_id": "diagram",
                        "timestamp_ms": 1_250,
                        "caption": "unique architecture diagram",
                        "metadata": {},
                    },
                    {
                        "frame_id": "closing",
                        "timestamp_ms": 1_750,
                        "caption": "closing title card",
                        "metadata": {},
                    }
                ],
            }
        ).encode()
    ).artifact
    payload = {
        "schema": "mdrack.video-resource.v1",
        "resource": {
            "resource_id": resource,
            "media_type": "video/mp4",
            "source_namespace": "fixture",
            "locator": {"kind": "external_record", "payload": {"source_ref": "video-e2e"}},
            "source_metadata": {"series": "synthetic"},
            "title": "Synthetic video",
        },
        "transcript": transcript.to_dict(),
        "frame_captions": frames.to_dict(),
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(), resource


def _invalid_manifest(source: bytes, *, defect: str) -> bytes:
    payload = json.loads(source)
    frames = payload["frame_captions"]
    observations = frames["observations"]
    if defect in {"duplicate_identity", "duplicate_pair"}:
        first, second = observations
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
    elif defect == "forbidden_metadata":
        frames["metadata"] = {"provider_payload": "PRIVATE_PROVIDER_BODY"}
        observations[0]["metadata"] = {"nested": {"frame_path": "/PRIVATE/frame.png"}}
    else:  # pragma: no cover - test helper guard
        raise AssertionError(defect)
    FrameCaptionArtifact.from_dict(frames)
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def test_cli_and_engine_compose_the_same_provider_free_video_graph(tmp_path: Path) -> None:
    source, resource = _manifest_bytes()
    source_path = tmp_path / "PRIVATE_VIDEO_MANIFEST.json"
    source_path.write_bytes(source)
    cli_database = tmp_path / "cli.sqlite3"
    engine_database = tmp_path / "engine.sqlite3"
    with SQLiteCatalog.create(cli_database):
        pass
    with SQLiteCatalog.create(engine_database):
        pass

    cli_result = CliRunner().invoke(
        main,
        [
            "ingest",
            "video",
            str(source_path),
            "--no-embeddings",
            "--catalog",
            str(cli_database),
        ],
    )
    assert cli_result.exit_code == 0, cli_result.output
    cli_data = json.loads(cli_result.stdout)["data"]

    manifest = read_video_resource_manifest(source)
    with SQLiteCatalog.open(engine_database) as catalog:
        engine = MDRackEngine(
            root=tmp_path,
            config=MDRackConfig(),
            storage=_EngineStorage(catalog),
        )
        engine_result = asyncio.run(
            engine.ingest_video(
                manifest.transcript,
                manifest.frame_captions,
                media_type=manifest.media_type,
                source_namespace=manifest.source_namespace,
                source_locator=manifest.source_locator,
                source_metadata=manifest.source_metadata,
                title=manifest.title,
                embeddings=False,
            )
        )
        direct = VideoCompositionService(catalog).prepare(
            manifest.transcript,
            manifest.frame_captions,
            media_type=manifest.media_type,
            source_namespace=manifest.source_namespace,
            source_locator=manifest.source_locator,
            source_metadata=manifest.source_metadata,
            title=manifest.title,
        )
        stored = catalog.read_resource(resource)

    assert cli_data == {**engine_result.to_dict(), "persisted": True}
    assert direct.resource.content_hash == stored.content_hash  # type: ignore[union-attr]
    assert cli_data["transcript_unit_count"] == 1
    assert cli_data["frame_unit_count"] == 2
    assert cli_data["vector_count"] == 0
    assert source_path.read_bytes() == source
    assert str(source_path) not in cli_result.stdout + cli_result.stderr
    assert "speech transaction boundary" not in cli_result.stderr
    assert "unique architecture diagram" not in cli_result.stderr


def test_complete_manifest_rejects_semantic_duplicates_and_forbidden_frame_metadata() -> None:
    source, _ = _manifest_bytes()
    for defect in ("duplicate_identity", "duplicate_pair", "forbidden_metadata"):
        private_source = _invalid_manifest(source, defect=defect)
        with pytest.raises(MediaManifestError, match="^media_manifest_invalid$"):
            read_video_resource_manifest(private_source)


def test_engine_rejects_invalid_frame_artifacts_before_persistence(tmp_path: Path) -> None:
    source, _ = _manifest_bytes()
    valid = read_video_resource_manifest(source)
    database = tmp_path / "engine-validation.sqlite3"
    with SQLiteCatalog.create(database) as catalog:
        engine = MDRackEngine(
            root=tmp_path,
            config=MDRackConfig(),
            storage=_EngineStorage(catalog),
        )
        for defect, error in (
            ("duplicate_identity", "frame_manifest_duplicate"),
            ("duplicate_pair", "frame_manifest_duplicate"),
            ("forbidden_metadata", "frame_manifest_forbidden_metadata"),
        ):
            payload = json.loads(_invalid_manifest(source, defect=defect))
            frames = FrameCaptionArtifact.from_dict(payload["frame_captions"])
            with pytest.raises(FrameCaptionManifestError, match=f"^{error}$"):
                asyncio.run(
                    engine.ingest_video(
                        valid.transcript,
                        frames,
                        media_type=valid.media_type,
                        source_namespace=valid.source_namespace,
                        source_locator=valid.source_locator,
                        source_metadata={"generic": {"frame_path_label": "preserved"}},
                        embeddings=False,
                    )
                )
        assert catalog.verify().resources == 0
