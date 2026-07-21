"""I1 transcript ingestion and timed retrieval against real local SQLite."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from mdrack.application.transcript_ingestion import (
    TimedRetrievalService,
    TranscriptIngestionService,
)
from mdrack.config.models import MDRackConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.ingestion.transcripts import read_transcript
from mdrack.ports.embeddings import EmbeddingError
from mdrack.public_api import MDRackEngine
from mdrack.public_api.models import (
    TimedSearchResult,
    TranscriptIngestionResult,
)
from mdrack_core import EmbeddingSpaceRecord, Locator, RankedCandidate, SearchScope
from mdrack_media import (
    EmbeddingFingerprint,
    ProducerFingerprint,
    TimedChunkingPolicy,
    TokenCounterFingerprint,
    resource_id,
)
from mdrack_sqlite import SQLiteCatalog


class _OrderedProvider(FakeEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__(dimensions=8)
        self.completed = False

    async def embed(self, texts, profile: str = "default"):  # type: ignore[no-untyped-def]
        vectors = await super().embed(texts, profile=profile)
        self.completed = True
        return vectors


class _FailingProvider(FakeEmbeddingProvider):
    async def embed(self, texts, profile: str = "default"):  # type: ignore[no-untyped-def]
        raise RuntimeError("private provider failure")


class _OrderedCatalog:
    def __init__(self, catalog: SQLiteCatalog, provider: _OrderedProvider) -> None:
        self._catalog = catalog
        self._provider = provider

    @property
    def connection(self):  # type: ignore[no-untyped-def]
        return self._catalog.connection

    def replace_resource(self, batch) -> None:  # type: ignore[no-untyped-def]
        assert self._provider.completed
        self._catalog.replace_resource(batch)

    def search_lexical(self, branch, *, scope):  # type: ignore[no-untyped-def]
        return self._catalog.search_lexical(branch, scope=scope)

    def search_vector(self, branch, *, scope):  # type: ignore[no-untyped-def]
        return self._catalog.search_vector(branch, scope=scope)

    def resolve_embedding_space(self, *, fingerprint, dimensions):  # type: ignore[no-untyped-def]
        return self._catalog.resolve_embedding_space(
            fingerprint=fingerprint,
            dimensions=dimensions,
        )

    def read_resource(self, resource_identifier):  # type: ignore[no-untyped-def]
        return self._catalog.read_resource(resource_identifier)

    def read_unit(self, unit_identifier):  # type: ignore[no-untyped-def]
        return self._catalog.read_unit(unit_identifier)

    def find_by_content_hash(self, content_hash, *, scope):  # type: ignore[no-untyped-def]
        return self._catalog.find_by_content_hash(content_hash, scope=scope)

    def delete_resource(self, resource_identifier):  # type: ignore[no-untyped-def]
        return self._catalog.delete_resource(resource_identifier)


class _EngineStorage:
    def __init__(self, catalog: SQLiteCatalog) -> None:
        self.resource_store = catalog

    def close(self) -> None:
        pass


class _ExactCounter:
    fingerprint = TokenCounterFingerprint.from_payload(
        {"algorithm": "fixture-exact-v1", "version": 1}
    )

    def count(self, text: str) -> int:
        return len(text.split())


class _TimedSearchPort:
    def __init__(self, *, fingerprint: str) -> None:
        self.fingerprint = fingerprint
        self.calls: list[tuple[str, object, SearchScope]] = []

    def resolve_embedding_space(
        self,
        *,
        fingerprint: str,
        dimensions: int,
    ) -> EmbeddingSpaceRecord | None:
        assert fingerprint == self.fingerprint
        return EmbeddingSpaceRecord("generic-space", dimensions, "cosine", fingerprint)

    def search_lexical(self, branch, *, scope):  # type: ignore[no-untyped-def]
        self.calls.append(("lexical", branch, scope))
        return [self._candidate(branch.branch_id, rank=1)]

    def search_vector(self, branch, *, scope):  # type: ignore[no-untyped-def]
        self.calls.append(("vector", branch, scope))
        return [self._candidate(branch.branch_id, rank=1)]

    @staticmethod
    def _candidate(
        branch_id: str,
        *,
        rank: int,
        resource_identifier: str = "generic-resource",
    ) -> RankedCandidate:
        return RankedCandidate(
            unit_id=f"generic-unit-{rank}",
            resource_id=resource_identifier,
            representation_id="generic-representation",
            rank=rank,
            raw_score=1.0 / rank,
            branch_id=branch_id,
            evidence_locator=Locator(
                "time_segment",
                {"start_ms": rank - 1, "end_ms": rank, "track": "audio"},
            ),
        )


class _CrowdedTimedSearchPort(_TimedSearchPort):
    def search_lexical(self, branch, *, scope):  # type: ignore[no-untyped-def]
        self.calls.append(("lexical", branch, scope))
        crowded = [
            self._candidate(branch.branch_id, rank=rank, resource_identifier="crowded")
            for rank in range(1, 101)
        ]
        return [
            *crowded,
            self._candidate(branch.branch_id, rank=101, resource_identifier="sparse"),
        ]


@pytest.fixture
def transcript_source() -> bytes:
    return json.dumps(
        {
            "language": "en",
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "private opening words"},
                {"start": 1.0, "end": 2.0, "text": "transaction boundary explained"},
                {"start": 2.0, "end": 3.0, "text": "private closing words"},
            ],
        },
        separators=(",", ":"),
    ).encode()


def _policy() -> TimedChunkingPolicy:
    return TimedChunkingPolicy(
        soft_min_tokens=1,
        target_tokens=3,
        soft_max_tokens=5,
        hard_max_tokens=8,
        soft_min_duration_ms=1,
        target_duration_ms=1_000,
        soft_max_duration_ms=1_000,
        hard_max_duration_ms=2_000,
    )


def test_default_estimated_and_injected_exact_token_provenance_change_identity(
    tmp_path: Path,
    transcript_source: bytes,
) -> None:
    resource = resource_id("fixture", "token-provenance")
    artifact = read_transcript(
        transcript_source,
        resource_id=resource,
        producer_fingerprint=ProducerFingerprint.from_payload(
            {"producer": "fixture", "version": 1}
        ),
    ).artifact
    with SQLiteCatalog.create(tmp_path / "token-provenance.sqlite3") as catalog:
        estimated = TranscriptIngestionService(catalog).prepare(
            artifact,
            resource_kind="audio",
            media_type="audio/wav",
            source_namespace="fixture",
            source_locator=Locator(
                "external_record",
                {"source_ref": "token-provenance"},
            ),
            chunking_policy=_policy(),
        )
        exact = TranscriptIngestionService(
            catalog,
            token_counter=_ExactCounter(),
            token_count_kind="exact",
        ).prepare(
            artifact,
            resource_kind="audio",
            media_type="audio/wav",
            source_namespace="fixture",
            source_locator=Locator(
                "external_record",
                {"source_ref": "token-provenance"},
            ),
            chunking_policy=_policy(),
        )
        catalog.replace_resource(estimated)
        estimated_kinds = {
            str(row[0])
            for row in catalog.connection.execute(
                "SELECT token_count_kind FROM core_search_units"
            )
        }
        catalog.replace_resource(exact)
        exact_kinds = {
            str(row[0])
            for row in catalog.connection.execute(
                "SELECT token_count_kind FROM core_search_units"
            )
        }

    assert estimated.representations[0].token_count_kind == "estimated"
    assert {unit.token_count_kind for unit in estimated.units} == {"estimated"}
    assert estimated_kinds == {"estimated"}
    assert exact.representations[0].token_count_kind == "exact"
    assert {unit.token_count_kind for unit in exact.units} == {"exact"}
    assert exact_kinds == {"exact"}
    assert estimated.representations[0].representation_id != exact.representations[0].representation_id


def test_injected_token_counter_requires_explicit_provenance(tmp_path: Path) -> None:
    with SQLiteCatalog.create(tmp_path / "counter-contract.sqlite3") as catalog:
        with pytest.raises(ValueError, match="token_count_kind"):
            TranscriptIngestionService(catalog, token_counter=_ExactCounter())


@pytest.mark.asyncio
async def test_generic_port_semantic_search_uses_provider_neutral_space_resolution() -> None:
    fingerprint = EmbeddingFingerprint.from_payload(
        {"provider": "fake", "dimensions": 8, "version": 1}
    ).value
    port = _TimedSearchPort(fingerprint=fingerprint)
    result = await TimedRetrievalService(
        port,
        embedding_provider=FakeEmbeddingProvider(dimensions=8),
        embedding_fingerprint=fingerprint,
    ).search("query", mode="semantic")

    assert result.results and result.degraded is False
    assert [kind for kind, _, _ in port.calls] == ["vector"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit", "expected_budget"),
    [(1, 100), (10, 100), (11, 110)],
)
async def test_timed_branches_use_normative_candidate_budget(
    limit: int,
    expected_budget: int,
) -> None:
    fingerprint = EmbeddingFingerprint.from_payload(
        {"provider": "fake", "dimensions": 8, "version": 1}
    ).value
    port = _TimedSearchPort(fingerprint=fingerprint)
    await TimedRetrievalService(
        port,
        embedding_provider=FakeEmbeddingProvider(dimensions=8),
        embedding_fingerprint=fingerprint,
    ).search("query", mode="hybrid", limit=limit)

    assert len(port.calls) == 2
    assert {getattr(branch, "candidate_limit") for _, branch, _ in port.calls} == {
        expected_budget
    }


@pytest.mark.asyncio
async def test_resource_grouping_sees_sparse_resource_beyond_first_hundred_units() -> None:
    port = _CrowdedTimedSearchPort(fingerprint="unused")
    result = await TimedRetrievalService(port).search(
        "query",
        mode="text",
        target="resource",
        limit=20,
    )

    assert [item.resource_id for item in result.results] == ["crowded", "sparse"]


@pytest.mark.asyncio
async def test_transcript_provider_finishes_before_atomic_replace_and_all_modes_return_timing(
    tmp_path: Path,
    transcript_source: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source_before = bytes(transcript_source)
    resource = resource_id("fixture", "audio-1")
    artifact = read_transcript(
        transcript_source,
        resource_id=resource,
        producer_fingerprint=ProducerFingerprint.from_payload(
            {"producer": "fixture", "version": 1}
        ),
    ).artifact
    provider = _OrderedProvider()
    embedding_fingerprint = EmbeddingFingerprint.from_payload(
        {"provider": "fake", "dimensions": 8, "version": 1}
    ).value
    database = tmp_path / "transcripts.sqlite3"

    caplog.set_level(logging.INFO)
    with SQLiteCatalog.create(database) as sqlite_catalog:
        catalog = _OrderedCatalog(sqlite_catalog, provider)
        ingestion = TranscriptIngestionService(
            catalog,
            embedding_provider=provider,
            embedding_fingerprint=embedding_fingerprint,
        )
        ingested = await ingestion.ingest(
            artifact,
            resource_kind="audio",
            media_type="audio/wav",
            source_namespace="fixture",
            source_locator=Locator("external_record", {"source_ref": "audio-1"}),
            chunking_policy=_policy(),
        )
        whole_unit_id = str(
            sqlite_catalog.connection.execute(
                "SELECT unit_id FROM core_search_units "
                "WHERE resource_id=? AND unit_kind='whole_resource'",
                (resource,),
            ).fetchone()[0]
        )
        stored_whole = sqlite_catalog.read_unit(whole_unit_id)
        retrieval = TimedRetrievalService(
            catalog,
            embedding_provider=provider,
            embedding_fingerprint=embedding_fingerprint,
        )
        text = await retrieval.search("transaction", mode="text")
        semantic = await retrieval.search("transaction boundary explained", mode="semantic")
        hybrid = await retrieval.search(
            "transaction boundary explained",
            mode="hybrid",
            target="resource",
        )
        wrong_kind = await retrieval.search(
            "transaction",
            mode="text",
            scope=SearchScope(resource_kinds=("document",)),
        )

    assert provider.completed
    assert ingested.vector_count == ingested.unit_count > 0
    assert ingested.space_id is not None
    assert stored_whole is not None
    assert stored_whole.metadata["aggregation"] == "direct_text_v1"
    assert text.results[0].evidence[0].start_ms == 1_000
    assert text.results[0].evidence[0].end_ms == 2_000
    assert text.results[0].evidence[0].track == "audio"
    assert semantic.results and semantic.degraded is False
    assert hybrid.results and hybrid.target == "resource"
    assert hybrid.results[0].unit_id is None
    assert wrong_kind.results == ()
    assert transcript_source == source_before
    captured = caplog.text
    assert "private opening words" not in captured
    assert "transaction boundary explained" not in captured
    assert "audio-1" not in captured


@pytest.mark.asyncio
async def test_long_transcript_persists_token_weighted_centroid_identity(
    tmp_path: Path,
) -> None:
    resource = resource_id("fixture", "long-centroid")
    source = json.dumps(
        {
            "segments": [
                {
                    "start": float(index),
                    "end": float(index + 1),
                    "text": " ".join(f"token-{index}-{token}" for token in range(500)),
                }
                for index in range(17)
            ]
        },
        separators=(",", ":"),
    ).encode()
    artifact = read_transcript(
        source,
        resource_id=resource,
        producer_fingerprint=ProducerFingerprint.from_payload(
            {"producer": "long-fixture", "version": 1}
        ),
    ).artifact
    fingerprint = EmbeddingFingerprint.from_payload(
        {"provider": "fake", "dimensions": 8, "version": 1}
    ).value

    with SQLiteCatalog.create(tmp_path / "centroid.sqlite3") as catalog:
        result = await TranscriptIngestionService(
            catalog,
            embedding_provider=FakeEmbeddingProvider(dimensions=8),
            embedding_fingerprint=fingerprint,
        ).ingest(
            artifact,
            resource_kind="audio",
            media_type="audio/wav",
            source_namespace="fixture",
            source_locator=Locator("external_record", {"source_ref": "long-centroid"}),
        )
        whole_unit_id = str(
            catalog.connection.execute(
                "SELECT unit_id FROM core_search_units "
                "WHERE resource_id=? AND unit_kind='whole_resource'",
                (resource,),
            ).fetchone()[0]
        )
        whole_unit = catalog.read_unit(whole_unit_id)
        whole_vector = catalog.read_vector(whole_unit_id, result.space_id or "")

    assert whole_unit is not None
    assert whole_unit.metadata["aggregation"] == "token_weighted_centroid_v1"
    assert whole_vector is not None
    assert result.vector_count == result.unit_count


@pytest.mark.asyncio
async def test_lexical_replace_removes_ready_vectors_and_hybrid_degrades_safely(
    tmp_path: Path,
    transcript_source: bytes,
) -> None:
    resource = resource_id("fixture", "video-1")
    artifact = read_transcript(
        transcript_source,
        resource_id=resource,
        producer_fingerprint=ProducerFingerprint.from_payload(
            {"producer": "fixture", "version": 1}
        ),
    ).artifact
    provider = FakeEmbeddingProvider(dimensions=8)
    embedding_fingerprint = EmbeddingFingerprint.from_payload(
        {"provider": "fake", "dimensions": 8, "version": 1}
    ).value
    database = tmp_path / "replace.sqlite3"

    with SQLiteCatalog.create(database) as catalog:
        vector_ingestion = TranscriptIngestionService(
            catalog,
            embedding_provider=provider,
            embedding_fingerprint=embedding_fingerprint,
        )
        await vector_ingestion.ingest(
            artifact,
            resource_kind="video",
            media_type="video/mp4",
            source_namespace="fixture",
            source_locator=Locator("external_record", {"source_ref": "video-1"}),
            chunking_policy=_policy(),
        )
        before_ids = tuple(
            str(row[0])
            for row in catalog.connection.execute(
                "SELECT unit_id FROM core_search_units WHERE resource_id=? ORDER BY unit_id",
                (resource,),
            ).fetchall()
        )
        assert catalog.connection.execute(
            "SELECT COUNT(*) FROM core_unit_embeddings"
        ).fetchone()[0]

        lexical_ingestion = TranscriptIngestionService(catalog)
        replaced = await lexical_ingestion.ingest(
            artifact,
            resource_kind="video",
            media_type="video/mp4",
            source_namespace="fixture",
            source_locator=Locator("external_record", {"source_ref": "video-1"}),
            chunking_policy=_policy(),
            embeddings=False,
        )
        hybrid = await TimedRetrievalService(catalog).search(
            "transaction",
            mode="hybrid",
        )
        semantic = await TimedRetrievalService(catalog).search(
            "transaction",
            mode="semantic",
        )

        assert replaced.vector_count == 0
        assert catalog.connection.execute(
            "SELECT COUNT(*) FROM core_unit_embeddings"
        ).fetchone()[0] == 0
        assert tuple(
            str(row[0])
            for row in catalog.connection.execute(
                "SELECT unit_id FROM core_search_units WHERE resource_id=? ORDER BY unit_id",
                (resource,),
            ).fetchall()
        ) == before_ids
    assert hybrid.results
    assert hybrid.degraded is True
    assert hybrid.degraded_reason == "embedding_provider_unavailable"
    assert semantic.results == ()
    assert semantic.degraded_reason == "embedding_provider_unavailable"


@pytest.mark.asyncio
async def test_provider_failure_preserves_previous_complete_resource(
    tmp_path: Path,
    transcript_source: bytes,
) -> None:
    resource = resource_id("fixture", "failure-audio")
    artifact = read_transcript(
        transcript_source,
        resource_id=resource,
        producer_fingerprint=ProducerFingerprint.from_payload(
            {"producer": "fixture", "version": 1}
        ),
    ).artifact
    database = tmp_path / "failure.sqlite3"
    fingerprint = EmbeddingFingerprint.from_payload(
        {"provider": "fake", "dimensions": 8, "version": 1}
    ).value

    with SQLiteCatalog.create(database) as catalog:
        await TranscriptIngestionService(catalog).ingest(
            artifact,
            resource_kind="audio",
            media_type="audio/wav",
            source_namespace="fixture",
            source_locator=Locator(
                "external_record", {"source_ref": "failure-audio"}
            ),
            chunking_policy=_policy(),
            embeddings=False,
        )
        before = tuple(catalog.connection.iterdump())

        with pytest.raises(EmbeddingError, match="embedding_provider_error"):
            await TranscriptIngestionService(
                catalog,
                embedding_provider=_FailingProvider(dimensions=8),
                embedding_fingerprint=fingerprint,
            ).ingest(
                artifact,
                resource_kind="audio",
                media_type="audio/wav",
                source_namespace="fixture",
                source_locator=Locator(
                    "external_record", {"source_ref": "failure-audio"}
                ),
                chunking_policy=_policy(),
            )

        after = tuple(catalog.connection.iterdump())
    assert after == before


@pytest.mark.asyncio
async def test_engine_transcript_ingestion_and_search_match_application_surface(
    tmp_path: Path,
    transcript_source: bytes,
) -> None:
    resource = resource_id("fixture", "engine-audio")
    artifact = read_transcript(
        transcript_source,
        resource_id=resource,
        producer_fingerprint=ProducerFingerprint.from_payload(
            {"producer": "fixture", "version": 1}
        ),
    ).artifact
    database = tmp_path / "engine.sqlite3"

    with SQLiteCatalog.create(database) as catalog:
        engine = MDRackEngine(
            root=tmp_path,
            config=MDRackConfig(),
            embedding_provider=FakeEmbeddingProvider(dimensions=8),
            storage=_EngineStorage(catalog),  # type: ignore[arg-type]
        )
        ingested = await engine.ingest_transcript(
            artifact,
            resource_kind="audio",
            media_type="audio/wav",
            source_namespace="fixture",
            source_locator=Locator(
                "external_record", {"source_ref": "engine-audio"}
            ),
            chunking_policy=_policy(),
        )
        found = await engine.search_transcripts("transaction", mode="text")

    assert ingested.unit_count == ingested.vector_count
    assert isinstance(ingested, TranscriptIngestionResult)
    assert isinstance(found, TimedSearchResult)
    assert found.results[0].resource_id == resource
    assert found.results[0].evidence[0].to_dict() == {
        "unit_id": found.results[0].evidence[0].unit_id,
        "representation_id": found.results[0].evidence[0].representation_id,
        "start_ms": 1_000,
        "end_ms": 2_000,
        "track": "audio",
        "timestamp_unit": "ms",
    }
