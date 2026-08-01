"""Canonical one-store resource similarity and scoped search contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from click.testing import CliRunner

from mdrack.application.resources import ResourceQueryScope, ResourceQueryService
from mdrack.cli import main
from mdrack.config.models import MDRackConfig
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
    unit_kind: str = "whole_resource",
    text: str = "needle",
    tags: tuple[str, ...] = (),
) -> PreparedResourceBatch:
    representation_id = f"representation-{resource_id}"
    unit_id = f"{unit_kind}-{resource_id}"
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
                "timed_passage" if unit_kind == "whole_resource" else "frame_caption",
                "text",
                text,
            ),
        ),
        (
            SearchUnitRecord(
                unit_id,
                resource_id,
                representation_id,
                unit_kind,
                "text",
                text,
                Locator("whole_media" if unit_kind == "whole_resource" else "video_frame", {}),
                0,
                metadata={
                    "similarity_basis": "transcript_text" if unit_kind == "whole_resource" else "frame_caption_text",
                    "aggregation": "direct_text_v1",
                },
            ),
        ),
        (EmbeddingSpaceRecord("text-space", 2, "cosine", "text-fingerprint"),),
        (VectorRecord(unit_id, "text-space", vector),),
        facets=tuple(
            ResourceFacet(resource_id, Facet("tag", f"s:{tag}"), "fixture") for tag in tags
        ),
    )


def _catalog_path(root: Path) -> Path:
    return root / ".mdrack" / "catalog.sqlite3"


def _write_catalog(root: Path, *batches: PreparedResourceBatch) -> None:
    path = _catalog_path(root)
    path.parent.mkdir()
    with SQLiteCatalog.create_v2(path) as catalog:
        for batch in batches:
            catalog.replace_resource(batch)


def _json(result: object) -> dict[str, Any]:
    output = getattr(result, "output")
    return json.loads(output)


def test_resources_similar_cli_matches_explicit_catalog_request(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        _batch("query", (1.0, 0.0)),
        _batch("near", (0.9, 0.1)),
        _batch("far", (0.1, 0.9)),
    )
    with SQLiteCatalog.open_readonly(_catalog_path(tmp_path)) as catalog:
        expected = ResourceQueryService(catalog).find_similar(
            "whole_resource-query",
            "text-space",
            scope=ResourceQueryScope(),
            limit=2,
        ).to_dict()

    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "resources",
            "similar",
            "whole_resource-query",
            "--space-id",
            "text-space",
            "--limit",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert payload["data"] == expected
    expected_results = cast(list[dict[str, Any]], expected["results"])
    assert [item["resource_id"] for item in expected_results] == ["near", "far"]


def test_selected_resource_similarity_rejects_frame_only_anchor(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        _batch("frame-only", (1.0, 0.0), unit_kind="frame"),
        _batch("whole-candidate", (0.9, 0.1)),
    )
    engine = MDRackEngine(root=tmp_path, config=MDRackConfig())
    try:
        result = engine.find_similar_resource("frame-only", scope="all", limit=2)
    finally:
        engine.close()

    assert result.results == ()
    assert result.degraded is True
    assert result.degraded_reason == "textual_similarity_identity_unavailable"


def test_resources_search_applies_facet_filter_before_limit(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        _batch("keep", (1.0, 0.0), text="needle", tags=("keep",)),
        _batch("drop", (0.9, 0.1), text="needle needle needle", tags=("drop",)),
    )

    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "resources",
            "search",
            "needle",
            "--target",
            "resource",
            "--facet-all",
            "tag=s:keep",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    data = _json(result)["data"]
    assert data["total_count"] == 1
    assert [item["resource_id"] for item in data["results"]] == ["keep"]
