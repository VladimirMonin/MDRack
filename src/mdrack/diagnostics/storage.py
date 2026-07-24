"""Read-only, privacy-safe SQLite storage analysis."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Literal, Sequence, cast

from mdrack.application.compatibility import resolve_application_database_path
from mdrack.diagnostics.integrity import get_generation_status


class StorageAnalysisError(RuntimeError):
    """Raised when aggregate storage analysis cannot complete safely."""

    def __init__(self) -> None:
        super().__init__("Storage analysis could not be completed")


@dataclass(frozen=True)
class PayloadSizeSummary:
    """Aggregate vector payload statistics that never include vector values."""

    count: int
    total_bytes: int
    min_bytes: int | None
    median_bytes: float | None
    p95_bytes: int | None
    max_bytes: int | None

    @classmethod
    def from_sizes(cls, sizes: Sequence[int]) -> PayloadSizeSummary:
        if not sizes:
            return cls(
                count=0,
                total_bytes=0,
                min_bytes=None,
                median_bytes=None,
                p95_bytes=None,
                max_bytes=None,
            )
        ordered = sorted(sizes)
        p95_index = max(0, (95 * len(ordered) + 99) // 100 - 1)
        return cls(
            count=len(ordered),
            total_bytes=sum(ordered),
            min_bytes=ordered[0],
            median_bytes=median(ordered),
            p95_bytes=ordered[p95_index],
            max_bytes=ordered[-1],
        )

    def to_dict(self) -> dict[str, int | float | None]:
        return {
            "count": self.count,
            "total_bytes": self.total_bytes,
            "min_bytes": self.min_bytes,
            "median_bytes": self.median_bytes,
            "p95_bytes": self.p95_bytes,
            "max_bytes": self.max_bytes,
        }


@dataclass(frozen=True)
class VectorSpaceAnalysis:
    """One anonymous vector-space aggregate; source space identifiers stay private."""

    contour: Literal["legacy", "core"]
    dimensions: int | None
    metric: Literal["cosine", "dot", "l2"] | None
    payload: PayloadSizeSummary

    def to_dict(self) -> dict[str, object]:
        return {
            "contour": self.contour,
            "dimensions": self.dimensions,
            "metric": self.metric,
            "vector_count": self.payload.count,
            "payload": self.payload.to_dict(),
        }


@dataclass(frozen=True)
class StorageAnalysis:
    """Portable aggregate diagnostics for one SQLite database file."""

    readiness: dict[str, object]
    database_bytes: dict[str, int]
    pages: dict[str, int]
    records: dict[str, dict[str, int]]
    vector_payload: dict[str, PayloadSizeSummary]
    vectors_by_space: tuple[VectorSpaceAnalysis, ...]
    duplication: dict[str, int | str]
    fts_bytes: dict[str, bool | int | None]
    codec_backend_registry: tuple[dict[str, int | str], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "readiness": self.readiness,
            "database_bytes": self.database_bytes,
            "pages": self.pages,
            "records": self.records,
            "vector_payload": {
                contour: payload.to_dict()
                for contour, payload in self.vector_payload.items()
            },
            "vectors_by_space": [space.to_dict() for space in self.vectors_by_space],
            "duplication": self.duplication,
            "fts_bytes": self.fts_bytes,
            "codec_backend_registry": list(self.codec_backend_registry),
        }


_KNOWN_TABLES = frozenset(
    {
        "files",
        "sections",
        "chunks",
        "embedding_profiles",
        "chunk_embeddings",
        "chunks_fts",
        "core_resources",
        "core_representations",
        "core_search_units",
        "core_embedding_spaces",
        "core_unit_embeddings",
        "core_search_units_fts",
        "mdrack_vector_backends",
        "mdrack_vector_codecs",
    }
)
_CORE_CATALOG_TABLES = frozenset(
    {
        "core_resources",
        "core_representations",
        "core_search_units",
        "core_embedding_spaces",
        "core_unit_embeddings",
        "core_search_units_fts",
    }
)


def analyze_application_storage(root: Path, config: Any) -> StorageAnalysis:
    """Analyze the configured active application store without modifying it."""
    try:
        database_path = resolve_application_database_path(root, config)
        store_dir = Path(config.paths.store)
        if not store_dir.is_absolute():
            store_dir = root.resolve() / store_dir
        readiness = _application_readiness(get_generation_status(store_dir))
        return _analyze_database(database_path, readiness=readiness, require_core_catalog=False)
    except StorageAnalysisError:
        raise
    except Exception:
        raise StorageAnalysisError() from None


def analyze_standalone_catalog(database_path: Path) -> StorageAnalysis:
    """Analyze an explicit clean resource-core catalog without modifying it."""
    try:
        return _analyze_database(
            database_path,
            readiness={"state": "ready"},
            require_core_catalog=True,
        )
    except StorageAnalysisError:
        raise
    except Exception:
        raise StorageAnalysisError() from None


def _application_readiness(generation_status: dict[str, object]) -> dict[str, object]:
    state = generation_status.get("generation_state")
    if state not in {"legacy_only", "building", "failed", "rebuild_required", "ready"}:
        raise StorageAnalysisError()
    return {"state": state}


def _analyze_database(
    database_path: Path,
    *,
    readiness: dict[str, object],
    require_core_catalog: bool,
) -> StorageAnalysis:
    sizes = _database_sizes(database_path)
    connection = _open_read_only(database_path)
    try:
        tables = _known_tables(connection)
        if require_core_catalog and not _CORE_CATALOG_TABLES <= tables:
            raise StorageAnalysisError()

        pages = _page_metrics(connection)
        legacy_spaces = _legacy_vector_spaces(connection, tables)
        core_spaces = _core_vector_spaces(connection, tables)
        legacy_payload = _all_vector_payload(connection, tables, "chunk_embeddings")
        core_payload = _all_vector_payload(connection, tables, "core_unit_embeddings")
        records = _record_counts(connection, tables, legacy_payload, core_payload)
        duplication = _duplication(records, legacy_payload, core_payload)
        fts_bytes = _fts_bytes(connection, tables)
        registry = _codec_backend_registry(connection, tables, legacy_payload, core_payload)
        return StorageAnalysis(
            readiness=dict(readiness),
            database_bytes=sizes,
            pages=pages,
            records=records,
            vector_payload={"legacy": legacy_payload, "core": core_payload},
            vectors_by_space=tuple([*legacy_spaces, *core_spaces]),
            duplication=duplication,
            fts_bytes=fts_bytes,
            codec_backend_registry=registry,
        )
    except StorageAnalysisError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError):
        raise StorageAnalysisError() from None
    finally:
        connection.close()


def _open_read_only(database_path: Path) -> sqlite3.Connection:
    if not database_path.is_file():
        raise StorageAnalysisError()
    connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _database_sizes(database_path: Path) -> dict[str, int]:
    if not database_path.is_file():
        raise StorageAnalysisError()
    main = database_path.stat().st_size
    wal = _optional_file_size(database_path.with_name(f"{database_path.name}-wal"))
    shm = _optional_file_size(database_path.with_name(f"{database_path.name}-shm"))
    return {"main": main, "wal": wal, "shm": shm, "total": main + wal + shm}


def _optional_file_size(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


def _known_tables(connection: sqlite3.Connection) -> set[str]:
    placeholders = ", ".join("?" for _ in _KNOWN_TABLES)
    query = (
        "SELECT name FROM sqlite_master "
        "WHERE type IN ('table', 'view') AND name IN (" + placeholders + ")"
    )
    return {str(row[0]) for row in connection.execute(query, tuple(_KNOWN_TABLES))}


def _page_metrics(connection: sqlite3.Connection) -> dict[str, int]:
    page_size = _pragma_int(connection, "page_size")
    page_count = _pragma_int(connection, "page_count")
    freelist_count = _pragma_int(connection, "freelist_count")
    return {
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "allocated_bytes": page_size * page_count,
        "freelist_bytes": page_size * freelist_count,
    }


def _pragma_int(connection: sqlite3.Connection, pragma: str) -> int:
    value = connection.execute(f"PRAGMA {pragma}").fetchone()[0]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StorageAnalysisError()
    return value


def _record_counts(
    connection: sqlite3.Connection,
    tables: set[str],
    legacy_payload: PayloadSizeSummary,
    core_payload: PayloadSizeSummary,
) -> dict[str, dict[str, int]]:
    return {
        "legacy": {
            "files": _table_count(connection, tables, "files"),
            "sections": _table_count(connection, tables, "sections"),
            "chunks": _table_count(connection, tables, "chunks"),
            "vectors": legacy_payload.count,
        },
        "core": {
            "resources": _table_count(connection, tables, "core_resources"),
            "representations": _table_count(connection, tables, "core_representations"),
            "units": _table_count(connection, tables, "core_search_units"),
            "spaces": _table_count(connection, tables, "core_embedding_spaces"),
            "vectors": core_payload.count,
        },
    }


def _table_count(connection: sqlite3.Connection, tables: set[str], table: str) -> int:
    if table not in tables:
        return 0
    value = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StorageAnalysisError()
    return value


def _legacy_vector_spaces(
    connection: sqlite3.Connection,
    tables: set[str],
) -> list[VectorSpaceAnalysis]:
    if "chunk_embeddings" not in tables:
        return []
    if "embedding_profiles" not in tables:
        return _legacy_orphan_vector_spaces(connection)

    rows = connection.execute(
        """
        SELECT
            p.name AS private_space_id,
            CASE WHEN typeof(p.dimensions) = 'integer' AND p.dimensions >= 1
                THEN p.dimensions ELSE NULL END AS dimensions
        FROM embedding_profiles p
        ORDER BY p.name
        """
    ).fetchall()
    return [
        VectorSpaceAnalysis(
            contour="legacy",
            dimensions=_valid_dimension(row["dimensions"]),
            metric=None,
            payload=_payload_for_legacy_space(connection, row["private_space_id"]),
        )
        for row in rows
    ]


def _legacy_orphan_vector_spaces(connection: sqlite3.Connection) -> list[VectorSpaceAnalysis]:
    rows = connection.execute(
        "SELECT profile_name AS private_space_id FROM chunk_embeddings GROUP BY profile_name ORDER BY profile_name"
    ).fetchall()
    return [
        VectorSpaceAnalysis(
            contour="legacy",
            dimensions=None,
            metric=None,
            payload=_payload_for_legacy_space(connection, row["private_space_id"]),
        )
        for row in rows
    ]


def _payload_for_legacy_space(connection: sqlite3.Connection, private_space_id: object) -> PayloadSizeSummary:
    rows = connection.execute(
        "SELECT length(embedding) AS payload_bytes FROM chunk_embeddings "
        "WHERE profile_name = ? AND embedding IS NOT NULL",
        (private_space_id,),
    ).fetchall()
    return PayloadSizeSummary.from_sizes(_payload_sizes(rows))


def _core_vector_spaces(
    connection: sqlite3.Connection,
    tables: set[str],
) -> list[VectorSpaceAnalysis]:
    if not {"core_embedding_spaces", "core_unit_embeddings"} <= tables:
        return []
    rows = connection.execute(
        """
        SELECT
            s.space_id AS private_space_id,
            CASE WHEN typeof(s.dimensions) = 'integer' AND s.dimensions >= 1
                THEN s.dimensions ELSE NULL END AS dimensions,
            CASE WHEN s.metric IN ('cosine', 'dot', 'l2') THEN s.metric ELSE NULL END AS metric
        FROM core_embedding_spaces s
        ORDER BY s.space_id
        """
    ).fetchall()
    return [
        VectorSpaceAnalysis(
            contour="core",
            dimensions=_valid_dimension(row["dimensions"]),
            metric=_valid_metric(row["metric"]),
            payload=_payload_for_core_space(connection, row["private_space_id"]),
        )
        for row in rows
    ]


def _payload_for_core_space(connection: sqlite3.Connection, private_space_id: object) -> PayloadSizeSummary:
    rows = connection.execute(
        "SELECT length(embedding) AS payload_bytes FROM core_unit_embeddings "
        "WHERE space_id = ? AND embedding IS NOT NULL",
        (private_space_id,),
    ).fetchall()
    return PayloadSizeSummary.from_sizes(_payload_sizes(rows))


def _payload_sizes(rows: Sequence[sqlite3.Row]) -> list[int]:
    sizes: list[int] = []
    for row in rows:
        value = row["payload_bytes"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise StorageAnalysisError()
        sizes.append(value)
    return sizes


def _all_vector_payload(
    connection: sqlite3.Connection,
    tables: set[str],
    table: Literal["chunk_embeddings", "core_unit_embeddings"],
) -> PayloadSizeSummary:
    if table not in tables:
        return PayloadSizeSummary.from_sizes(())
    rows = connection.execute(
        f"SELECT length(embedding) AS payload_bytes FROM {table} WHERE embedding IS NOT NULL"
    ).fetchall()
    return PayloadSizeSummary.from_sizes(_payload_sizes(rows))


def _valid_dimension(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise StorageAnalysisError()
    return value


def _valid_metric(value: object) -> Literal["cosine", "dot", "l2"] | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in {"cosine", "dot", "l2"}:
        raise StorageAnalysisError()
    return cast(Literal["cosine", "dot", "l2"], value)


def _duplication(
    records: dict[str, dict[str, int]],
    legacy_payload: PayloadSizeSummary,
    core_payload: PayloadSizeSummary,
) -> dict[str, int | str]:
    legacy_present = any(records["legacy"].values())
    core_present = any(records["core"].values())
    if legacy_present and core_present:
        state = "dual_write"
    elif legacy_present:
        state = "legacy_only"
    elif core_present:
        state = "core_only"
    else:
        state = "empty"
    return {
        "state": state,
        "legacy_vector_count": legacy_payload.count,
        "core_vector_count": core_payload.count,
        "combined_vector_count": legacy_payload.count + core_payload.count,
        "legacy_payload_bytes": legacy_payload.total_bytes,
        "core_payload_bytes": core_payload.total_bytes,
        "combined_payload_bytes": legacy_payload.total_bytes + core_payload.total_bytes,
    }


def _fts_bytes(connection: sqlite3.Connection, tables: set[str]) -> dict[str, bool | int | None]:
    requested = {
        "legacy": "chunks_fts" in tables,
        "core": "core_search_units_fts" in tables,
    }
    values: dict[str, int | None] = {}
    available = True
    for contour, prefix in (("legacy", "chunks_fts"), ("core", "core_search_units_fts")):
        if not requested[contour]:
            values[contour] = 0
            continue
        try:
            value = connection.execute(
                "SELECT COALESCE(SUM(pgsize), 0) FROM dbstat WHERE name GLOB ?",
                (f"{prefix}*",),
            ).fetchone()[0]
        except sqlite3.Error:
            value = None
            available = False
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise StorageAnalysisError()
        values[contour] = value
    return {"available": available, "legacy": values["legacy"], "core": values["core"]}


def _codec_backend_registry(
    connection: sqlite3.Connection,
    tables: set[str],
    legacy_payload: PayloadSizeSummary,
    core_payload: PayloadSizeSummary,
) -> tuple[dict[str, int | str], ...]:
    entries: list[dict[str, int | str]] = []
    if "chunk_embeddings" in tables:
        entries.append(
            {
                "contour": "legacy",
                "codec": "json_utf8",
                "backend": "sqlite_python_exact",
                "vectors": legacy_payload.count,
            }
        )
    if "core_unit_embeddings" in tables:
        if {"mdrack_vector_backends", "mdrack_vector_codecs"} <= tables:
            entries.extend(_v2_codec_backend_registry(connection, core_payload))
        else:
            entries.append(
                {
                    "contour": "core",
                    "codec": "json_utf8",
                    "backend": "sqlite_python_exact",
                    "vectors": core_payload.count,
                }
            )
    return tuple(entries)


def _v2_codec_backend_registry(
    connection: sqlite3.Connection,
    core_payload: PayloadSizeSummary,
) -> tuple[dict[str, int | str], ...]:
    backend_rows = connection.execute(
        "SELECT backend_id FROM mdrack_vector_backends ORDER BY backend_id"
    ).fetchall()
    if (
        len(backend_rows) != 1
        or not isinstance(backend_rows[0][0], str)
        or not backend_rows[0][0]
    ):
        raise StorageAnalysisError()
    backend_id = backend_rows[0][0]
    codec_ids = {
        str(row[0])
        for row in connection.execute("SELECT codec_id FROM mdrack_vector_codecs").fetchall()
        if isinstance(row[0], str) and row[0]
    }
    if not codec_ids:
        raise StorageAnalysisError()
    vector_counts: dict[str, int] = {}
    rows = connection.execute(
        """
        SELECT spaces.metadata_json, COUNT(embeddings.unit_id) AS vector_count
        FROM core_embedding_spaces AS spaces
        LEFT JOIN core_unit_embeddings AS embeddings ON embeddings.space_id = spaces.space_id
        GROUP BY spaces.space_id, spaces.metadata_json
        """
    ).fetchall()
    for row in rows:
        metadata_json, vector_count = row
        if (
            not isinstance(metadata_json, str)
            or isinstance(vector_count, bool)
            or not isinstance(vector_count, int)
        ):
            raise StorageAnalysisError()
        metadata = json.loads(metadata_json)
        if not isinstance(metadata, Mapping):
            raise StorageAnalysisError()
        codec_id = _v2_codec_id_from_metadata(metadata)
        if codec_id not in codec_ids:
            raise StorageAnalysisError()
        vector_counts[codec_id] = vector_counts.get(codec_id, 0) + vector_count
    if sum(vector_counts.values()) != core_payload.count:
        raise StorageAnalysisError()
    return tuple(
        {
            "contour": "core",
            "codec": codec_id,
            "backend": backend_id,
            "vectors": vector_counts[codec_id],
        }
        for codec_id in sorted(vector_counts)
    )


def _v2_codec_id_from_metadata(metadata: Mapping[str, object]) -> str:
    policy = metadata.get("vector_value_policy")
    codec = metadata.get("vector_codec")
    if policy is None and codec in {None, "ieee754-f64-le-v1"}:
        return "ieee754-f64-le-v1"
    if policy == "ieee754-f32-canonical-v1" and codec == "ieee754-f32-le-v1":
        return "ieee754-f32-le-v1"
    raise StorageAnalysisError()
