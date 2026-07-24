"""V1 complete video composition against real local SQLite."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from mdrack.application.transcript_ingestion import TimedRetrievalService
from mdrack.application.video_composition import VideoCompositionService
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.ingestion.frame_captions import (
    FrameCaptionManifestError,
    read_frame_captions,
)
from mdrack.ingestion.transcripts import read_transcript
from mdrack_core import (
    BranchScopeOverride,
    LexicalBranch,
    Locator,
    SearchRequest,
    SearchScope,
)
from mdrack_core.application.retrieval import RetrievalService
from mdrack_media import (
    EmbeddingFingerprint,
    FrameCaptionArtifact,
    ProducerFingerprint,
    TimedChunkingPolicy,
    frame_id,
    resource_id,
)
from mdrack_sqlite import SQLiteCatalog


class _CountingCatalog:
    def __init__(self, catalog: SQLiteCatalog, *, fail: bool = False) -> None:
        self.catalog = catalog
        self.fail = fail
        self.replace_calls = 0

    def replace_resource(self, batch) -> None:  # type: ignore[no-untyped-def]
        self.replace_calls += 1
        if self.fail:
            raise RuntimeError("private storage failure")
        self.catalog.replace_resource(batch)


class _CountingProvider(FakeEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__(dimensions=8)
        self.calls = 0

    async def embed(self, texts, profile="default"):  # type: ignore[no-untyped-def]
        self.calls += 1
        return await super().embed(texts, profile)


def _invalid_frames(frames: object, *, defect: str) -> FrameCaptionArtifact:
    payload = frames.to_dict()  # type: ignore[attr-defined]
    observations = payload["observations"]
    if defect in {"duplicate_identity", "duplicate_pair"}:
        first, second = observations  # type: ignore[misc]
        if defect == "duplicate_identity":
            second["observation_identity"] = first["observation_identity"]
        else:
            second["timestamp_ms"] = first["timestamp_ms"]
            second["caption"] = first["caption"]
            second["content_fingerprint"] = first["content_fingerprint"]
            second["token_count"] = first["token_count"]
        second["frame_id"] = frame_id(
            payload["resource_id"],
            payload["producer_fingerprint"],
            second["ordinal"],
            second["timestamp_ms"],
            second["observation_identity"],
        )
    elif defect == "forbidden_metadata":
        payload["metadata"] = {"provider_payload": "PRIVATE_PROVIDER_BODY"}
        observations[0]["metadata"] = {  # type: ignore[index]
            "nested": {"frame_path": "/PRIVATE/frame.png"}
        }
    else:  # pragma: no cover - test helper guard
        raise AssertionError(defect)
    return FrameCaptionArtifact.from_dict(payload)


@pytest.fixture
def video_inputs() -> tuple[object, object, bytes, str]:
    resource = resource_id("fixture", "video-complete")
    transcript_source = json.dumps(
        {
            "segments": [
                {"start": 0, "end": 1, "text": "speech transaction boundary"},
                {"start": 1, "end": 2, "text": "speech remains searchable"},
            ]
        },
        separators=(",", ":"),
    ).encode()
    transcript = read_transcript(
        transcript_source,
        resource_id=resource,
        producer_fingerprint=ProducerFingerprint.from_payload({"producer": "fixture-transcript", "version": 1}),
    ).artifact
    frame_source = json.dumps(
        {
            "schema": "mdrack.frame-captions.v1",
            "resource_id": resource,
            "producer_fingerprint": EmbeddingFingerprint.from_payload(
                {"producer": "fixture-caption", "version": 1}
            ).value,
            "normalization_fingerprint": None,
            "metadata": {"capture_policy": {"interval_ms": 1_000}},
            "frames": [
                {
                    "frame_id": "slide-a",
                    "timestamp_ms": 500,
                    "caption": "unique architecture diagram",
                    "metadata": {"confidence": 0.9},
                },
                {
                    "frame_id": "slide-b",
                    "timestamp_ms": 1_500,
                    "caption": "closing title card",
                    "metadata": {},
                },
            ],
        },
        separators=(",", ":"),
    ).encode()
    frames = read_frame_captions(frame_source).artifact
    return transcript, frames, transcript_source + frame_source, resource


def _policy() -> TimedChunkingPolicy:
    return TimedChunkingPolicy(
        soft_min_tokens=1,
        target_tokens=2,
        soft_max_tokens=4,
        hard_max_tokens=8,
        soft_min_duration_ms=1,
        target_duration_ms=1_000,
        soft_max_duration_ms=1_000,
        hard_max_duration_ms=2_000,
    )


@pytest.mark.asyncio
async def test_default_video_composition_flags_oversized_atom_without_losing_frames(
    tmp_path: Path,
) -> None:
    resource = resource_id("fixture", "oversized-video-atom")
    transcript = read_transcript(
        json.dumps(
            {
                "segments": [
                    {
                        "start": 0,
                        "end": 130,
                        "text": "длинный фрагмент с точной временной привязкой",
                    }
                ]
            }
        ).encode(),
        resource_id=resource,
        producer_fingerprint=ProducerFingerprint.from_payload({"producer": "fixture-transcript", "version": 1}),
    ).artifact
    frames = read_frame_captions(
        json.dumps(
            {
                "schema": "mdrack.frame-captions.v1",
                "resource_id": resource,
                "producer_fingerprint": ProducerFingerprint.from_payload(
                    {"producer": "fixture-frame", "version": 1}
                ).value,
                "normalization_fingerprint": None,
                "metadata": {},
                "frames": [
                    {
                        "frame_id": "frame-1",
                        "timestamp_ms": 65_000,
                        "caption": "кадр внутри длинного фрагмента",
                        "metadata": {},
                    }
                ],
            }
        ).encode()
    ).artifact
    fingerprint = EmbeddingFingerprint.from_payload({"provider": "fake", "dimensions": 8, "version": 1}).value

    with SQLiteCatalog.create(tmp_path / "oversized-video.sqlite3") as catalog:
        service = VideoCompositionService(
            catalog,
            embedding_provider=FakeEmbeddingProvider(dimensions=8),
            embedding_fingerprint=fingerprint,
        )
        result = await service.ingest(
            transcript,
            frames,
            media_type="video/mp4",
            source_namespace="fixture",
            source_locator=Locator("external_record", {"source_ref": "oversized-video-atom"}),
        )
        passage_id = catalog.connection.execute(
            "SELECT unit_id FROM core_search_units WHERE resource_id=? AND unit_kind='time_segment'",
            (resource,),
        ).fetchone()[0]
        passage = catalog.read_unit(passage_id)

    assert result.transcript_unit_count == 1
    assert result.frame_unit_count == 1
    assert passage is not None
    assert passage.evidence_locator.payload == {
        "start_ms": 0,
        "end_ms": 130_000,
        "track": "video",
    }
    assert passage.metadata["unsplittable"] is True
    assert passage.metadata["hard_limit_exceeded"] is True


@pytest.mark.asyncio
async def test_one_composer_persists_transcript_frames_metadata_and_text_vectors_once(
    tmp_path: Path,
    video_inputs: tuple[object, object, bytes, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    transcript, frames, private_source, resource = video_inputs
    database = tmp_path / "video.sqlite3"
    provider = FakeEmbeddingProvider(dimensions=8)
    fingerprint = EmbeddingFingerprint.from_payload({"provider": "fake", "dimensions": 8, "version": 1}).value
    caplog.set_level(logging.INFO)

    with SQLiteCatalog.create(database) as sqlite_catalog:
        catalog = _CountingCatalog(sqlite_catalog)
        service = VideoCompositionService(
            catalog,
            embedding_provider=provider,
            embedding_fingerprint=fingerprint,
        )
        prepared = service.prepare(
            transcript,  # type: ignore[arg-type]
            frames,  # type: ignore[arg-type]
            media_type="video/mp4",
            source_namespace="fixture",
            source_locator=Locator("external_record", {"source_ref": "video-complete"}),
            source_metadata={"project": "MDRack", "draft": False},
            title="Complete video",
            chunking_policy=_policy(),
        )
        result = await service.ingest(
            transcript,  # type: ignore[arg-type]
            frames,  # type: ignore[arg-type]
            media_type="video/mp4",
            source_namespace="fixture",
            source_locator=Locator("external_record", {"source_ref": "video-complete"}),
            source_metadata={"project": "MDRack", "draft": False},
            title="Complete video",
            chunking_policy=_policy(),
        )
        stored = sqlite_catalog.read_resource(resource)
        prepared_whole = next(unit for unit in prepared.units if unit.unit_kind == "whole_resource")
        stored_whole = sqlite_catalog.read_unit(prepared_whole.unit_id)
        rows = sqlite_catalog.connection.execute(
            "SELECT unit_kind,modality,evidence_locator_json FROM core_search_units "
            "WHERE resource_id=? ORDER BY unit_kind,ordinal",
            (resource,),
        ).fetchall()
        transcript_search = await TimedRetrievalService(sqlite_catalog).search("transaction", mode="text")
        frame_search = RetrievalService(sqlite_catalog).search(
            SearchRequest(
                lexical_branches=(
                    LexicalBranch(
                        "frames",
                        "architecture",
                        scope_override=BranchScopeOverride(
                            representation_kinds=("frame_caption",),
                            unit_kinds=("frame",),
                        ),
                    ),
                ),
                vector_branches=(),
                scope=SearchScope(resource_kinds=("video",)),
                target="resource",
                limit=10,
            )
        )

    assert catalog.replace_calls == 1
    assert result.transcript_unit_count == 2
    assert result.frame_unit_count == 2
    assert result.vector_count == 5
    assert stored is not None
    assert stored.metadata["source"] == {"project": "MDRack", "draft": False}
    frame_representation = next(
        item for item in prepared.representations if item.representation_kind == "frame_caption"
    )
    first_frame = next(item for item in prepared.units if item.unit_kind == "frame")
    assert frame_representation.metadata["capture_policy"] == {"interval_ms": 1_000}
    assert first_frame.metadata["confidence"] == 0.9
    assert {row["unit_kind"] for row in rows} == {
        "time_segment",
        "frame",
        "whole_resource",
    }
    assert {row["modality"] for row in rows} == {"text"}
    assert prepared_whole.metadata["aggregation"] == "direct_text_v1"
    assert stored_whole is not None
    assert stored_whole.metadata["aggregation"] == "direct_text_v1"
    frame_locators = [json.loads(row["evidence_locator_json"]) for row in rows if row["unit_kind"] == "frame"]
    assert [item["timestamp_ms"] for item in frame_locators] == [500, 1_500]
    assert transcript_search.results[0].resource_id == resource
    assert frame_search.items[0].resource_id == resource
    captured = caplog.text
    assert "speech transaction boundary" not in captured
    assert "unique architecture diagram" not in captured
    assert "video-complete" not in captured
    assert private_source


@pytest.mark.asyncio
async def test_long_video_builds_centroid_only_after_passage_vectors_exist(
    tmp_path: Path,
) -> None:
    resource = resource_id("fixture", "long-video-centroid")
    transcript = read_transcript(
        json.dumps(
            {
                "segments": [
                    {
                        "start": 0,
                        "end": 600,
                        "text": " ".join(["semantic"] * 9_000),
                    }
                ]
            }
        ).encode(),
        resource_id=resource,
        producer_fingerprint=ProducerFingerprint.from_payload({"producer": "long-transcript", "version": 1}),
    ).artifact
    frames = read_frame_captions(
        json.dumps(
            {
                "schema": "mdrack.frame-captions.v1",
                "resource_id": resource,
                "producer_fingerprint": ProducerFingerprint.from_payload(
                    {"producer": "long-frame", "version": 1}
                ).value,
                "normalization_fingerprint": None,
                "metadata": {},
                "frames": [
                    {
                        "frame_id": "frame-long",
                        "timestamp_ms": 300_000,
                        "caption": "semantic diagram",
                        "metadata": {},
                    }
                ],
            }
        ).encode()
    ).artifact
    fingerprint = EmbeddingFingerprint.from_payload({"provider": "fake", "dimensions": 8, "version": 1}).value

    with SQLiteCatalog.create(tmp_path / "long-video-centroid.sqlite3") as catalog:
        result = await VideoCompositionService(
            catalog,
            embedding_provider=FakeEmbeddingProvider(dimensions=8),
            embedding_fingerprint=fingerprint,
        ).ingest(
            transcript,
            frames,
            media_type="video/mp4",
            source_namespace="fixture",
            source_locator=Locator("external_record", {"source_ref": "long-video-centroid"}),
        )
        whole = catalog.connection.execute(
            "SELECT metadata_json FROM core_search_units WHERE resource_id=? AND unit_kind='whole_resource'",
            (resource,),
        ).fetchone()

    assert result.transcript_unit_count == 1
    assert result.frame_unit_count == 1
    assert result.vector_count == 3
    assert whole is not None
    assert json.loads(whole[0])["aggregation"] == "token_weighted_centroid_v1"


@pytest.mark.asyncio
async def test_failed_complete_replace_preserves_prior_transcript_and_frames(
    tmp_path: Path,
    video_inputs: tuple[object, object, bytes, str],
) -> None:
    transcript, frames, _, resource = video_inputs
    with SQLiteCatalog.create(tmp_path / "atomic.sqlite3") as catalog:
        await VideoCompositionService(catalog).ingest(
            transcript,  # type: ignore[arg-type]
            frames,  # type: ignore[arg-type]
            media_type="video/mp4",
            source_namespace="fixture",
            source_locator=Locator("external_record", {"source_ref": "video-complete"}),
            chunking_policy=_policy(),
            embeddings=False,
        )
        before = tuple(catalog.connection.iterdump())
        failing = _CountingCatalog(catalog, fail=True)
        with pytest.raises(Exception):
            await VideoCompositionService(failing).ingest(
                transcript,  # type: ignore[arg-type]
                frames,  # type: ignore[arg-type]
                media_type="video/mp4",
                source_namespace="fixture",
                source_locator=Locator("external_record", {"source_ref": "video-complete"}),
                chunking_policy=_policy(),
                embeddings=False,
            )
        after = tuple(catalog.connection.iterdump())
        assert catalog.read_resource(resource) is not None
    assert failing.replace_calls == 1
    assert after == before


def test_frame_manifest_rejects_duplicate_observations_without_source_access(
    video_inputs: tuple[object, object, bytes, str],
) -> None:
    _, frames, _, resource = video_inputs
    producer = frames.producer_fingerprint.value  # type: ignore[attr-defined]
    duplicate = {
        "schema": "mdrack.frame-captions.v1",
        "resource_id": resource,
        "producer_fingerprint": producer,
        "normalization_fingerprint": None,
        "metadata": {},
        "frames": [
            {"frame_id": "same", "timestamp_ms": 1, "caption": "same", "metadata": {}},
            {"frame_id": "same", "timestamp_ms": 2, "caption": "other", "metadata": {}},
        ],
    }
    with pytest.raises(FrameCaptionManifestError, match="frame_manifest_duplicate"):
        read_frame_captions(json.dumps(duplicate).encode())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("defect", "error"),
    [
        ("duplicate_identity", "frame_manifest_duplicate"),
        ("duplicate_pair", "frame_manifest_duplicate"),
        ("forbidden_metadata", "frame_manifest_forbidden_metadata"),
    ],
)
async def test_application_boundary_rejects_invalid_frames_before_provider_or_replace(
    tmp_path: Path,
    video_inputs: tuple[object, object, bytes, str],
    defect: str,
    error: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    transcript, frames, _, resource = video_inputs
    invalid = _invalid_frames(frames, defect=defect)
    provider = _CountingProvider()
    fingerprint = EmbeddingFingerprint.from_payload({"provider": "counting", "dimensions": 8, "version": 1}).value
    caplog.set_level(logging.INFO)

    with SQLiteCatalog.create(tmp_path / "validation.sqlite3") as sqlite_catalog:
        await VideoCompositionService(sqlite_catalog).ingest(
            transcript,  # type: ignore[arg-type]
            frames,  # type: ignore[arg-type]
            media_type="video/mp4",
            source_namespace="fixture",
            source_locator=Locator("external_record", {"source_ref": "video-complete"}),
            chunking_policy=_policy(),
            embeddings=False,
        )
        before = tuple(sqlite_catalog.connection.iterdump())
        catalog = _CountingCatalog(sqlite_catalog)
        with pytest.raises(FrameCaptionManifestError, match=f"^{error}$"):
            await VideoCompositionService(
                catalog,
                embedding_provider=provider,
                embedding_fingerprint=fingerprint,
            ).ingest(
                transcript,  # type: ignore[arg-type]
                invalid,
                media_type="video/mp4",
                source_namespace="fixture",
                source_locator=Locator("external_record", {"source_ref": "PRIVATE_SOURCE_LOCATOR"}),
                chunking_policy=_policy(),
            )
        after = tuple(sqlite_catalog.connection.iterdump())
        assert sqlite_catalog.read_resource(resource) is not None

    assert provider.calls == 0
    assert catalog.replace_calls == 0
    assert after == before
    assert "PRIVATE_PROVIDER_BODY" not in caplog.text
    assert "/PRIVATE/frame.png" not in caplog.text
