"""Generic catalog/search contract shared by memory and SQLite adapters."""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Protocol

import pytest
from fakes.memory_store import MemoryCatalog

from mdrack.adapters.sqlite.resource_store import SQLiteResourceStore
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_candidate_migrations, get_migrations_dir
from mdrack_core.domain import (
    BranchExecutionError,
    CatalogExecutionError,
    EmbeddingSpaceRecord,
    ErrorCategory,
    Facet,
    LexicalBranch,
    Locator,
    PreparedResourceBatch,
    RankedCandidate,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchScope,
    SearchUnitRecord,
    VectorBranch,
    VectorRecord,
)


class ContractStore(Protocol):
    def replace_resource(self, batch: PreparedResourceBatch) -> None: ...

    def delete_resource(self, resource_id: str) -> None: ...

    def read_resource(self, resource_id: str) -> ResourceRecord | None: ...

    def read_unit(self, unit_id: str) -> SearchUnitRecord | None: ...

    def read_vector(self, unit_id: str, space_id: str) -> VectorRecord | None: ...

    def find_by_content_hash(
        self, content_hash: str, *, scope: SearchScope
    ) -> list[ResourceRecord]: ...

    def search_lexical(
        self, branch: LexicalBranch, *, scope: SearchScope
    ) -> list[RankedCandidate]: ...

    def search_vector(
        self, branch: VectorBranch, *, scope: SearchScope
    ) -> list[RankedCandidate]: ...


def _batch(resource_id: str, namespace: str, text: str, vector: tuple[float, ...]) -> PreparedResourceBatch:
    representation_id = f"representation-{resource_id}"
    unit_id = f"unit-{resource_id}"
    return PreparedResourceBatch(
        ResourceRecord(
            resource_id,
            "document",
            "text/plain",
            namespace,
            Locator("logical", {"id": resource_id}),
            "sha256:shared",
            metadata={"stable": True},
        ),
        [RepresentationRecord(
            representation_id,
            resource_id,
            "retrieval_text",
            "text",
            text,
        )],
        [SearchUnitRecord(
            unit_id,
            resource_id,
            representation_id,
            "text_chunk",
            "text",
            text,
            Locator("whole", {}),
            0,
        )],
        [EmbeddingSpaceRecord("space", 2, "dot", "fingerprint")],
        [VectorRecord(unit_id, "space", vector)],
        [ResourceFacet(resource_id, Facet("tag", "included"), "user", confidence=-0.0)],
    )


def _multi_representation_batch(resource_id: str = "multi") -> PreparedResourceBatch:
    resource = ResourceRecord(
        resource_id,
        "document",
        "text/plain",
        "vault",
        Locator("logical", {"id": resource_id}),
        "sha256:multi",
    )
    representations = (
        RepresentationRecord("rep-a", resource_id, "kind-a", "text", "needle"),
        RepresentationRecord("rep-b", resource_id, "kind-b", "text", "needle"),
    )
    units = (
        SearchUnitRecord(
            "unit-a", resource_id, "rep-a", "chunk-a", "text", "needle", Locator("whole", {}), 0
        ),
        SearchUnitRecord(
            "unit-b", resource_id, "rep-b", "chunk-b", "text", "needle", Locator("whole", {}), 0
        ),
    )
    return PreparedResourceBatch(
        resource,
        representations,
        units,
        (EmbeddingSpaceRecord("space", 2, "dot", "fingerprint"),),
        (
            VectorRecord("unit-a", "space", (1.0, 0.0)),
            VectorRecord("unit-b", "space", (1.0, 0.0)),
        ),
    )


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_generic_catalog_and_search_contract(
    backend: str,
    tmp_path: Path,
) -> None:
    connection: sqlite3.Connection | None = None
    if backend == "memory":
        store: ContractStore = MemoryCatalog(enforce_resource_contract=True)
    else:
        connection = get_connection(tmp_path / "generic-contract.db")
        apply_candidate_migrations(connection, get_migrations_dir())
        store = SQLiteResourceStore(connection)
    try:
        excluded = _batch("excluded", "other", "needle needle", (10.0, 0.0))
        included = _batch("included", "vault", "needle", (1.0, -0.0))
        store.replace_resource(excluded)
        store.replace_resource(included)

        assert store.read_resource("included") == included.resource
        assert store.read_unit("unit-included") == included.units[0]
        actual_vector = store.read_vector("unit-included", "space")
        assert actual_vector is not None
        assert actual_vector.vector == (1.0, -0.0)
        assert math.copysign(1.0, actual_vector.vector[1]) == -1.0
        scope = SearchScope(
            source_namespaces=["vault"],
            facets_all=[Facet("tag", "included")],
        )
        assert store.find_by_content_hash("sha256:shared", scope=scope) == [included.resource]
        lexical = store.search_lexical(
            LexicalBranch("lexical", "needle", candidate_limit=1),
            scope=scope,
        )
        vector = store.search_vector(
            VectorBranch(
                "vector",
                "space",
                (1.0, 0.0),
                candidate_limit=1,
                expected_fingerprint="fingerprint",
            ),
            scope=scope,
        )
        assert [item.unit_id for item in lexical] == ["unit-included"]  # type: ignore[attr-defined]
        assert [item.unit_id for item in vector] == ["unit-included"]  # type: ignore[attr-defined]
        with pytest.raises(BranchExecutionError) as mismatch:
            store.search_vector(
                VectorBranch(
                    "vector-mismatch",
                    "space",
                    (1.0, 0.0),
                    expected_fingerprint="PRIVATE_FINGERPRINT_SENTINEL",
                ),
                scope=scope,
            )
        assert mismatch.value.category is ErrorCategory.INCOMPATIBLE_VECTOR_SPACE
        assert str(mismatch.value) == "incompatible_vector_space"

        multi = _multi_representation_batch()
        store.replace_resource(multi)
        unit_scope = SearchScope(representation_kinds=("kind-a",))
        assert [
            item.unit_id  # type: ignore[attr-defined]
            for item in store.search_lexical(
                LexicalBranch("multi-lexical", "needle", candidate_limit=10),
                scope=unit_scope,
            )
        ] == ["unit-a"]
        assert [
            item.unit_id  # type: ignore[attr-defined]
            for item in store.search_vector(
                VectorBranch("multi-vector", "space", (1.0, 0.0), candidate_limit=10),
                scope=unit_scope,
            )
        ] == ["unit-a"]
        assert store.find_by_content_hash("sha256:multi", scope=unit_scope) == [multi.resource]
        multi_scope = SearchScope(representation_kinds=("kind-a", "kind-b"))
        assert [
            item.unit_id
            for item in store.search_lexical(
                LexicalBranch("stable-lexical", "needle", candidate_limit=10),
                scope=multi_scope,
            )
        ] == ["unit-a", "unit-b"]
        assert [
            item.unit_id
            for item in store.search_vector(
                VectorBranch("stable-vector", "space", (1.0, 0.0), candidate_limit=10),
                scope=multi_scope,
            )
        ] == ["unit-a", "unit-b"]

        replacement = _batch("included", "vault", "replacement needle", (2.0, 0.0))
        store.replace_resource(replacement)
        assert store.read_unit("unit-included") == replacement.units[0]

        source_collision = _batch("collision", "vault", "needle", (1.0, 0.0))
        object.__setattr__(source_collision.resource, "locator", replacement.resource.locator)
        with pytest.raises(CatalogExecutionError) as collision:
            store.replace_resource(source_collision)
        assert str(collision.value) == "catalog_error"

        identity_collision = _batch("included", "vault", "needle", (1.0, 0.0))
        object.__setattr__(identity_collision.resource, "locator", Locator("logical", {"id": "other"}))
        with pytest.raises(CatalogExecutionError) as identity_error:
            store.replace_resource(identity_collision)
        assert str(identity_error.value) == "catalog_error"

        incompatible = _batch("incompatible", "vault", "needle", (1.0, 0.0))
        object.__setattr__(incompatible.spaces[0], "fingerprint", "different-fingerprint")
        with pytest.raises(CatalogExecutionError) as space_error:
            store.replace_resource(incompatible)
        assert str(space_error.value) == "catalog_error"

        store.delete_resource("included")
        store.delete_resource("included")
        assert store.read_resource("included") is None
        assert store.read_unit("unit-included") is None
        assert store.read_vector("unit-included", "space") is None
    finally:
        if connection is not None:
            connection.close()
