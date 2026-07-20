"""M2 Memory/SQLite parity for typed metadata filters before branch limits."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from mdrack.application.metadata_filters import (
    MetadataFilter,
    MetadataFilters,
    compile_metadata_filters,
)
from mdrack.application.metadata_projection import MetadataProjection, MetadataProjectionPolicy
from mdrack_core.domain import (
    EmbeddingSpaceRecord,
    LexicalBranch,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchUnitRecord,
    VectorBranch,
    VectorRecord,
)
from mdrack_sqlite import SQLiteCatalog
from tests.core.fakes.memory_store import MemoryCatalog

_POLICY = MetadataProjectionPolicy(
    (
        MetadataProjection("/status", "facet", "status"),
        MetadataProjection("/tags", "facet_many", "tag"),
        MetadataProjection("/blocked", "facet", "blocked"),
    )
)
_FILTERS = MetadataFilters(
    any=(MetadataFilter("status", "ready"),),
    all=(MetadataFilter("tag", "python"),),
    none=(MetadataFilter("blocked", True),),
)


def _batch(
    resource_id: str,
    text: str,
    vector: tuple[float, float],
    metadata: Mapping[str, object],
) -> PreparedResourceBatch:
    projection = _POLICY.project(metadata)  # type: ignore[arg-type]
    representation_id = f"representation-{resource_id}"
    unit_id = f"unit-{resource_id}"
    return PreparedResourceBatch(
        ResourceRecord(
            resource_id,
            "document",
            "text/markdown",
            "fixture",
            Locator("logical", {"id": resource_id}),
            "sha256:shared",
            metadata={"source": metadata},  # type: ignore[arg-type]
        ),
        (RepresentationRecord(representation_id, resource_id, "retrieval_text", "text", text),),
        (
            SearchUnitRecord(
                unit_id,
                resource_id,
                representation_id,
                "text_chunk",
                "text",
                text,
                Locator("whole", {}),
                0,
            ),
        ),
        (EmbeddingSpaceRecord("space", 2, "dot", "space-v1"),),
        (VectorRecord(unit_id, "space", vector),),
        tuple(
            ResourceFacet(
                resource_id,
                facet,
                "source",
                producer_fingerprint=projection.policy_fingerprint,
            )
            for facet in projection.facets
        ),
    )


def _fixtures() -> tuple[PreparedResourceBatch, ...]:
    return (
        _batch(
            "excluded-any",
            "needle needle needle needle",
            (4.0, 0.0),
            {"status": "other", "tags": ["python"], "blocked": False},
        ),
        _batch(
            "excluded-all",
            "needle needle needle",
            (3.0, 0.0),
            {"status": "ready", "tags": ["other"], "blocked": False},
        ),
        _batch(
            "excluded-none",
            "needle needle",
            (2.0, 0.0),
            {"status": "ready", "tags": ["python"], "blocked": True},
        ),
        _batch(
            "included",
            "needle",
            (1.0, 0.0),
            {"status": "ready", "tags": ["python"], "blocked": False},
        ),
    )


def _assert_pre_limit_results(store: object) -> tuple[list[str], list[str], list[str]]:
    scope = compile_metadata_filters(_FILTERS)
    lexical = store.search_lexical(  # type: ignore[attr-defined]
        LexicalBranch("text", "needle", candidate_limit=1),
        scope=scope,
    )
    vector = store.search_vector(  # type: ignore[attr-defined]
        VectorBranch("semantic", "space", (1.0, 0.0), candidate_limit=1),
        scope=scope,
    )
    duplicates = store.find_by_content_hash("sha256:shared", scope=scope)  # type: ignore[attr-defined]
    return (
        [item.unit_id for item in lexical],
        [item.unit_id for item in vector],
        [item.resource_id for item in duplicates],
    )


def test_memory_and_sqlite_have_any_all_none_pre_limit_parity_and_explain_evidence(
    tmp_path: Path,
) -> None:
    memory = MemoryCatalog(enforce_resource_contract=True)
    for batch in _fixtures():
        memory.replace_resource(batch)
    memory_results = _assert_pre_limit_results(memory)

    database = tmp_path / "metadata-filter-parity.sqlite3"
    with SQLiteCatalog.create(database) as sqlite:
        schema_before = tuple(
            sqlite.connection.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
            ).fetchall()
        )
        for batch in _fixtures():
            sqlite.replace_resource(batch)

        traced: list[str] = []
        sqlite.connection.set_trace_callback(traced.append)
        sqlite_results = _assert_pre_limit_results(sqlite)
        sqlite.connection.set_trace_callback(None)

        lexical_statement = next(
            statement
            for statement in traced
            if "FROM core_search_units_fts" in statement and "bm25" in statement
        )
        vector_statement = next(
            statement
            for statement in traced
            if "FROM core_unit_embeddings e" in statement and "e.space_id" in statement
        )
        explain_rows = sqlite.connection.execute(
            "EXPLAIN QUERY PLAN " + lexical_statement
        ).fetchall()
        explain = " ".join(str(row[3]) for row in explain_rows)
        facet_indexes = {
            str(row[1])
            for table in ("core_facets", "core_resource_facets")
            for row in sqlite.connection.execute(f"PRAGMA index_list({table})").fetchall()
        }
        schema_after = tuple(
            sqlite.connection.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
            ).fetchall()
        )

    expected = (["unit-included"], ["unit-included"], ["included"])
    assert memory_results == sqlite_results == expected
    assert "EXISTS" in lexical_statement
    assert lexical_statement.index("EXISTS") < lexical_statement.rindex("LIMIT 1")
    assert "metadata_json" not in lexical_statement
    assert "json_extract" not in lexical_statement
    assert "EXISTS" in vector_statement
    assert "metadata_json" not in vector_statement
    assert "CORRELATED" in explain
    assert "rf" in explain
    assert "INDEX" in explain
    assert {
        "idx_core_facets_lookup",
        "idx_core_resource_facets_facet",
        "idx_core_resource_facets_resource",
    } <= facet_indexes
    assert schema_after == schema_before
