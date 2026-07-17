"""Provider- and persistence-neutral prepared-resource indexing."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Hashable, Iterable

from ..domain.batches import PreparedResourceBatch
from ..domain.common import freeze_json_mapping, require_non_empty, require_utf8_encodable
from ..domain.errors import CatalogExecutionError, CoreError, ErrorCategory
from ..domain.resources import Locator, RepresentationRecord, SearchUnitRecord
from ..observability import LifecycleStatus, SafeEvent, emit_event
from ..ports.catalog import ResourceWritePort


class CoreIndexingService:
    """Validate one caller-prepared graph before one atomic catalog replacement."""

    def __init__(
        self,
        write_port: ResourceWritePort,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._write_port = write_port
        self._logger = logger or logging.getLogger(__name__)

    def index(self, batch: PreparedResourceBatch) -> None:
        """Validate and atomically replace one complete resource graph."""
        started = time.perf_counter()
        counts = self._counts(batch)
        self._emit(
            "core.index.started",
            status=LifecycleStatus.STARTED,
            **counts,
        )

        validation_started = time.perf_counter()
        try:
            self._validate_batch(batch)
        except (TypeError, ValueError):
            error = CoreError(ErrorCategory.VALIDATION)
            self._emit_failure(error.category, started, **counts)
            raise error from None

        self._emit(
            "core.index.validated",
            status=LifecycleStatus.VALIDATED,
            validation_ms=self._elapsed_ms(validation_started),
            **counts,
        )

        catalog_started = time.perf_counter()
        try:
            self._write_port.replace_resource(batch)
        except CatalogExecutionError as error:
            self._emit_failure(error.category, started, **counts)
            raise
        except TimeoutError:
            timeout_error = CatalogExecutionError(ErrorCategory.ADAPTER_TIMEOUT)
            self._emit_failure(timeout_error.category, started, **counts)
            raise timeout_error from None
        except Exception:
            catalog_error = CatalogExecutionError(ErrorCategory.CATALOG_ERROR)
            self._emit_failure(catalog_error.category, started, **counts)
            raise catalog_error from None

        self._emit(
            "core.index.completed",
            status=LifecycleStatus.COMPLETED,
            elapsed_ms=self._elapsed_ms(started),
            **counts,
            **{"storage_ms": self._elapsed_ms(catalog_started)},
        )

    def delete(self, resource_id: str) -> None:
        """Idempotently delete one graph by its caller-owned logical resource ID."""
        try:
            require_non_empty(resource_id, "resource_id")
        except ValueError:
            raise CoreError(ErrorCategory.VALIDATION)
        try:
            self._write_port.delete_resource(resource_id)
        except CatalogExecutionError:
            raise
        except TimeoutError:
            raise CatalogExecutionError(ErrorCategory.ADAPTER_TIMEOUT) from None
        except Exception:
            raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR) from None

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
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in vector.vector
            ):
                raise ValueError("vector values must be finite numbers")
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

    @classmethod
    def _counts(cls, batch: object) -> dict[str, int]:
        if not isinstance(batch, PreparedResourceBatch):
            return {
                "representation_count": 0,
                "unit_count": 0,
                "text_unit_count": 0,
                "vector_unit_count": 0,
                "vector_count": 0,
                "space_count": 0,
                "facet_count": 0,
                "input_bytes": 0,
                "representation_token_count_total": 0,
                "representation_token_count_max": 0,
                "unit_token_count_total": 0,
                "unit_token_count_max": 0,
            }
        representation_tokens = [item.token_count or 0 for item in batch.representations]
        unit_tokens = [item.token_count or 0 for item in batch.units]
        vector_units = {item.unit_id for item in batch.vectors}
        text_values: list[str] = []
        for representation in batch.representations:
            if isinstance(representation.text, str):
                text_values.append(representation.text)
        for unit in batch.units:
            if isinstance(unit.text, str):
                text_values.append(unit.text)
        return {
            "representation_count": len(batch.representations),
            "unit_count": len(batch.units),
            "text_unit_count": sum(cls._has_text(item) for item in batch.units),
            "vector_unit_count": len(vector_units),
            "vector_count": len(batch.vectors),
            "space_count": len(batch.spaces),
            "facet_count": len(batch.facets),
            "input_bytes": sum(
                len(value.encode("utf-8", errors="replace")) for value in text_values
            ),
            "representation_token_count_total": sum(representation_tokens),
            "representation_token_count_max": max(representation_tokens, default=0),
            "unit_token_count_total": sum(unit_tokens),
            "unit_token_count_max": max(unit_tokens, default=0),
        }

    def _emit_failure(
        self,
        category: ErrorCategory,
        started: float,
        **counts: int,
    ) -> None:
        self._emit(
            "core.index.failed",
            status=LifecycleStatus.FAILED,
            category=category,
            elapsed_ms=self._elapsed_ms(started),
            **counts,
        )

    def _emit(self, name: str, **fields: object) -> None:
        emit_event(self._logger, SafeEvent(name=name, fields=fields))

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return max(0.0, (time.perf_counter() - started) * 1000.0)
