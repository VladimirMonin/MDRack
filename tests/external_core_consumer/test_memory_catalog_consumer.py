from __future__ import annotations

from mdrack_core.application import CoreIndexingService, RetrievalService
from mdrack_core.domain import (
    LexicalBranch,
    Locator,
    PreparedResourceBatch,
    RankedCandidate,
    RepresentationRecord,
    ResourceRecord,
    SearchRequest,
    SearchScope,
    SearchUnitRecord,
    VectorBranch,
    VectorRecord,
)


class MemoryCatalog:
    """Package-neutral external consumer implementation of the frozen core ports."""

    def __init__(self) -> None:
        self._batches: dict[str, PreparedResourceBatch] = {}

    def replace_resource(self, batch: PreparedResourceBatch) -> None:
        self._batches = {**self._batches, batch.resource.resource_id: batch}

    def delete_resource(self, resource_id: str) -> None:
        self._batches = {
            key: batch for key, batch in self._batches.items() if key != resource_id
        }

    def read_resource(self, resource_id: str) -> ResourceRecord | None:
        batch = self._batches.get(resource_id)
        return None if batch is None else batch.resource

    def read_unit(self, unit_id: str) -> SearchUnitRecord | None:
        return next(
            (
                unit
                for batch in self._batches.values()
                for unit in batch.units
                if unit.unit_id == unit_id
            ),
            None,
        )

    def read_vector(self, unit_id: str, space_id: str) -> VectorRecord | None:
        return next(
            (
                vector
                for batch in self._batches.values()
                for vector in batch.vectors
                if vector.unit_id == unit_id and vector.space_id == space_id
            ),
            None,
        )

    def find_by_content_hash(
        self,
        content_hash: str,
        *,
        scope: SearchScope,
    ) -> list[ResourceRecord]:
        del scope
        return sorted(
            (
                batch.resource
                for batch in self._batches.values()
                if batch.resource.content_hash == content_hash
            ),
            key=lambda item: item.resource_id,
        )

    def search_lexical(
        self,
        branch: LexicalBranch,
        *,
        scope: SearchScope,
    ) -> list[RankedCandidate]:
        del scope
        units = sorted(
            (
                unit
                for batch in self._batches.values()
                for unit in batch.units
                if unit.text is not None and branch.query.casefold() in unit.text.casefold()
            ),
            key=lambda item: item.unit_id,
        )
        return [
            RankedCandidate(
                unit.unit_id,
                unit.resource_id,
                unit.representation_id,
                rank,
                1.0,
                branch.branch_id,
                unit.evidence_locator,
            )
            for rank, unit in enumerate(units[: branch.candidate_limit], start=1)
        ]

    def search_vector(
        self,
        branch: VectorBranch,
        *,
        scope: SearchScope,
    ) -> list[RankedCandidate]:
        del branch, scope
        return []


def test_external_memory_catalog_indexes_and_retrieves_without_mdrack_imports() -> None:
    catalog = MemoryCatalog()
    batch = PreparedResourceBatch(
        resource=ResourceRecord(
            "resource-external",
            "document",
            "text/plain",
            "external",
            Locator("opaque", {"external_id": "one"}),
        ),
        representations=(
            RepresentationRecord(
                "representation-external",
                "resource-external",
                "retrieval_text",
                "text",
                "portable alpha content",
            ),
        ),
        units=(
            SearchUnitRecord(
                "unit-external",
                "resource-external",
                "representation-external",
                "text_chunk",
                "text",
                "portable alpha content",
                Locator("opaque_evidence", {"ordinal": 0}),
                0,
            ),
        ),
    )

    CoreIndexingService(catalog).index(batch)
    result = RetrievalService(catalog).search(
        SearchRequest(
            lexical_branches=(LexicalBranch("lexical", "alpha"),),
            vector_branches=(),
            scope=SearchScope(),
            target="unit",
            limit=5,
        )
    )

    assert [item.logical_id for item in result.items] == ["unit-external"]
    assert catalog.read_resource("resource-external") == batch.resource
