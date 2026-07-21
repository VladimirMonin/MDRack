from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from mdrack.application.metadata_filters import MetadataFilter, MetadataFilters
from mdrack.cli import main
from mdrack.config.models import MDRackConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.public_api import MDRackEngine
from mdrack_core import (
    EmbeddingSpaceRecord,
    Facet,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)
from mdrack_sqlite import SQLiteCatalog


def _batch(
    resource_id: str,
    vector: tuple[float, float],
    *,
    modality: str = "text",
    similarity_basis: str = "transcript_text",
    aggregation: str = "direct_text_v1",
) -> PreparedResourceBatch:
    representation_id = f"representation-{resource_id}"
    unit_id = f"whole-{resource_id}"
    return PreparedResourceBatch(
        ResourceRecord(
            resource_id,
            "video",
            "video/mp4",
            "fixture",
            Locator("video", {"id": resource_id}),
        ),
        (
            RepresentationRecord(
                representation_id,
                resource_id,
                "transcript_text",
                modality,
                "textual transcript",
            ),
        ),
        (
            SearchUnitRecord(
                unit_id,
                resource_id,
                representation_id,
                "whole_resource",
                modality,
                "textual transcript",
                Locator("whole_media", {}),
                0,
                metadata={
                    "similarity_basis": similarity_basis,
                    "aggregation": aggregation,
                },
            ),
        ),
        (EmbeddingSpaceRecord("text-space", 2, "cosine", "text-fingerprint"),),
        (VectorRecord(unit_id, "text-space", vector),),
    )


def _search_batch(
    resource_id: str,
    *,
    transcript: str,
    frame_caption: str,
    tags: tuple[str, ...] = (),
) -> PreparedResourceBatch:
    transcript_representation = f"transcript-{resource_id}"
    frame_representation = f"frames-{resource_id}"
    return PreparedResourceBatch(
        ResourceRecord(
            resource_id,
            "video",
            "video/mp4",
            "fixture",
            Locator("video", {"id": resource_id}),
        ),
        (
            RepresentationRecord(
                transcript_representation,
                resource_id,
                "timed_passage",
                "text",
                transcript,
            ),
            RepresentationRecord(
                frame_representation,
                resource_id,
                "frame_caption",
                "text",
                frame_caption,
            ),
        ),
        (
            SearchUnitRecord(
                f"passage-{resource_id}",
                resource_id,
                transcript_representation,
                "time_segment",
                "text",
                transcript,
                Locator("time_segment", {"start_ms": 0, "end_ms": 1000}),
                0,
            ),
            SearchUnitRecord(
                f"frame-{resource_id}",
                resource_id,
                frame_representation,
                "frame",
                "text",
                frame_caption,
                Locator("video_frame", {"timestamp_ms": 500}),
                0,
            ),
        ),
        facets=tuple(
            ResourceFacet(resource_id, Facet("tag", f"s:{tag}"), "source")
            for tag in tags
        ),
    )


class _EngineStorage:
    def __init__(self, catalog: SQLiteCatalog) -> None:
        self.resource_store = catalog

    def close(self) -> None:
        self.resource_store.close()


class _EmptyQueryProvider(FakeEmbeddingProvider):
    async def embed_query(self, text: str, profile: str = "default") -> list[float]:
        del text, profile
        return []


def test_cli_and_engine_textual_similarity_workflow_have_exact_parity(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite3"
    with SQLiteCatalog.create(database) as catalog:
        catalog.replace_resource(_batch("query", (1.0, 0.0)))
        catalog.replace_resource(
            _batch(
                "visual",
                (1.0, 0.0),
                modality="image",
                similarity_basis="visual_content",
            )
        )
        catalog.replace_resource(
            _batch(
                "acoustic",
                (0.99, 0.01),
                modality="audio",
                similarity_basis="acoustic_content",
            )
        )
        catalog.replace_resource(_batch("near", (0.9, 0.1)))
        catalog.replace_resource(_batch("far", (0.1, 0.9)))

    api_catalog = SQLiteCatalog.open_readonly(database)
    engine = MDRackEngine(
        root=tmp_path,
        config=MDRackConfig(),
        storage=_EngineStorage(api_catalog),  # type: ignore[arg-type]
        search_index=api_catalog,  # type: ignore[arg-type]
        read_storage=api_catalog,  # type: ignore[arg-type]
    )
    try:
        api = engine.find_textually_similar_resources(
            "whole-query",
            "text-space",
            aggregation="direct_text_v1",
            expected_fingerprint="text-fingerprint",
            limit=2,
        ).to_dict()
    finally:
        engine.close()

    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "resource",
            "similar",
            "whole-query",
            "--catalog",
            str(database),
            "--space-id",
            "text-space",
            "--embedding-fingerprint",
            "text-fingerprint",
            "--aggregation",
            "direct-text",
            "--basis",
            "textual-content",
            "--limit",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    payload: dict[str, Any] = json.loads(result.output)
    assert payload == {
        "ok": True,
        "data": api,
        "meta": {"command": "resource similar"},
    }
    assert api["similarity_basis"] == "textual_content"
    assert api["aggregation"] == "direct_text_v1"
    assert [item["resource_id"] for item in api["results"]] == ["near", "far"]
    assert "visual" not in result.output.lower()
    assert "acoustic" not in result.output.lower()


def test_sqlite_engine_and_cli_cannot_relabel_persisted_aggregation(tmp_path: Path) -> None:
    database = tmp_path / "aggregation-identity.sqlite3"
    with SQLiteCatalog.create(database) as catalog:
        catalog.replace_resource(_batch("query", (1.0, 0.0)))
        catalog.replace_resource(_batch("candidate", (0.9, 0.1)))

    api_catalog = SQLiteCatalog.open_readonly(database)
    engine = MDRackEngine(
        root=tmp_path,
        config=MDRackConfig(),
        storage=_EngineStorage(api_catalog),  # type: ignore[arg-type]
        search_index=api_catalog,  # type: ignore[arg-type]
        read_storage=api_catalog,  # type: ignore[arg-type]
    )
    try:
        api = engine.find_textually_similar_resources(
            "whole-query",
            "text-space",
            aggregation="token_weighted_centroid_v1",
            expected_fingerprint="text-fingerprint",
        ).to_dict()
    finally:
        engine.close()

    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "resource",
            "similar",
            "whole-query",
            "--catalog",
            str(database),
            "--space-id",
            "text-space",
            "--embedding-fingerprint",
            "text-fingerprint",
            "--aggregation",
            "token-weighted-centroid",
        ],
    )

    assert result.exit_code == 0, result.output
    payload: dict[str, Any] = json.loads(result.output)
    assert payload["data"] == api
    assert api["results"] == []
    assert api["aggregation"] is None
    assert api["degraded_reason"] == "textual_similarity_identity_unavailable"


def test_cli_and_engine_resource_preset_search_have_exact_parity(tmp_path: Path) -> None:
    database = tmp_path / "preset.sqlite3"
    with SQLiteCatalog.create(database) as catalog:
        catalog.replace_resource(
            _search_batch(
                "video-a",
                transcript="needle",
                frame_caption="needle needle needle",
            )
        )
        catalog.replace_resource(
            _search_batch(
                "video-b",
                transcript="needle needle needle",
                frame_caption="needle",
            )
        )

    api_catalog = SQLiteCatalog.open_readonly(database)
    engine = MDRackEngine(
        root=tmp_path,
        config=MDRackConfig(),
        storage=_EngineStorage(api_catalog),  # type: ignore[arg-type]
        search_index=api_catalog,  # type: ignore[arg-type]
        read_storage=api_catalog,  # type: ignore[arg-type]
    )
    try:
        api = asyncio.run(
            engine.search_resource_content(
                "needle",
                preset="frames_first",
                mode="text",
                limit=2,
            )
        ).to_dict()
    finally:
        engine.close()

    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "search",
            "needle",
            "--catalog",
            str(database),
            "--target",
            "resource",
            "--preset",
            "frames_first",
            "--mode",
            "text",
            "--limit",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "ok": True,
        "data": api,
        "meta": {"command": "search"},
    }
    assert api["preset"] == "frames_first"
    assert [item["resource_id"] for item in api["results"]] == ["video-a", "video-b"]


@pytest.mark.parametrize(
    ("cli_filter", "metadata_filters"),
    (
        (("--tag", "keep"), MetadataFilters(all=(MetadataFilter("tag", "keep"),))),
        (
            ("--meta", '/tags="keep"'),
            MetadataFilters(all=(MetadataFilter("tag", "keep"),)),
        ),
        (
            ("--meta-any", '/tags="keep"'),
            MetadataFilters(any=(MetadataFilter("tag", "keep"),)),
        ),
        (
            ("--meta-none", '/tags="drop"'),
            MetadataFilters(none=(MetadataFilter("tag", "drop"),)),
        ),
    ),
)
def test_cli_and_engine_preset_facets_filter_before_branch_limits(
    tmp_path: Path,
    cli_filter: tuple[str, str],
    metadata_filters: MetadataFilters,
) -> None:
    database = tmp_path / "preset-facets.sqlite3"
    with SQLiteCatalog.create(database) as catalog:
        catalog.replace_resource(
            _search_batch(
                "video-keep",
                transcript="needle",
                frame_caption="needle",
                tags=("keep", "blue"),
            )
        )
        catalog.replace_resource(
            _search_batch(
                "video-drop",
                transcript="needle needle needle",
                frame_caption="needle needle needle",
                tags=("drop", "red"),
            )
        )

    api_catalog = SQLiteCatalog.open_readonly(database)
    engine = MDRackEngine(
        root=tmp_path,
        config=MDRackConfig(),
        storage=_EngineStorage(api_catalog),  # type: ignore[arg-type]
        search_index=api_catalog,  # type: ignore[arg-type]
        read_storage=api_catalog,  # type: ignore[arg-type]
    )
    try:
        api = asyncio.run(
            engine.search_resource_content(
                "needle",
                preset="balanced",
                mode="text",
                metadata_filters=metadata_filters,
                limit=1,
            )
        ).to_dict()
    finally:
        engine.close()

    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "search",
            "needle",
            "--catalog",
            str(database),
            "--target",
            "resource",
            "--preset",
            "balanced",
            "--mode",
            "text",
            "--limit",
            "1",
            *cli_filter,
        ],
    )

    assert result.exit_code == 0, result.output
    payload: dict[str, Any] = json.loads(result.output)
    assert payload["data"] == api
    api_results = api["results"]
    assert isinstance(api_results, list)
    assert [item["resource_id"] for item in api_results] == ["video-keep"]


@pytest.mark.parametrize(
    ("provider_kind", "reason"),
    (
        ("empty", "embedding_provider_error"),
        ("missing_space", "incompatible_embedding_profile"),
    ),
)
def test_sqlite_engine_and_cli_hybrid_preset_degrade_to_lexical_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_kind: str,
    reason: str,
) -> None:
    database = tmp_path / "preset-degradation.sqlite3"
    with SQLiteCatalog.create(database) as catalog:
        catalog.replace_resource(
            _search_batch(
                "video-lexical",
                transcript="needle",
                frame_caption="needle",
            )
        )

    def provider() -> FakeEmbeddingProvider:
        if provider_kind == "empty":
            return _EmptyQueryProvider(dimensions=2)
        return FakeEmbeddingProvider(dimensions=2)

    api_catalog = SQLiteCatalog.open_readonly(database)
    engine = MDRackEngine(
        root=tmp_path,
        config=MDRackConfig(),
        embedding_provider=provider(),
        storage=_EngineStorage(api_catalog),  # type: ignore[arg-type]
        search_index=api_catalog,  # type: ignore[arg-type]
        read_storage=api_catalog,  # type: ignore[arg-type]
    )
    try:
        api = asyncio.run(
            engine.search_resource_content(
                "needle",
                preset="balanced",
                mode="hybrid",
                limit=1,
            )
        ).to_dict()
    finally:
        engine.close()

    monkeypatch.setattr(
        "mdrack.cli.commands.search.create_embedding_provider",
        lambda *_args, **_kwargs: provider(),
    )
    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "search",
            "needle",
            "--catalog",
            str(database),
            "--target",
            "resource",
            "--preset",
            "balanced",
            "--mode",
            "hybrid",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    payload: dict[str, Any] = json.loads(result.output)
    assert payload["data"] == api
    assert api["degraded"] is True
    assert api["degraded_reason"] == reason
