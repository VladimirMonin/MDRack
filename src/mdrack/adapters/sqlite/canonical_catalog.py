"""Fixed one-store lifecycle for the application's SQLite catalog."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from mdrack_sqlite import SQLiteCatalog, SQLiteCatalogError, SQLiteErrorCode
from mdrack_sqlite.contract_v2 import SQLITE_CATALOG_V2_SCHEMA_ID

CATALOG_FILENAME = "catalog.sqlite3"
_LEGACY_LIFECYCLE_ARTIFACTS = (
    "knowledge.db",
    "active-generation.json",
    "generations",
)
_RACE_OPEN_TIMEOUT_SECONDS = 2.0
_RACE_OPEN_INTERVAL_SECONDS = 0.02
_SQLITE_HEADER = b"SQLite format 3\x00"
_SQLITE_MAIN_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})


class ApplicationStoreError(RuntimeError):
    """A configured application store cannot be safely opened."""


def application_store_dir(root: Path, config: Any) -> Path:
    """Resolve the configured store directory without creating it."""
    store_value = getattr(getattr(config, "paths", None), "store", ".mdrack")
    store_path = Path(store_value)
    return store_path if store_path.is_absolute() else root / store_path


def canonical_catalog_path(root: Path, config: Any) -> Path:
    """Return the only catalog location supported by normal application flows."""
    return application_store_dir(root, config) / CATALOG_FILENAME


def open_application_catalog(
    root: Path,
    config: Any,
    *,
    create: bool,
) -> SQLiteCatalog:
    """Open the fixed catalog or create it on the first writable operation.

    ``SQLiteCatalog.create_v2`` reserves the final path with ``O_EXCL`` and
    removes an incomplete file on failure. A concurrent opener retries only
    after that reservation becomes a valid catalog; no candidate or legacy
    database is consulted.
    """
    store_dir = application_store_dir(root, config)
    catalog_path = store_dir / CATALOG_FILENAME
    _reject_forbidden_store_topology(store_dir, catalog_path)
    if catalog_path.is_file():
        return _open_verified_v2(catalog_path)
    if catalog_path.exists():
        raise ApplicationStoreError("catalog_path_invalid")
    if not create:
        raise ApplicationStoreError("catalog_missing")

    try:
        store_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ApplicationStoreError("store_directory_unavailable") from exc

    try:
        return SQLiteCatalog.create_v2(catalog_path)
    except SQLiteCatalogError as exc:
        if exc.code is not SQLiteErrorCode.DATABASE_EXISTS:
            raise ApplicationStoreError("catalog_create_failed") from exc
    return _open_verified_v2(catalog_path)


def open_application_catalog_readonly(root: Path, config: Any) -> SQLiteCatalog:
    """Open the fixed catalog read-only after the same topology/v2 checks."""
    store_dir = application_store_dir(root, config)
    catalog_path = store_dir / CATALOG_FILENAME
    _reject_forbidden_store_topology(store_dir, catalog_path)
    if not catalog_path.is_file():
        if catalog_path.exists():
            raise ApplicationStoreError("catalog_path_invalid")
        raise ApplicationStoreError("catalog_missing")
    return _open_verified_v2(catalog_path, readonly=True)


def _reject_forbidden_store_topology(store_dir: Path, catalog_path: Path) -> None:
    if not store_dir.exists():
        return
    if store_dir.is_symlink() or catalog_path.is_symlink():
        raise ApplicationStoreError("catalog_path_invalid")
    for artifact_name in _LEGACY_LIFECYCLE_ARTIFACTS:
        if (store_dir / artifact_name).exists():
            raise ApplicationStoreError("legacy_store_unsupported")

    try:
        sqlite_paths = tuple(
            path
            for path in store_dir.rglob("*")
            if path.is_file()
            and path != catalog_path
            and _looks_like_sqlite_main_path(path)
        )
    except OSError as exc:
        raise ApplicationStoreError("store_topology_unavailable") from exc
    if sqlite_paths:
        raise ApplicationStoreError("multiple_sqlite_stores_unsupported")


def _looks_like_sqlite_main_path(path: Path) -> bool:
    if path.suffix.lower() in _SQLITE_MAIN_SUFFIXES:
        return True
    with path.open("rb") as stream:
        return stream.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER


def _open_verified_v2(catalog_path: Path, *, readonly: bool = False) -> SQLiteCatalog:
    deadline = time.monotonic() + _RACE_OPEN_TIMEOUT_SECONDS
    while True:
        try:
            catalog = (
                SQLiteCatalog.open_readonly(catalog_path)
                if readonly
                else SQLiteCatalog.open(catalog_path)
            )
        except SQLiteCatalogError as exc:
            if time.monotonic() >= deadline:
                raise ApplicationStoreError("catalog_open_after_create_failed") from exc
            time.sleep(_RACE_OPEN_INTERVAL_SECONDS)
            continue
        if catalog.schema_id != SQLITE_CATALOG_V2_SCHEMA_ID:
            catalog.close()
            raise ApplicationStoreError("catalog_schema_unsupported")
        return catalog


__all__ = [
    "ApplicationStoreError",
    "CATALOG_FILENAME",
    "application_store_dir",
    "canonical_catalog_path",
    "open_application_catalog",
    "open_application_catalog_readonly",
]
