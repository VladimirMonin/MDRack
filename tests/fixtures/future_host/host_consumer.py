"""External-host consumer fixture; imports only installed public packages."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

from mdrack_core import Locator
from mdrack_core.application import CoreIndexingService
from mdrack_media import (
    REPRESENTATION_AUDIO_TRANSCRIPT,
    REPRESENTATION_FRAME_CAPTION,
    REPRESENTATION_TIMED_PASSAGE,
    FrameBatchBuilderInput,
    FrameCaptionArtifact,
    FrameCaptionObservation,
    MediaResourceDescriptor,
    NormalizationFingerprint,
    ProducerFingerprint,
    TimedChunkingPolicy,
    TimedTextAtom,
    TokenCount,
    TokenCounterFingerprint,
    TranscriptArtifact,
    TranscriptBatchBuilderInput,
    atom_id,
    build_video_frame_caption_batch,
    build_video_transcript_batch,
    frame_id,
    group_timed_atoms,
    representation_id,
    resource_id,
)
from mdrack_sqlite import SQLiteCatalog


@dataclass(frozen=True)
class HostTranscriptDTO:
    source_id: str
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class HostFrameDTO:
    source_id: str
    timestamp_ms: int
    caption: str


class HostTokenCounter:
    fingerprint = TokenCounterFingerprint.from_payload({"host": "word-count", "version": 1})

    def count(self, text: str) -> int:
        return len(text.split())


def _transcript_batch(resource: str, items: tuple[HostTranscriptDTO, ...]):
    producer = ProducerFingerprint.from_payload({"host": "fixture-transcript", "version": 1})
    normalization = NormalizationFingerprint.from_payload({"host": "identity", "version": 1})
    grouper_policy = TimedChunkingPolicy()
    counter = HostTokenCounter()
    atoms = tuple(
        TimedTextAtom(
            atom_id=atom_id(resource, producer.value, ordinal),
            resource_id=resource,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            text=item.text,
            ordinal=ordinal,
            producer_fingerprint=producer,
            normalization_fingerprint=normalization,
        )
        for ordinal, item in enumerate(items)
    )
    transcript_representation = representation_id(
        resource, REPRESENTATION_AUDIO_TRANSCRIPT, producer.value, normalization.value
    )
    transcript = TranscriptArtifact(
        resource_id=resource,
        representation_id=transcript_representation,
        representation_kind=REPRESENTATION_AUDIO_TRANSCRIPT,
        atoms=atoms,
        producer_fingerprint=producer,
        normalization_fingerprint=normalization,
        duration_ms=items[-1].end_ms,
    )
    grouped = group_timed_atoms(
        atoms,
        policy=grouper_policy,
        token_counter=counter,
        token_count_kind="exact",
        resource_identifier=resource,
        normalization_fingerprint=normalization,
    )
    return build_video_transcript_batch(
        TranscriptBatchBuilderInput(
            resource=MediaResourceDescriptor(
                resource_id=resource,
                resource_kind="video",
                media_type="video/mp4",
                source_namespace="future-host",
                locator=Locator("host_video", {"source_id": items[0].source_id}),
            ),
            transcript=transcript,
            passage_representation_id=representation_id(
                resource, REPRESENTATION_TIMED_PASSAGE, grouped.grouper_fingerprint.value, normalization.value
            ),
            passage_representation_kind=REPRESENTATION_TIMED_PASSAGE,
            chunking_policy=grouper_policy,
            grouper_fingerprint=grouped.grouper_fingerprint,
        ),
        token_counter=counter,
    )


def _frame_batch(resource: str, items: tuple[HostFrameDTO, ...]):
    producer = ProducerFingerprint.from_payload({"host": "fixture-caption", "version": 1})
    normalization = NormalizationFingerprint.from_payload({"host": "identity", "version": 1})
    counter = TokenCounterFingerprint.from_payload({"host": "word-count", "version": 1})
    observations = tuple(
        FrameCaptionObservation(
            frame_id=frame_id(resource, producer.value, ordinal, item.timestamp_ms, item.source_id),
            resource_id=resource,
            timestamp_ms=item.timestamp_ms,
            observation_identity=item.source_id,
            caption=item.caption,
            ordinal=ordinal,
            token_count=TokenCount(len(item.caption.split()), "estimated", counter),
            producer_fingerprint=producer,
            normalization_fingerprint=normalization,
        )
        for ordinal, item in enumerate(items)
    )
    artifact = FrameCaptionArtifact(
        resource_id=resource,
        representation_id=representation_id(
            resource, REPRESENTATION_FRAME_CAPTION, producer.value, normalization.value
        ),
        representation_kind=REPRESENTATION_FRAME_CAPTION,
        observations=observations,
        producer_fingerprint=producer,
        normalization_fingerprint=normalization,
    )
    return build_video_frame_caption_batch(
        FrameBatchBuilderInput(
            resource=MediaResourceDescriptor(
                resource_id=resource,
                resource_kind="video",
                media_type="video/mp4",
                source_namespace="future-host",
                locator=Locator("host_video", {"source_id": items[0].source_id}),
            ),
            frames=artifact,
        )
    )


def run(database: str) -> dict[str, Any]:
    transcript_dto = (HostTranscriptDTO("clip-a", 0, 1_000, "host transcript"),)
    frame_dto = (
        HostFrameDTO("frame-a", 100, "red object"),
        HostFrameDTO("frame-b", 900, "blue object"),
    )
    transcript_resource = resource_id("future-host", "clip-a-transcript")
    frame_resource = resource_id("future-host", "clip-a-frames")
    transcript_batch = _transcript_batch(transcript_resource, transcript_dto)
    frame_batch = _frame_batch(frame_resource, frame_dto)
    resolver = {item.source_id: item for item in frame_dto}
    frame_units = {item.source_id: unit for item, unit in zip(frame_dto, frame_batch.units)}

    with SQLiteCatalog.create(database) as catalog:
        indexer = CoreIndexingService(catalog)
        indexer.index(transcript_batch)
        indexer.index(frame_batch)
        resolved = resolver["frame-b"]
        resolved_unit = catalog.read_unit(frame_units[resolved.source_id].unit_id)
        assert resolved_unit is not None
        assert resolved_unit.evidence_locator.payload["timestamp_ms"] == resolved.timestamp_ms
        before_delete = catalog.verify()
        indexer.delete(frame_resource)
        assert catalog.read_resource(frame_resource) is None
        indexer.index(frame_batch)
        after_rebuild = catalog.verify()

    return {
        "contract": "future-host-consumer-v1",
        "resources": [transcript_resource, frame_resource],
        "resolved": {"source_id": resolved.source_id, "timestamp_ms": resolved.timestamp_ms},
        "before_delete": before_delete.resources,
        "after_rebuild": after_rebuild.resources,
        "schema_id": after_rebuild.schema_id,
        "installed_modules": {
            "mdrack_core": __import__("mdrack_core").__file__,
            "mdrack_media": __import__("mdrack_media").__file__,
            "mdrack_sqlite": __import__("mdrack_sqlite").__file__,
        },
    }


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1]), sort_keys=True))
