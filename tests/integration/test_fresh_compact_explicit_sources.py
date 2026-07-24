"""Explicit immutable media-source coverage for fresh compact rebuilds."""

from __future__ import annotations

import hashlib
from pathlib import Path

from mdrack.adapters.sqlite.generation_runtime import SQLiteGenerationRuntime
from mdrack.application.fresh_reindex import FreshCompactReindexService
from mdrack.application.generation_manager import StoreGenerationManager
from mdrack.application.store_generations import GenerationState
from mdrack.config.models import MDRackConfig, PathsConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.storage.sqlite.connection import get_connection
from mdrack_core.domain import (
    EmbeddingSpaceRecord,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)


class _TranscriptSource:
    def __init__(self, path: Path) -> None:
        self.path = path

    def source_digest(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def prepare_batches(self) -> tuple[PreparedResourceBatch, ...]:
        source_hash = self.source_digest()
        resource_id = "audio-transcript"
        representation_id = "audio-transcript-text"
        unit_id = "audio-transcript-unit"
        return (
            PreparedResourceBatch(
                ResourceRecord(
                    resource_id,
                    "audio",
                    "text/vtt",
                    "explicit-media",
                    Locator("portable", {"resource": resource_id}),
                    f"sha256:{source_hash}",
                ),
                [RepresentationRecord(
                    representation_id,
                    resource_id,
                    "timed_text",
                    "text",
                    "Explicit transcript text.",
                )],
                [SearchUnitRecord(
                    unit_id,
                    resource_id,
                    representation_id,
                    "timed_passage",
                    "text",
                    "Explicit transcript text.",
                    Locator("timed", {"end_ms": 1000, "start_ms": 0}),
                    0,
                )],
                [EmbeddingSpaceRecord("transcript-space", 2, "cosine", "transcript-fingerprint")],
                [VectorRecord(unit_id, "transcript-space", (1.0 + 2**-30, -0.0))],
            ),
        )


def test_fresh_reindex_accepts_explicit_immutable_transcript_source(tmp_path: Path) -> None:
    root = tmp_path / "markdown-root"
    root.mkdir()
    transcript = tmp_path / "media" / "captions.vtt"
    transcript.parent.mkdir()
    transcript.write_bytes(b"WEBVTT\n\n00:00.000 --> 00:01.000\nExplicit transcript text.\n")
    source_digest = hashlib.sha256(transcript.read_bytes()).hexdigest()
    store_dir = tmp_path / "store"
    manager = StoreGenerationManager(
        store_dir,
        runtime=SQLiteGenerationRuntime(),
        id_factory=lambda: "explicit-source",
    )
    service = FreshCompactReindexService(
        root=root,
        config=MDRackConfig(paths=PathsConfig(root=".", store=str(store_dir))),
        provider=FakeEmbeddingProvider(dimensions=8),
        manager=manager,
        source_inputs=(_TranscriptSource(transcript),),
    )

    candidate = service.rebuild()

    assert candidate.generation.state is GenerationState.READY
    assert candidate.source_count == 1
    assert hashlib.sha256(transcript.read_bytes()).hexdigest() == source_digest
    connection = get_connection(manager.database_path(candidate.generation.generation_id))
    try:
        assert connection.execute("SELECT COUNT(*) FROM core_resources").fetchone()[0] == 1
        assert connection.execute("SELECT length(embedding) FROM core_unit_embeddings").fetchone()[0] == 8
        metadata = connection.execute("SELECT metadata_json FROM core_embedding_spaces").fetchone()[0]
        assert '"vector_codec":"ieee754-f32-le-v1"' in metadata
    finally:
        connection.close()
