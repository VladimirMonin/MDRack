from __future__ import annotations

from mdrack_core.domain import (
    PreparedResourceBatch,
    ResourceRecord,
    SearchScope,
    SearchUnitRecord,
    VectorRecord,
)


class MemoryCatalog:
    """Deterministic test-only catalog implementing the frozen catalog ports."""

    def __init__(self) -> None:
        self._batches: dict[str, PreparedResourceBatch] = {}
        self.replace_calls: list[PreparedResourceBatch] = []
        self.delete_calls: list[str] = []
        self._replace_failure: BaseException | None = None
        self._delete_failure: BaseException | None = None

    def inject_replace_failure(self, error: BaseException) -> None:
        self._replace_failure = error

    def inject_delete_failure(self, error: BaseException) -> None:
        self._delete_failure = error

    def replace_resource(self, batch: PreparedResourceBatch) -> None:
        self.replace_calls.append(batch)
        candidate = dict(self._batches)
        candidate[batch.resource.resource_id] = batch
        if self._replace_failure is not None:
            error = self._replace_failure
            self._replace_failure = None
            raise error
        self._batches = candidate

    def delete_resource(self, resource_id: str) -> None:
        self.delete_calls.append(resource_id)
        candidate = dict(self._batches)
        candidate.pop(resource_id, None)
        if self._delete_failure is not None:
            error = self._delete_failure
            self._delete_failure = None
            raise error
        self._batches = candidate

    def read_resource(self, resource_id: str) -> ResourceRecord | None:
        batch = self._batches.get(resource_id)
        return None if batch is None else batch.resource

    def read_unit(self, unit_id: str) -> SearchUnitRecord | None:
        for batch in self._ordered_batches():
            for unit in batch.units:
                if unit.unit_id == unit_id:
                    return unit
        return None

    def read_vector(self, unit_id: str, space_id: str) -> VectorRecord | None:
        for batch in self._ordered_batches():
            for vector in batch.vectors:
                if vector.unit_id == unit_id and vector.space_id == space_id:
                    return vector
        return None

    def find_by_content_hash(
        self,
        content_hash: str,
        *,
        scope: SearchScope,
    ) -> list[ResourceRecord]:
        return [
            batch.resource
            for batch in self._ordered_batches()
            if batch.resource.content_hash == content_hash and self._matches_scope(batch, scope)
        ]

    def batch(self, resource_id: str) -> PreparedResourceBatch | None:
        """Expose one immutable graph for adapter-contract assertions only."""
        return self._batches.get(resource_id)

    def _ordered_batches(self) -> tuple[PreparedResourceBatch, ...]:
        return tuple(self._batches[key] for key in sorted(self._batches))

    @staticmethod
    def _matches_scope(batch: PreparedResourceBatch, scope: SearchScope) -> bool:
        resource = batch.resource
        if scope.resource_kinds and resource.resource_kind not in scope.resource_kinds:
            return False
        if scope.media_types and resource.media_type not in scope.media_types:
            return False
        if scope.source_namespaces and resource.source_namespace not in scope.source_namespaces:
            return False

        representation_kinds = {item.representation_kind for item in batch.representations}
        if scope.representation_kinds and representation_kinds.isdisjoint(scope.representation_kinds):
            return False
        modalities = {item.modality for item in batch.representations}
        modalities.update(item.modality for item in batch.units)
        if scope.modalities and modalities.isdisjoint(scope.modalities):
            return False
        unit_kinds = {item.unit_kind for item in batch.units}
        if scope.unit_kinds and unit_kinds.isdisjoint(scope.unit_kinds):
            return False

        facets = {item.facet for item in batch.facets}
        if scope.facets_any and facets.isdisjoint(scope.facets_any):
            return False
        if scope.facets_all and not set(scope.facets_all).issubset(facets):
            return False
        if scope.facets_none and not facets.isdisjoint(scope.facets_none):
            return False
        return True
