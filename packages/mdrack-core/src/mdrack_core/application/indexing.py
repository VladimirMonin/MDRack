"""Provider- and persistence-neutral prepared-resource indexing."""

from __future__ import annotations

import logging
import time

from ..domain.batches import PreparedResourceBatch
from ..domain.common import require_non_empty
from ..domain.errors import CatalogExecutionError, CoreError, ErrorCategory
from ..domain.publication import ExpectedPublication, PublicationResult
from ..observability import LifecycleStatus, SafeEvent, emit_event
from ..ports.catalog import ResourceWritePort
from ..ports.publication import PublicationPort
from .validation import _BatchValidator, validate_prepared_batch


class CoreIndexingService:
    """Validate prepared graphs using one shared, I/O-free validator."""

    def __init__(self, write_port: ResourceWritePort, *, logger: logging.Logger | None = None) -> None:
        self._write_port = write_port
        self._logger = logger or logging.getLogger(__name__)

    def index(self, batch: PreparedResourceBatch) -> None:
        self._index(batch, None)

    def index_if_current(self, batch: PreparedResourceBatch, expected: ExpectedPublication) -> PublicationResult:
        """Require publication capability before attempting a conditional write."""
        if not isinstance(self._write_port, PublicationPort):
            raise TypeError("publication capability required")
        if not isinstance(expected, ExpectedPublication):
            raise TypeError("expected must be ExpectedPublication")
        result = self._index(batch, expected)
        assert isinstance(result, PublicationResult)
        return result

    def _index(self, batch: PreparedResourceBatch, expected: ExpectedPublication | None) -> PublicationResult | None:
        started = time.perf_counter()
        counts = self._counts(batch)
        self._emit("core.index.started", status=LifecycleStatus.STARTED, **counts)
        validation_started = time.perf_counter()
        try:
            validate_prepared_batch(batch)
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
        result = None
        try:
            if expected is None:
                self._write_port.replace_resource(batch)
            else:
                assert isinstance(self._write_port, PublicationPort)
                result = self._write_port.compare_and_publish(batch, expected)
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
        publication_fields: dict[str, object] = (
            {}
            if expected is None
            else {
                "publication_applied": None if result is None else result.applied,
            }
        )
        self._emit(
            "core.index.completed",
            status=LifecycleStatus.COMPLETED,
            elapsed_ms=self._elapsed_ms(started),
            **{"storage_ms": self._elapsed_ms(catalog_started)},
            **counts,
            **publication_fields,
        )
        return result

    def delete(self, resource_id: str) -> None:
        try:
            require_non_empty(resource_id, "resource_id")
        except ValueError:
            raise CoreError(ErrorCategory.VALIDATION) from None
        try:
            self._write_port.delete_resource(resource_id)
        except CatalogExecutionError:
            raise
        except TimeoutError:
            raise CatalogExecutionError(ErrorCategory.ADAPTER_TIMEOUT) from None
        except Exception:
            raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR) from None

    _validate_batch = staticmethod(validate_prepared_batch)

    @classmethod
    def _counts(cls, batch: object) -> dict[str, int]:
        names = (
            "representation_count",
            "unit_count",
            "text_unit_count",
            "vector_unit_count",
            "vector_count",
            "space_count",
            "facet_count",
            "input_bytes",
            "representation_token_count_total",
            "representation_token_count_max",
            "unit_token_count_total",
            "unit_token_count_max",
        )
        if not isinstance(batch, PreparedResourceBatch):
            return dict.fromkeys(names, 0)
        representation_tokens = [item.token_count or 0 for item in batch.representations]
        unit_tokens = [item.token_count or 0 for item in batch.units]
        text_values: list[str] = []
        for representation in batch.representations:
            if isinstance(representation.text, str):
                text_values.append(representation.text)
        for unit in batch.units:
            if isinstance(unit.text, str):
                text_values.append(unit.text)
        return dict(
            zip(
                names,
                (
                    len(batch.representations),
                    len(batch.units),
                    sum(_BatchValidator._has_text(u) for u in batch.units),
                    len({v.unit_id for v in batch.vectors}),
                    len(batch.vectors),
                    len(batch.spaces),
                    len(batch.facets),
                    sum(len(text.encode("utf-8", errors="replace")) for text in text_values),
                    sum(representation_tokens),
                    max(representation_tokens, default=0),
                    sum(unit_tokens),
                    max(unit_tokens, default=0),
                ),
                strict=True,
            )
        )

    def _emit_failure(self, category: ErrorCategory, started: float, **counts: int) -> None:
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
