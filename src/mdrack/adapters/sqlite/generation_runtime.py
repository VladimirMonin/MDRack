"""SQLite durability and verification runtime for app-owned store generations."""

from __future__ import annotations

import json
import math
import os
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from mdrack.adapters.sqlite.resource_store import SQLiteResourceStore
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import (
    EXPECTED_MIGRATION_VERSION,
)
from mdrack_core.domain import (
    EmbeddingSpaceRecord,
    Facet,
    RepresentationRecord,
    ResourceFacet,
)
from mdrack_core.domain.common import (
    JSONValue,
    canonical_json,
    freeze_json_mapping,
    require_non_empty,
    require_optional_non_empty,
    require_utf8_encodable,
)
from mdrack_sqlite.contract_v2 import SQLITE_CATALOG_V2_SCHEMA_VERSION
from mdrack_sqlite.migrations_v2 import apply_v2_migrations, validate_v2_clean_identity
from mdrack_sqlite.vector_codecs import decode_vector_payload

FailureHook = Callable[[str], None]


class GenerationRuntimeError(RuntimeError):
    """A privacy-safe candidate verification or durability failure."""


class SQLiteGenerationRuntime:
    """Own candidate SQLite creation, verification, checkpoint, and fsync barriers."""

    def __init__(self, failure_hook: FailureHook | None = None) -> None:
        self._failure_hook = failure_hook

    def set_failure_hook(self, hook: FailureHook | None) -> None:
        self._failure_hook = hook

    def create_candidate(self, database_path: Path) -> sqlite3.Connection:
        """Exclusively create and open one empty candidate database."""
        database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(database_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except OSError as exc:
            raise GenerationRuntimeError("candidate_create_failed") from exc
        os.close(fd)
        self._fail("after_exclusive_create")
        connection: sqlite3.Connection | None = None
        try:
            connection = get_connection(database_path)
            self._fail("after_candidate_open")
            return connection
        except Exception as exc:
            if connection is not None:
                connection.close()
            if isinstance(exc, GenerationRuntimeError):
                raise
            raise GenerationRuntimeError("candidate_open_failed") from exc

    def migrate_candidate(self, connection: sqlite3.Connection) -> None:
        """Create the exact independent v2 clean catalog; never upgrade a source database."""
        try:
            apply_v2_migrations(connection)
            self._fail("after_candidate_migrations")
        except Exception as exc:
            if isinstance(exc, GenerationRuntimeError):
                raise
            raise GenerationRuntimeError("candidate_migration_failed") from exc

    def verify_candidate(
        self,
        connection: sqlite3.Connection,
        *,
        expected_fingerprints: Sequence[str] = (),
        expected_schema_version: str = SQLITE_CATALOG_V2_SCHEMA_VERSION,
    ) -> dict[str, int]:
        """Verify exact schema, integrity, graph, FTS, and vector contracts."""
        try:
            if connection.in_transaction:
                raise GenerationRuntimeError("candidate_transaction_open")
            if expected_schema_version == SQLITE_CATALOG_V2_SCHEMA_VERSION:
                try:
                    validate_v2_clean_identity(connection)
                except Exception as exc:
                    raise GenerationRuntimeError("candidate_manifest_mismatch") from exc
            elif expected_schema_version == EXPECTED_MIGRATION_VERSION:
                versions = [
                    row["version"]
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall()
                ]
                if versions != [
                    f"{index:04d}" for index in range(int(EXPECTED_MIGRATION_VERSION) + 1)
                ]:
                    raise GenerationRuntimeError("candidate_manifest_mismatch")
            else:
                raise GenerationRuntimeError("candidate_manifest_mismatch")
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check").fetchall()]
            if integrity != ["ok"] or connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise GenerationRuntimeError("candidate_integrity_failed")

            required = {
                "core_resources",
                "core_representations",
                "core_search_units",
                "core_embedding_spaces",
                "core_unit_embeddings",
                "core_facets",
                "core_resource_facets",
                "core_search_units_fts",
            }
            if expected_schema_version == SQLITE_CATALOG_V2_SCHEMA_VERSION:
                required |= {"mdrack_vector_codecs", "mdrack_vector_backends"}
            objects = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                ).fetchall()
            }
            if not required <= objects:
                raise GenerationRuntimeError("candidate_schema_missing")
            if expected_schema_version == SQLITE_CATALOG_V2_SCHEMA_VERSION:
                self._verify_v2_vector_registry(connection)

            incomplete_resources = connection.execute(
                "SELECT 1 FROM core_resources resource "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM core_representations representation "
                "WHERE representation.resource_id=resource.resource_id"
                ") OR NOT EXISTS ("
                "SELECT 1 FROM core_representations representation "
                "JOIN core_search_units unit "
                "ON unit.representation_id=representation.representation_id "
                "AND unit.resource_id=resource.resource_id "
                "WHERE representation.resource_id=resource.resource_id"
                ") LIMIT 1"
            ).fetchone()
            invalid_representations = connection.execute(
                "SELECT 1 FROM core_representations r "
                "LEFT JOIN core_search_units u ON u.representation_id=r.representation_id "
                "GROUP BY r.representation_id HAVING COUNT(u.unit_id)=0 LIMIT 1"
            ).fetchone()
            invalid_units = connection.execute(
                "SELECT 1 FROM core_search_units u "
                "JOIN core_representations r ON r.representation_id=u.representation_id "
                "LEFT JOIN core_unit_embeddings e ON e.unit_id=u.unit_id "
                "WHERE u.resource_id<>r.resource_id OR u.modality<>r.modality "
                "OR ((u.text_content IS NULL OR trim(u.text_content)='') AND e.unit_id IS NULL) "
                "LIMIT 1"
            ).fetchone()
            if (
                incomplete_resources is not None
                or invalid_representations is not None
                or invalid_units is not None
            ):
                raise GenerationRuntimeError("candidate_graph_invalid")

            expected_fts = {
                row[0]
                for row in connection.execute(
                    "SELECT unit_id FROM core_search_units "
                    "WHERE text_content IS NOT NULL AND trim(text_content)<>''"
                ).fetchall()
            }
            actual_fts_rows = connection.execute(
                "SELECT unit_id, COUNT(*) AS count FROM core_search_units_fts GROUP BY unit_id"
            ).fetchall()
            actual_fts = {row[0] for row in actual_fts_rows}
            if expected_fts != actual_fts or any(row[1] != 1 for row in actual_fts_rows):
                raise GenerationRuntimeError("candidate_fts_invalid")

            vector_rows = connection.execute(
                "SELECT e.embedding,s.dimensions,s.metadata_json FROM core_unit_embeddings e "
                "JOIN core_embedding_spaces s ON s.space_id=e.space_id"
            ).fetchall()
            for row in vector_rows:
                self._validate_vector(row[0], row[1], row[2])
            confidence_rows = connection.execute(
                "SELECT confidence_json FROM core_resource_facets WHERE confidence_json IS NOT NULL"
            ).fetchall()
            for row in confidence_rows:
                self._validate_confidence(row[0])

            self._verify_adapter_graph(connection, expected_fingerprints)

            counts = {
                "resources": connection.execute("SELECT COUNT(*) FROM core_resources").fetchone()[0],
                "representations": connection.execute(
                    "SELECT COUNT(*) FROM core_representations"
                ).fetchone()[0],
                "units": connection.execute("SELECT COUNT(*) FROM core_search_units").fetchone()[0],
                "vectors": len(vector_rows),
                "fts_rows": len(actual_fts_rows),
            }
            self._fail("after_candidate_verification")
            return counts
        except GenerationRuntimeError:
            raise
        except Exception as exc:
            raise GenerationRuntimeError("candidate_verification_failed") from exc

    def finalize_candidate(
        self,
        connection: sqlite3.Connection,
        database_path: Path,
    ) -> None:
        """Checkpoint, close, and durably fsync the candidate and containing directory."""
        closed = False
        try:
            self._fail("before_checkpoint")
            row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if row is None or tuple(row) != (0, 0, 0):
                raise GenerationRuntimeError("candidate_checkpoint_busy")
            self._fail("after_checkpoint")
            connection.close()
            closed = True
            self._fail("after_candidate_close")
            self._fail("before_database_fsync")
            _fsync_file(database_path)
            self._fail("after_database_fsync")
            self._fail("before_directory_fsync")
            _fsync_directory(database_path.parent)
            self._fail("after_directory_fsync")
        except GenerationRuntimeError:
            raise
        except Exception as exc:
            raise GenerationRuntimeError("candidate_durability_failed") from exc
        finally:
            if not closed:
                connection.close()

    def verify_database_path(
        self,
        database_path: Path,
        *,
        expected_version: str,
        expected_fingerprints: Sequence[str] = (),
    ) -> dict[str, int]:
        """Verify a closed database read-only without creating WAL/SHM files."""
        try:
            connection = sqlite3.connect(
                f"file:{database_path.as_posix()}?mode=ro",
                uri=True,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA query_only=ON")
        except sqlite3.Error as exc:
            raise GenerationRuntimeError("generation_open_failed") from exc
        try:
            if expected_version == SQLITE_CATALOG_V2_SCHEMA_VERSION:
                try:
                    validate_v2_clean_identity(connection)
                except Exception as exc:
                    raise GenerationRuntimeError("generation_schema_mismatch") from exc
                return self.verify_candidate(
                    connection,
                    expected_fingerprints=expected_fingerprints,
                    expected_schema_version=SQLITE_CATALOG_V2_SCHEMA_VERSION,
                )
            versions = [
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
            if versions != [f"{index:04d}" for index in range(int(expected_version) + 1)]:
                raise GenerationRuntimeError("generation_schema_mismatch")
            if expected_version == EXPECTED_MIGRATION_VERSION:
                return self.verify_candidate(
                    connection,
                    expected_fingerprints=expected_fingerprints,
                    expected_schema_version=EXPECTED_MIGRATION_VERSION,
                )
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check").fetchall()]
            if integrity != ["ok"] or connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise GenerationRuntimeError("generation_integrity_failed")
            return {"resources": 0, "representations": 0, "units": 0, "vectors": 0, "fts_rows": 0}
        finally:
            connection.close()

    @staticmethod
    def _verify_v2_vector_registry(connection: sqlite3.Connection) -> None:
        codecs = [
            tuple(row)
            for row in connection.execute(
                "SELECT codec_id,codec_version,component_type,byte_order,lossy "
                "FROM mdrack_vector_codecs ORDER BY codec_id"
            ).fetchall()
        ]
        if codecs != [
            ("ieee754-f32-le-v1", 1, "float32", "little", 0),
            ("ieee754-f64-le-v1", 1, "float64", "little", 0),
        ]:
            raise GenerationRuntimeError("candidate_manifest_mismatch")
        backends = [
            tuple(row)
            for row in connection.execute(
                "SELECT backend_id,backend_schema_version,extension_required,"
                "supports_atomic_replace,supports_atomic_delete "
                "FROM mdrack_vector_backends ORDER BY backend_id"
            ).fetchall()
        ]
        if backends != [("builtin-exact-v1", 1, 0, 1, 1)]:
            raise GenerationRuntimeError("candidate_manifest_mismatch")

    @staticmethod
    def _verify_adapter_graph(
        connection: sqlite3.Connection,
        expected_fingerprints: Sequence[str],
    ) -> None:
        """Read every persisted graph family through production DTO/adapter invariants."""
        store = SQLiteResourceStore(connection)
        observed_fingerprints: set[str] = set()

        resource_ids = [
            row[0]
            for row in connection.execute(
                "SELECT resource_id FROM core_resources ORDER BY resource_id"
            ).fetchall()
        ]
        for resource_id in resource_ids:
            if store.read_resource(resource_id) is None:
                raise GenerationRuntimeError("candidate_adapter_readback_invalid")

        representation_rows = connection.execute(
            "SELECT * FROM core_representations ORDER BY representation_id"
        ).fetchall()
        for row in representation_rows:
            producer = require_optional_non_empty(
                row["producer_fingerprint"],
                "producer_fingerprint",
            )
            if producer is not None:
                observed_fingerprints.add(producer)
            RepresentationRecord(
                require_non_empty(row["representation_id"], "representation_id"),
                require_non_empty(row["resource_id"], "resource_id"),
                require_non_empty(row["representation_kind"], "representation_kind"),
                require_non_empty(row["modality"], "modality"),
                (
                    None
                    if row["text_content"] is None
                    else require_utf8_encodable(row["text_content"], "text_content")
                ),
                require_optional_non_empty(row["language"], "language"),
                producer,
                row["token_count"],
                row["token_count_kind"],
                _decode_canonical_mapping(row["metadata_json"], "representation.metadata"),
            )

        unit_ids = [
            row[0]
            for row in connection.execute(
                "SELECT unit_id FROM core_search_units ORDER BY unit_id"
            ).fetchall()
        ]
        for unit_id in unit_ids:
            if store.read_unit(unit_id) is None:
                raise GenerationRuntimeError("candidate_adapter_readback_invalid")

        space_rows = connection.execute(
            "SELECT * FROM core_embedding_spaces ORDER BY space_id"
        ).fetchall()
        for row in space_rows:
            fingerprint = require_non_empty(row["fingerprint"], "space.fingerprint")
            observed_fingerprints.add(fingerprint)
            EmbeddingSpaceRecord(
                require_non_empty(row["space_id"], "space_id"),
                row["dimensions"],
                require_non_empty(row["metric"], "metric"),
                fingerprint,
                _decode_canonical_mapping(row["metadata_json"], "space.metadata"),
            )

        vector_rows = connection.execute(
            "SELECT unit_id,space_id,embedded_at FROM core_unit_embeddings "
            "ORDER BY unit_id,space_id"
        ).fetchall()
        for row in vector_rows:
            if store.read_vector(row["unit_id"], row["space_id"]) is None:
                raise GenerationRuntimeError("candidate_adapter_readback_invalid")
            require_non_empty(row["embedded_at"], "embedded_at")

        facet_rows = connection.execute(
            "SELECT rf.resource_id,f.namespace,f.value,rf.origin,rf.producer_is_null,"
            "rf.producer_value,rf.confidence_json FROM core_resource_facets rf "
            "JOIN core_facets f USING(facet_id) "
            "ORDER BY rf.resource_id,f.namespace,f.value,rf.origin,"
            "rf.producer_is_null,rf.producer_value"
        ).fetchall()
        for row in facet_rows:
            producer = _decode_producer_fingerprint(
                row["producer_is_null"],
                row["producer_value"],
            )
            if producer is not None:
                observed_fingerprints.add(producer)
            confidence = (
                None
                if row["confidence_json"] is None
                else _decode_confidence(row["confidence_json"])
            )
            ResourceFacet(
                require_non_empty(row["resource_id"], "resource_id"),
                Facet(
                    require_non_empty(row["namespace"], "facet.namespace"),
                    require_non_empty(row["value"], "facet.value"),
                ),
                require_non_empty(row["origin"], "facet.origin"),
                producer,
                confidence,
            )

        indexed_at_rows = connection.execute(
            "SELECT indexed_at FROM core_resources ORDER BY resource_id"
        ).fetchall()
        for row in indexed_at_rows:
            require_non_empty(row[0], "indexed_at")

        duplicate_source = connection.execute(
            "SELECT 1 FROM core_resources GROUP BY source_namespace,locator_kind,"
            "locator_fingerprint HAVING COUNT(*)<>1 LIMIT 1"
        ).fetchone()
        if duplicate_source is not None:
            raise GenerationRuntimeError("candidate_adapter_readback_invalid")

        expected = {
            require_non_empty(value, "expected_fingerprint")
            for value in expected_fingerprints
        }
        if not expected <= observed_fingerprints:
            raise GenerationRuntimeError("candidate_fingerprint_mismatch")

    def _fail(self, point: str) -> None:
        if self._failure_hook is not None:
            self._failure_hook(point)

    @staticmethod
    def _validate_vector(payload: object, dimensions: object, metadata_payload: object) -> None:
        if not isinstance(payload, bytes) or type(dimensions) is not int:
            raise GenerationRuntimeError("candidate_vector_invalid")
        try:
            metadata = _decode_canonical_mapping(metadata_payload, "space.metadata")
            decode_vector_payload(payload, dimensions=dimensions, metadata=metadata)
        except (TypeError, ValueError) as exc:
            raise GenerationRuntimeError("candidate_vector_invalid") from exc

    @staticmethod
    def _validate_confidence(payload: object) -> None:
        if not isinstance(payload, bytes):
            raise GenerationRuntimeError("candidate_confidence_invalid")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GenerationRuntimeError("candidate_confidence_invalid") from exc
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GenerationRuntimeError("candidate_confidence_invalid")
        number = float(value)
        canonical = json.dumps(number, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if not math.isfinite(number) or not 0.0 <= number <= 1.0 or canonical != payload:
            raise GenerationRuntimeError("candidate_confidence_invalid")


def _decode_canonical_mapping(
    payload: object,
    field_name: str,
) -> Mapping[str, JSONValue]:
    text = require_utf8_encodable(payload, field_name)
    decoded = json.loads(text)
    frozen = freeze_json_mapping(decoded, field_name)
    if canonical_json(frozen) != text:
        raise GenerationRuntimeError("candidate_adapter_readback_invalid")
    return frozen


def _decode_producer_fingerprint(
    producer_is_null: object,
    producer_value: object,
) -> str | None:
    if producer_is_null == 1 and producer_value == "":
        return None
    if producer_is_null == 0:
        return require_non_empty(producer_value, "producer_fingerprint")
    raise GenerationRuntimeError("candidate_adapter_readback_invalid")


def _decode_confidence(payload: object) -> float:
    SQLiteGenerationRuntime._validate_confidence(payload)
    assert isinstance(payload, bytes)
    return float(json.loads(payload.decode("utf-8")))


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
