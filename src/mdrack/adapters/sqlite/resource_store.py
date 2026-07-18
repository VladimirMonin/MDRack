"""SQLite resource catalog and candidate-search adapter for the frozen core ports."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from mdrack.storage.sqlite.fts import plain_query_fallback
from mdrack_core.domain import (
    BranchExecutionError,
    CatalogExecutionError,
    EmbeddingSpaceRecord,
    ErrorCategory,
    LexicalBranch,
    Locator,
    PreparedResourceBatch,
    RankedCandidate,
    ResourceRecord,
    SearchScope,
    SearchUnitRecord,
    VectorBranch,
    VectorRecord,
)
from mdrack_core.domain.common import (
    JSONValue,
    canonical_json,
    freeze_json_mapping,
    require_non_empty,
    require_optional_non_empty,
    require_utf8_encodable,
)

FailureHook = Callable[[str], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode_mapping(value: object, field_name: str) -> str:
    frozen = freeze_json_mapping(value, field_name)
    encoded = canonical_json(frozen)
    str.encode(encoded, "utf-8", "strict")
    return encoded


def _decode_mapping(value: object, field_name: str) -> Mapping[str, JSONValue]:
    text = require_utf8_encodable(value, field_name)
    decoded = json.loads(text)
    frozen = freeze_json_mapping(decoded, field_name)
    if canonical_json(frozen) != text:
        raise ValueError(f"{field_name} is not canonical JSON")
    return frozen


def _locator_parts(locator: Locator, field_name: str) -> tuple[str, str, str]:
    if not isinstance(locator, Locator):
        raise ValueError(f"{field_name} must be a Locator")
    kind = require_non_empty(locator.kind, f"{field_name}.kind")
    payload = _encode_mapping(locator.payload, f"{field_name}.payload")
    fingerprint = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return kind, payload, fingerprint


def _decode_locator(kind: object, payload: object, field_name: str) -> Locator:
    return Locator(
        require_non_empty(kind, f"{field_name}.kind"),
        _decode_mapping(payload, f"{field_name}.payload"),
    )


def _encode_float(value: object, field_name: str, *, confidence: bool = False) -> bytes:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    if confidence and not 0.0 <= number <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    return json.dumps(number, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _decode_float(value: object, field_name: str, *, confidence: bool = False) -> float:
    if not isinstance(value, bytes):
        raise ValueError(f"{field_name} must be bytes")
    decoded = json.loads(value.decode("utf-8", "strict"))
    encoded = _encode_float(decoded, field_name, confidence=confidence)
    if encoded != value:
        raise ValueError(f"{field_name} is not canonical")
    return float(decoded)


def _encode_vector(vector: object, dimensions: int) -> bytes:
    if not isinstance(vector, (list, tuple)) or not vector:
        raise ValueError("vector must be a non-empty ordered sequence")
    values = tuple(_decode_numeric(item, "vector") for item in vector)
    if len(values) != dimensions:
        raise ValueError("vector dimension mismatch")
    return json.dumps(values, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _decode_vector(value: object, dimensions: int) -> tuple[float, ...]:
    if not isinstance(value, bytes):
        raise ValueError("embedding must be bytes")
    decoded = json.loads(value.decode("utf-8", "strict"))
    if not isinstance(decoded, list):
        raise ValueError("embedding must be a JSON array")
    vector = tuple(_decode_numeric(item, "embedding") for item in decoded)
    if _encode_vector(vector, dimensions) != value:
        raise ValueError("embedding is not canonical")
    return vector


def _decode_numeric(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must contain finite numbers")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must contain finite numbers")
    return number


def _is_busy(error: sqlite3.OperationalError) -> bool:
    message = str(error).lower()
    return "locked" in message or "busy" in message


def _validate_token_pair(count: object, kind: object) -> None:
    if count is None and kind is None:
        return
    if type(count) is not int or count < 0 or kind not in {"exact", "estimated"}:
        raise ValueError("token count and kind are invalid")


class SQLiteResourceStore:
    """One logical-ID-only SQLite owner for core catalog and search ports."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        self._writer_lock = threading.Lock()
        self._failure_hook: FailureHook | None = None
        self.transaction_open_count = 0

    def set_failure_hook(self, hook: FailureHook | None) -> None:
        """Install an adapter-local deterministic failure hook for atomicity tests."""
        if hook is not None and not callable(hook):
            raise TypeError("hook must be callable")
        self._failure_hook = hook

    def replace_resource(self, batch: PreparedResourceBatch) -> None:
        try:
            prepared = self._preflight(batch)
            if self.connection.in_transaction:
                raise ValueError("caller transaction is active")
            with self._writer_lock:
                self._replace(prepared)
        except CatalogExecutionError:
            raise
        except sqlite3.OperationalError as error:
            category = ErrorCategory.ADAPTER_TIMEOUT if _is_busy(error) else ErrorCategory.CATALOG_ERROR
            raise CatalogExecutionError(category) from None
        except Exception:
            raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR) from None

    def delete_resource(self, resource_id: str) -> None:
        try:
            resource_id = require_non_empty(resource_id, "resource_id")
            if self.connection.in_transaction:
                raise ValueError("caller transaction is active")
            with self._writer_lock:
                self._begin()
                self._point("after_begin")
                self._delete_fts(resource_id)
                self.connection.execute("DELETE FROM core_resources WHERE resource_id = ?", (resource_id,))
                self._point("after_delete")
                self._prune_facets()
                self._point("before_commit")
                self.connection.commit()
        except CatalogExecutionError:
            raise
        except sqlite3.OperationalError as error:
            self._rollback()
            category = ErrorCategory.ADAPTER_TIMEOUT if _is_busy(error) else ErrorCategory.CATALOG_ERROR
            raise CatalogExecutionError(category) from None
        except Exception:
            self._rollback()
            raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR) from None

    def read_resource(self, resource_id: str) -> ResourceRecord | None:
        try:
            resource_id = require_non_empty(resource_id, "resource_id")
            row = self.connection.execute(
                "SELECT * FROM core_resources WHERE resource_id = ?", (resource_id,)
            ).fetchone()
            if row is None:
                return None
            for facet_row in self.connection.execute(
                "SELECT producer_is_null,producer_value,confidence_json "
                "FROM core_resource_facets WHERE resource_id=?",
                (resource_id,),
            ):
                self._validate_facet_row(facet_row)
            return self._resource_from_row(row)
        except CatalogExecutionError:
            raise
        except sqlite3.OperationalError as error:
            category = ErrorCategory.ADAPTER_TIMEOUT if _is_busy(error) else ErrorCategory.CATALOG_ERROR
            raise CatalogExecutionError(category) from None
        except Exception:
            raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR) from None

    def read_unit(self, unit_id: str) -> SearchUnitRecord | None:
        try:
            unit_id = require_non_empty(unit_id, "unit_id")
            row = self.connection.execute(
                "SELECT u.*, p.resource_id AS representation_resource_id, "
                "p.modality AS representation_modality "
                "FROM core_search_units u "
                "JOIN core_representations p ON p.representation_id = u.representation_id "
                "JOIN core_resources r ON r.resource_id = u.resource_id "
                "WHERE u.unit_id = ?",
                (unit_id,),
            ).fetchone()
            return None if row is None else self._unit_from_row(row)
        except sqlite3.OperationalError as error:
            category = ErrorCategory.ADAPTER_TIMEOUT if _is_busy(error) else ErrorCategory.CATALOG_ERROR
            raise CatalogExecutionError(category) from None
        except Exception:
            raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR) from None

    def read_vector(self, unit_id: str, space_id: str) -> VectorRecord | None:
        try:
            unit_id = require_non_empty(unit_id, "unit_id")
            space_id = require_non_empty(space_id, "space_id")
            row = self.connection.execute(
                "SELECT e.unit_id, e.space_id, e.embedding, s.dimensions "
                "FROM core_unit_embeddings e JOIN core_embedding_spaces s USING(space_id) "
                "WHERE e.unit_id = ? AND e.space_id = ?",
                (unit_id, space_id),
            ).fetchone()
            if row is None:
                return None
            return VectorRecord(row["unit_id"], row["space_id"], _decode_vector(row["embedding"], row["dimensions"]))
        except sqlite3.OperationalError as error:
            category = ErrorCategory.ADAPTER_TIMEOUT if _is_busy(error) else ErrorCategory.CATALOG_ERROR
            raise CatalogExecutionError(category) from None
        except Exception:
            raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR) from None

    def find_by_content_hash(
        self,
        content_hash: str,
        *,
        scope: SearchScope,
    ) -> list[ResourceRecord]:
        try:
            content_hash = require_non_empty(content_hash, "content_hash")
            self._require_scope(scope)
            clauses, params = self._scope_clauses(scope)
            where = ["r.content_hash = ?", *clauses]
            rows = self.connection.execute(
                "SELECT DISTINCT r.* FROM core_resources r "
                "JOIN core_search_units u ON u.resource_id = r.resource_id "
                "JOIN core_representations p ON p.representation_id = u.representation_id "
                f"WHERE {' AND '.join(where)} ORDER BY r.resource_id",
                (content_hash, *params),
            ).fetchall()
            return [self._resource_from_row(row) for row in rows]
        except sqlite3.OperationalError as error:
            category = ErrorCategory.ADAPTER_TIMEOUT if _is_busy(error) else ErrorCategory.CATALOG_ERROR
            raise CatalogExecutionError(category) from None
        except CatalogExecutionError:
            raise
        except Exception:
            raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR) from None

    def search_lexical(
        self,
        branch: LexicalBranch,
        *,
        scope: SearchScope,
    ) -> list[RankedCandidate]:
        try:
            if not isinstance(branch, LexicalBranch):
                raise ValueError("branch must be LexicalBranch")
            self._require_scope(scope)
            clauses, params = self._scope_clauses(scope)
            where = ["core_search_units_fts MATCH ?", *clauses]
            statement = (
                "SELECT u.*, p.resource_id AS representation_resource_id, "
                "p.modality AS representation_modality, "
                "bm25(core_search_units_fts) AS branch_score "
                "FROM core_search_units_fts "
                "JOIN core_search_units u ON u.unit_id = core_search_units_fts.unit_id "
                "JOIN core_representations p ON p.representation_id = u.representation_id "
                "JOIN core_resources r ON r.resource_id = u.resource_id "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY branch_score ASC, core_search_units_fts.rowid ASC LIMIT ?"
            )
            try:
                rows = self.connection.execute(
                    statement,
                    (branch.query, *params, branch.candidate_limit),
                ).fetchall()
            except sqlite3.OperationalError:
                fallback_query = plain_query_fallback(branch.query)
                if fallback_query is None:
                    raise
                rows = self.connection.execute(
                    statement,
                    (fallback_query, *params, branch.candidate_limit),
                ).fetchall()
            return [
                self._candidate(row, rank=index, score=-float(row["branch_score"]), branch_id=branch.branch_id)
                for index, row in enumerate(rows, start=1)
            ]
        except (BranchExecutionError, CatalogExecutionError):
            raise
        except sqlite3.OperationalError as error:
            category = ErrorCategory.ADAPTER_TIMEOUT if _is_busy(error) else ErrorCategory.ADAPTER_ERROR
            raise BranchExecutionError(category, branch_id=branch.branch_id) from None
        except Exception:
            raise BranchExecutionError(ErrorCategory.ADAPTER_ERROR, branch_id=branch.branch_id) from None

    def search_vector(
        self,
        branch: VectorBranch,
        *,
        scope: SearchScope,
    ) -> list[RankedCandidate]:
        try:
            if not isinstance(branch, VectorBranch):
                raise ValueError("branch must be VectorBranch")
            self._require_scope(scope)
            space = self.connection.execute(
                "SELECT * FROM core_embedding_spaces WHERE space_id = ?", (branch.space_id,)
            ).fetchone()
            if (
                space is None
                or len(branch.vector) != space["dimensions"]
                or (
                    branch.expected_fingerprint is not None
                    and branch.expected_fingerprint != space["fingerprint"]
                )
            ):
                raise BranchExecutionError(
                    ErrorCategory.INCOMPATIBLE_VECTOR_SPACE, branch_id=branch.branch_id
                )
            query = tuple(_decode_numeric(value, "query vector") for value in branch.vector)
            if space["metric"] == "cosine" and self._norm(query) == 0.0:
                raise BranchExecutionError(
                    ErrorCategory.INCOMPATIBLE_VECTOR_SPACE, branch_id=branch.branch_id
                )
            clauses, params = self._scope_clauses(scope)
            rows = self.connection.execute(
                "SELECT u.*, p.resource_id AS representation_resource_id, "
                "p.modality AS representation_modality, e.embedding "
                "FROM core_unit_embeddings e "
                "JOIN core_search_units u ON u.unit_id = e.unit_id "
                "JOIN core_representations p ON p.representation_id = u.representation_id "
                "JOIN core_resources r ON r.resource_id = u.resource_id "
                f"WHERE e.space_id = ?{' AND ' if clauses else ''}{' AND '.join(clauses)}",
                (branch.space_id, *params),
            ).fetchall()
            scored: list[tuple[float, sqlite3.Row]] = []
            skipped_zero_cosine = False
            for row in rows:
                candidate = _decode_vector(row["embedding"], space["dimensions"])
                if space["metric"] == "cosine" and self._norm(candidate) == 0.0:
                    skipped_zero_cosine = True
                    continue
                score = self._score(query, candidate, space["metric"], branch.branch_id)
                scored.append((score, row))
            if skipped_zero_cosine and not scored:
                raise BranchExecutionError(
                    ErrorCategory.INCOMPATIBLE_VECTOR_SPACE, branch_id=branch.branch_id
                )
            scored.sort(key=lambda item: (-item[0], item[1]["unit_id"]))
            return [
                self._candidate(row, rank=index, score=score, branch_id=branch.branch_id)
                for index, (score, row) in enumerate(scored[: branch.candidate_limit], start=1)
            ]
        except (BranchExecutionError, CatalogExecutionError):
            raise
        except sqlite3.OperationalError as error:
            category = ErrorCategory.ADAPTER_TIMEOUT if _is_busy(error) else ErrorCategory.ADAPTER_ERROR
            raise BranchExecutionError(category, branch_id=branch.branch_id) from None
        except Exception:
            raise BranchExecutionError(ErrorCategory.ADAPTER_ERROR, branch_id=branch.branch_id) from None

    def _replace(self, prepared: dict[str, Any]) -> None:
        batch: PreparedResourceBatch = prepared["batch"]
        resource = batch.resource
        try:
            self._check_source_identity(prepared)
            self._check_spaces(prepared)
            self._begin()
            self._point("after_begin")
            self._check_source_identity(prepared)
            self._check_spaces(prepared)
            self._delete_fts(resource.resource_id)
            self.connection.execute("DELETE FROM core_resources WHERE resource_id = ?", (resource.resource_id,))
            self._point("after_delete")
            self.connection.execute(
                "INSERT INTO core_resources VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                prepared["resource_values"],
            )
            for values in prepared["space_values"]:
                self.connection.execute(
                    "INSERT INTO core_embedding_spaces VALUES(?,?,?,?,?) ON CONFLICT(space_id) DO NOTHING",
                    values,
                )
            for values in prepared["representation_values"]:
                self.connection.execute("INSERT INTO core_representations VALUES(?,?,?,?,?,?,?,?,?,?)", values)
            self._point("after_representations")
            for values in prepared["unit_values"]:
                self.connection.execute("INSERT INTO core_search_units VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", values)
            self._point("after_units")
            for values in prepared["vector_values"]:
                self.connection.execute("INSERT INTO core_unit_embeddings VALUES(?,?,?,?)", values)
            self._point("after_vectors")
            for values in prepared["facet_values"]:
                namespace, value, assignment = values
                self.connection.execute(
                    "INSERT INTO core_facets(namespace,value) VALUES(?,?) ON CONFLICT(namespace,value) DO NOTHING",
                    (namespace, value),
                )
                facet_id = self.connection.execute(
                    "SELECT facet_id FROM core_facets WHERE namespace=? AND value=?",
                    (namespace, value),
                ).fetchone()[0]
                self.connection.execute(
                    "INSERT INTO core_resource_facets VALUES(?,?,?,?,?,?)",
                    (resource.resource_id, facet_id, *assignment),
                )
            self._point("after_facets")
            for unit_id, text in prepared["fts_values"]:
                self.connection.execute(
                    "INSERT INTO core_search_units_fts(unit_id,content) VALUES(?,?)", (unit_id, text)
                )
            self._point("after_fts")
            self._prune_facets()
            self._verify(batch)
            self._point("before_commit")
            self.connection.commit()
        except Exception:
            self._rollback()
            raise

    def _preflight(self, batch: object) -> dict[str, Any]:
        if not isinstance(batch, PreparedResourceBatch):
            raise ValueError("batch must be PreparedResourceBatch")
        resource = batch.resource
        resource_locator_kind, resource_locator_json, resource_fingerprint = _locator_parts(
            resource.locator, "resource.locator"
        )
        resource_values = (
            require_non_empty(resource.resource_id, "resource_id"),
            require_non_empty(resource.resource_kind, "resource_kind"),
            require_non_empty(resource.media_type, "media_type"),
            require_non_empty(resource.source_namespace, "source_namespace"),
            resource_locator_kind,
            resource_locator_json,
            resource_fingerprint,
            require_optional_non_empty(resource.content_hash, "content_hash"),
            None if resource.title is None else require_utf8_encodable(resource.title, "title"),
            _encode_mapping(resource.metadata, "resource.metadata"),
            _now(),
        )
        representation_values = []
        representation_ids: set[str] = set()
        representation_modalities: dict[str, str] = {}
        for item in batch.representations:
            if item.resource_id != resource.resource_id or item.representation_id in representation_ids:
                raise ValueError("invalid representation ownership or identity")
            representation_ids.add(item.representation_id)
            representation_modalities[item.representation_id] = item.modality
            _validate_token_pair(item.token_count, item.token_count_kind)
            representation_values.append(
                (
                    require_non_empty(item.representation_id, "representation_id"),
                    require_non_empty(item.resource_id, "resource_id"),
                    require_non_empty(item.representation_kind, "representation_kind"),
                    require_non_empty(item.modality, "modality"),
                    None if item.text is None else require_utf8_encodable(item.text, "text"),
                    require_optional_non_empty(item.language, "language"),
                    require_optional_non_empty(item.producer_fingerprint, "producer_fingerprint"),
                    item.token_count,
                    item.token_count_kind,
                    _encode_mapping(item.metadata, "representation.metadata"),
                )
            )
        unit_values = []
        fts_values = []
        unit_ids: set[str] = set()
        ordinals: set[tuple[str, int]] = set()
        unit_has_text: dict[str, bool] = {}
        for item in batch.units:
            locator_kind, locator_json, _unused = _locator_parts(item.evidence_locator, "evidence_locator")
            ordinal_key = (item.representation_id, item.ordinal)
            if (
                item.resource_id != resource.resource_id
                or item.unit_id in unit_ids
                or item.representation_id not in representation_ids
                or ordinal_key in ordinals
                or representation_modalities[item.representation_id] != item.modality
            ):
                raise ValueError("invalid unit ownership, identity, modality, or ordinal")
            if type(item.ordinal) is not int or item.ordinal < 0:
                raise ValueError("ordinal must be a non-negative integer")
            _validate_token_pair(item.token_count, item.token_count_kind)
            unit_ids.add(item.unit_id)
            ordinals.add(ordinal_key)
            has_text = isinstance(item.text, str) and bool(item.text.strip())
            unit_has_text[item.unit_id] = has_text
            if has_text:
                fts_values.append((item.unit_id, item.text))
            unit_values.append(
                (
                    require_non_empty(item.unit_id, "unit_id"),
                    require_non_empty(item.resource_id, "resource_id"),
                    require_non_empty(item.representation_id, "representation_id"),
                    require_non_empty(item.unit_kind, "unit_kind"),
                    require_non_empty(item.modality, "modality"),
                    None if item.text is None else require_utf8_encodable(item.text, "text"),
                    locator_kind,
                    locator_json,
                    item.ordinal,
                    item.token_count,
                    item.token_count_kind,
                    _encode_mapping(item.metadata, "unit.metadata"),
                )
            )
        if not representation_ids or not unit_ids:
            raise ValueError("resource graph must contain representations and units")
        if {item.representation_id for item in batch.units} != representation_ids:
            raise ValueError("every representation must own a unit")
        space_values = []
        spaces: dict[str, EmbeddingSpaceRecord] = {}
        for item in batch.spaces:
            if item.space_id in spaces:
                raise ValueError("duplicate space_id")
            if type(item.dimensions) is not int or item.dimensions < 1:
                raise ValueError("space dimensions must be a positive integer")
            if item.metric not in {"cosine", "dot", "l2"}:
                raise ValueError("space metric is invalid")
            spaces[item.space_id] = item
            space_values.append(
                (
                    require_non_empty(item.space_id, "space_id"),
                    item.dimensions,
                    require_non_empty(item.metric, "metric"),
                    require_non_empty(item.fingerprint, "fingerprint"),
                    _encode_mapping(item.metadata, "space.metadata"),
                )
            )
        vector_values = []
        vector_keys: set[tuple[str, str]] = set()
        vector_units: set[str] = set()
        for item in batch.vectors:
            key = (item.unit_id, item.space_id)
            if key in vector_keys or item.unit_id not in unit_ids or item.space_id not in spaces:
                raise ValueError("invalid vector ownership or identity")
            encoded = _encode_vector(item.vector, spaces[item.space_id].dimensions)
            if spaces[item.space_id].metric == "cosine" and self._norm(item.vector) == 0.0:
                raise ValueError("cosine vector norm must be non-zero")
            vector_keys.add(key)
            vector_units.add(item.unit_id)
            vector_values.append(
                (
                    item.unit_id,
                    item.space_id,
                    encoded,
                    _now(),
                )
            )
        if any(not has_text and unit_id not in vector_units for unit_id, has_text in unit_has_text.items()):
            raise ValueError("every unit must have text or a vector")
        facet_values = []
        facet_keys: set[tuple[object, ...]] = set()
        for item in batch.facets:
            if item.resource_id != resource.resource_id:
                raise ValueError("facet belongs to another resource")
            namespace = require_non_empty(item.facet.namespace, "facet.namespace")
            value = require_non_empty(item.facet.value, "facet.value")
            origin = require_non_empty(item.origin, "facet.origin")
            producer = require_optional_non_empty(item.producer_fingerprint, "producer_fingerprint")
            producer_is_null = 1 if producer is None else 0
            producer_value = "" if producer is None else producer
            confidence = None if item.confidence is None else _encode_float(
                item.confidence, "confidence", confidence=True
            )
            key = (namespace, value, origin, producer_is_null, producer_value)
            if key in facet_keys:
                raise ValueError("duplicate facet assignment")
            facet_keys.add(key)
            facet_values.append((namespace, value, (origin, producer_is_null, producer_value, confidence)))
        return {
            "batch": batch,
            "resource_values": resource_values,
            "representation_values": representation_values,
            "unit_values": unit_values,
            "space_values": space_values,
            "vector_values": vector_values,
            "facet_values": facet_values,
            "fts_values": fts_values,
            "source_key": (resource.source_namespace, resource_locator_kind, resource_fingerprint),
        }

    def _begin(self) -> None:
        self._point("before_begin")
        self.connection.execute("BEGIN IMMEDIATE")
        self.transaction_open_count += 1

    def _rollback(self) -> None:
        if self.connection.in_transaction:
            self.connection.rollback()

    def _point(self, name: str) -> None:
        if self._failure_hook is not None:
            self._failure_hook(name)

    def _delete_fts(self, resource_id: str) -> None:
        self.connection.execute(
            "DELETE FROM core_search_units_fts WHERE unit_id IN "
            "(SELECT unit_id FROM core_search_units WHERE resource_id = ?)",
            (resource_id,),
        )

    def _prune_facets(self) -> None:
        self.connection.execute(
            "DELETE FROM core_facets WHERE NOT EXISTS "
            "(SELECT 1 FROM core_resource_facets rf WHERE rf.facet_id = core_facets.facet_id)"
        )

    def _check_source_identity(self, prepared: dict[str, Any]) -> None:
        batch: PreparedResourceBatch = prepared["batch"]
        key = prepared["source_key"]
        by_id = self.connection.execute(
            "SELECT source_namespace, locator_kind, locator_fingerprint FROM core_resources WHERE resource_id=?",
            (batch.resource.resource_id,),
        ).fetchone()
        if by_id is not None and tuple(by_id) != key:
            raise ValueError("resource_id is bound to another source identity")
        by_key = self.connection.execute(
            "SELECT resource_id FROM core_resources "
            "WHERE source_namespace=? AND locator_kind=? AND locator_fingerprint=?",
            key,
        ).fetchone()
        if by_key is not None and by_key["resource_id"] != batch.resource.resource_id:
            raise ValueError("source identity is bound to another resource_id")

    def _check_spaces(self, prepared: dict[str, Any]) -> None:
        for values in prepared["space_values"]:
            row = self.connection.execute(
                "SELECT dimensions,metric,fingerprint,metadata_json FROM core_embedding_spaces WHERE space_id=?",
                (values[0],),
            ).fetchone()
            if row is not None and tuple(row) != values[1:]:
                raise ValueError("embedding space identity mismatch")

    def _verify(self, batch: PreparedResourceBatch) -> None:
        resource_id = batch.resource.resource_id
        counts = self.connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM core_representations WHERE resource_id=?) AS representations,"
            "(SELECT COUNT(*) FROM core_search_units WHERE resource_id=?) AS units,"
            "(SELECT COUNT(*) FROM core_unit_embeddings e "
            "JOIN core_search_units u USING(unit_id) WHERE u.resource_id=?) AS vectors,"
            "(SELECT COUNT(*) FROM core_resource_facets WHERE resource_id=?) AS facets",
            (resource_id, resource_id, resource_id, resource_id),
        ).fetchone()
        if tuple(counts) != (
            len(batch.representations),
            len(batch.units),
            len(batch.vectors),
            len(batch.facets),
        ):
            raise RuntimeError("resource graph count invariant failed")
        missing_representation = self.connection.execute(
            "SELECT 1 FROM core_representations p WHERE p.resource_id=? AND NOT EXISTS "
            "(SELECT 1 FROM core_search_units u WHERE u.representation_id=p.representation_id) LIMIT 1",
            (resource_id,),
        ).fetchone()
        if missing_representation is not None:
            raise RuntimeError("representation ownership invariant failed")
        fts_actual = {
            row[0]
            for row in self.connection.execute(
                "SELECT unit_id FROM core_search_units_fts WHERE unit_id IN "
                "(SELECT unit_id FROM core_search_units WHERE resource_id=?)",
                (resource_id,),
            )
        }
        fts_expected = {item.unit_id for item in batch.units if isinstance(item.text, str) and item.text.strip()}
        if fts_actual != fts_expected or self.connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("FTS or foreign-key invariant failed")
        self._resource_from_row(
            self.connection.execute("SELECT * FROM core_resources WHERE resource_id=?", (resource_id,)).fetchone()
        )
        for row in self.connection.execute(
            "SELECT u.*, p.resource_id AS representation_resource_id, "
            "p.modality AS representation_modality "
            "FROM core_search_units u "
            "JOIN core_representations p ON p.representation_id=u.representation_id "
            "WHERE u.resource_id=?",
            (resource_id,),
        ):
            self._unit_from_row(row)
        for vector in batch.vectors:
            row = self.connection.execute(
                "SELECT e.embedding,s.dimensions FROM core_unit_embeddings e "
                "JOIN core_embedding_spaces s USING(space_id) WHERE e.unit_id=? AND e.space_id=?",
                (vector.unit_id, vector.space_id),
            ).fetchone()
            _decode_vector(row["embedding"], row["dimensions"])
        for row in self.connection.execute(
            "SELECT producer_is_null,producer_value,confidence_json FROM core_resource_facets WHERE resource_id=?",
            (resource_id,),
        ):
            self._validate_facet_row(row)

    @staticmethod
    def _resource_from_row(row: sqlite3.Row) -> ResourceRecord:
        locator = _decode_locator(row["locator_kind"], row["locator_json"], "locator")
        _kind, canonical, fingerprint = _locator_parts(locator, "locator")
        if canonical != row["locator_json"] or fingerprint != row["locator_fingerprint"]:
            raise ValueError("locator fingerprint mismatch")
        return ResourceRecord(
            require_non_empty(row["resource_id"], "resource_id"),
            require_non_empty(row["resource_kind"], "resource_kind"),
            require_non_empty(row["media_type"], "media_type"),
            require_non_empty(row["source_namespace"], "source_namespace"),
            locator,
            require_optional_non_empty(row["content_hash"], "content_hash"),
            None if row["title"] is None else require_utf8_encodable(row["title"], "title"),
            _decode_mapping(row["metadata_json"], "metadata"),
        )

    @staticmethod
    def _unit_from_row(row: sqlite3.Row) -> SearchUnitRecord:
        resource_id = require_non_empty(row["resource_id"], "resource_id")
        modality = require_non_empty(row["modality"], "modality")
        if (
            require_non_empty(row["representation_resource_id"], "representation.resource_id")
            != resource_id
            or require_non_empty(row["representation_modality"], "representation.modality")
            != modality
        ):
            raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR)
        return SearchUnitRecord(
            require_non_empty(row["unit_id"], "unit_id"),
            resource_id,
            require_non_empty(row["representation_id"], "representation_id"),
            require_non_empty(row["unit_kind"], "unit_kind"),
            modality,
            None if row["text_content"] is None else require_utf8_encodable(row["text_content"], "text"),
            _decode_locator(row["evidence_locator_kind"], row["evidence_locator_json"], "evidence_locator"),
            row["ordinal"],
            row["token_count"],
            row["token_count_kind"],
            _decode_mapping(row["metadata_json"], "metadata"),
        )

    @staticmethod
    def _validate_facet_row(row: sqlite3.Row) -> tuple[str | None, float | None]:
        discriminator = row["producer_is_null"]
        value = row["producer_value"]
        if discriminator == 1 and value == "":
            producer = None
        elif discriminator == 0:
            producer = require_non_empty(value, "producer_fingerprint")
        else:
            raise ValueError("invalid producer discriminator")
        confidence = (
            None
            if row["confidence_json"] is None
            else _decode_float(row["confidence_json"], "confidence", confidence=True)
        )
        return producer, confidence

    def _candidate(
        self,
        row: sqlite3.Row,
        *,
        rank: int,
        score: float,
        branch_id: str,
    ) -> RankedCandidate:
        unit = self._unit_from_row(row)
        return RankedCandidate(
            unit.unit_id,
            unit.resource_id,
            unit.representation_id,
            rank,
            score,
            branch_id,
            unit.evidence_locator,
            unit.metadata,
        )

    @staticmethod
    def _require_scope(scope: object) -> SearchScope:
        if not isinstance(scope, SearchScope):
            raise ValueError("scope must be SearchScope")
        return scope

    @staticmethod
    def _scope_clauses(scope: SearchScope) -> tuple[list[str], list[object]]:
        clauses: list[str] = []
        params: list[object] = []

        def add_in(column: str, values: Sequence[str]) -> None:
            if values:
                clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
                params.extend(values)

        add_in("r.resource_kind", scope.resource_kinds)
        add_in("r.media_type", scope.media_types)
        add_in("r.source_namespace", scope.source_namespaces)
        add_in("p.representation_kind", scope.representation_kinds)
        add_in("u.modality", scope.modalities)
        add_in("u.unit_kind", scope.unit_kinds)
        if scope.facets_any:
            pairs = " OR ".join("(f.namespace=? AND f.value=?)" for _ in scope.facets_any)
            clauses.append(
                "EXISTS (SELECT 1 FROM core_resource_facets rf JOIN core_facets f USING(facet_id) "
                f"WHERE rf.resource_id=r.resource_id AND ({pairs}))"
            )
            for facet in scope.facets_any:
                params.extend((facet.namespace, facet.value))
        for facet in scope.facets_all:
            clauses.append(
                "EXISTS (SELECT 1 FROM core_resource_facets rf JOIN core_facets f USING(facet_id) "
                "WHERE rf.resource_id=r.resource_id AND f.namespace=? AND f.value=?)"
            )
            params.extend((facet.namespace, facet.value))
        if scope.facets_none:
            pairs = " OR ".join("(f.namespace=? AND f.value=?)" for _ in scope.facets_none)
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM core_resource_facets rf JOIN core_facets f USING(facet_id) "
                f"WHERE rf.resource_id=r.resource_id AND ({pairs}))"
            )
            for facet in scope.facets_none:
                params.extend((facet.namespace, facet.value))
        return clauses, params

    @staticmethod
    def _norm(vector: Sequence[float]) -> float:
        return math.hypot(*vector)

    @classmethod
    def _score(
        cls,
        query: Sequence[float],
        candidate: Sequence[float],
        metric: str,
        branch_id: str,
    ) -> float:
        if metric == "dot":
            return sum(left * right for left, right in zip(query, candidate, strict=True))
        if metric == "l2":
            return -math.sqrt(
                sum((left - right) ** 2 for left, right in zip(query, candidate, strict=True))
            )
        if metric == "cosine":
            denominator = cls._norm(query) * cls._norm(candidate)
            if denominator == 0.0:
                raise BranchExecutionError(
                    ErrorCategory.INCOMPATIBLE_VECTOR_SPACE, branch_id=branch_id
                )
            return sum(left * right for left, right in zip(query, candidate, strict=True)) / denominator
        raise BranchExecutionError(ErrorCategory.INCOMPATIBLE_VECTOR_SPACE, branch_id=branch_id)
