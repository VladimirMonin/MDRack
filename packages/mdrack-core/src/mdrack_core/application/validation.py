"""Provider- and persistence-neutral prepared-resource indexing."""

from __future__ import annotations

import math
from collections.abc import Hashable, Iterable

from ..domain.batches import PreparedResourceBatch
from ..domain.common import freeze_json_mapping, require_utf8_encodable
from ..domain.resources import Locator, RepresentationRecord, SearchUnitRecord


class _BatchValidator:
    @classmethod
    def _validate_batch(cls, batch: object) -> None:
        if not isinstance(batch, PreparedResourceBatch):
            raise TypeError("batch must be a PreparedResourceBatch")
        if not batch.representations:
            raise ValueError("a resource graph must contain a representation")
        if not batch.units:
            raise ValueError("a resource graph must contain a search unit")

        cls._validate_persisted_strings(batch)
        resource_id = batch.resource.resource_id
        cls._require_unique(
            (item.representation_id for item in batch.representations),
            "representation_id",
        )
        cls._require_unique((item.unit_id for item in batch.units), "unit_id")
        cls._require_unique((item.space_id for item in batch.spaces), "space_id")
        cls._require_unique(
            ((item.unit_id, item.space_id) for item in batch.vectors),
            "unit_id/space_id vector",
        )
        cls._require_unique(
            (
                (
                    item.resource_id,
                    item.facet.namespace,
                    item.facet.value,
                    item.origin,
                    item.producer_fingerprint,
                )
                for item in batch.facets
            ),
            "resource facet assignment",
        )

        representations = {item.representation_id: item for item in batch.representations}
        units = {item.unit_id: item for item in batch.units}
        spaces = {item.space_id: item for item in batch.spaces}
        vector_units: set[str] = set()

        for representation in batch.representations:
            if representation.resource_id != resource_id:
                raise ValueError("representation belongs to another resource")

        for unit in batch.units:
            if unit.resource_id != resource_id:
                raise ValueError("unit belongs to another resource")
            owner_representation = representations.get(unit.representation_id)
            if owner_representation is None:
                raise ValueError("unit references an unknown representation")
            if unit.modality != owner_representation.modality:
                raise ValueError("unit modality differs from its representation")

        for vector in batch.vectors:
            if vector.unit_id not in units:
                raise ValueError("vector references an unknown unit")
            space = spaces.get(vector.space_id)
            if space is None:
                raise ValueError("vector references an unknown embedding space")
            if len(vector.vector) != space.dimensions:
                raise ValueError("vector dimensions differ from its embedding space")
            if any(
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))
                for value in vector.vector
            ):
                raise ValueError("vector values must be finite numbers")
            if space.metric == "cosine" and math.hypot(*vector.vector) == 0.0:
                raise ValueError("cosine vector norm must be non-zero")
            vector_units.add(vector.unit_id)

        represented = {unit.representation_id for unit in batch.units}
        if represented != set(representations):
            raise ValueError("every representation must own at least one unit")
        for unit in batch.units:
            if not cls._has_text(unit) and unit.unit_id not in vector_units:
                raise ValueError("every unit must contain text or a vector")

        for facet in batch.facets:
            if facet.resource_id != resource_id:
                raise ValueError("facet belongs to another resource")

    @staticmethod
    def _has_text(record: RepresentationRecord | SearchUnitRecord) -> bool:
        return isinstance(record.text, str) and bool(record.text.strip())

    @classmethod
    def _validate_persisted_strings(cls, batch: PreparedResourceBatch) -> None:
        resource = batch.resource
        cls._require_strings(
            resource.resource_id,
            resource.resource_kind,
            resource.media_type,
            resource.source_namespace,
            resource.content_hash,
            resource.title,
        )
        cls._require_locator(resource.locator)
        freeze_json_mapping(resource.metadata, "resource.metadata")

        for representation in batch.representations:
            cls._require_strings(
                representation.representation_id,
                representation.resource_id,
                representation.representation_kind,
                representation.modality,
                representation.text,
                representation.language,
                representation.producer_fingerprint,
                representation.token_count_kind,
            )
            freeze_json_mapping(representation.metadata, "representation.metadata")

        for unit in batch.units:
            cls._require_strings(
                unit.unit_id,
                unit.resource_id,
                unit.representation_id,
                unit.unit_kind,
                unit.modality,
                unit.text,
                unit.token_count_kind,
            )
            cls._require_locator(unit.evidence_locator)
            freeze_json_mapping(unit.metadata, "unit.metadata")

        for space in batch.spaces:
            cls._require_strings(space.space_id, space.metric, space.fingerprint)
            freeze_json_mapping(space.metadata, "space.metadata")

        for vector in batch.vectors:
            cls._require_strings(vector.unit_id, vector.space_id)

        for assignment in batch.facets:
            cls._require_strings(
                assignment.resource_id,
                assignment.facet.namespace,
                assignment.facet.value,
                assignment.origin,
                assignment.producer_fingerprint,
            )

    @staticmethod
    def _require_strings(*values: str | None) -> None:
        for value in values:
            if value is not None:
                require_utf8_encodable(value, "persisted string")

    @staticmethod
    def _require_locator(locator: Locator) -> None:
        require_utf8_encodable(locator.kind, "locator.kind")
        freeze_json_mapping(locator.payload, "locator.payload")

    @staticmethod
    def _require_unique(values: Iterable[Hashable], field_name: str) -> None:
        seen: set[Hashable] = set()
        for value in values:
            if value in seen:
                raise ValueError(f"{field_name} values must be unique")
            seen.add(value)


def validate_prepared_batch(batch: object) -> None:
    """Validate one complete graph without provider or storage I/O."""
    _BatchValidator._validate_batch(batch)
