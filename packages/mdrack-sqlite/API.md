# mdrack-sqlite API

## Frozen package root

`mdrack_sqlite` exports:

- `SQLITE_CATALOG_API_VERSION` (`1.0.0rc1`);
- `SQLITE_BRIDGE_SCHEMA_ID` (`mdrack-app-core-bridge-v1`);
- `SQLiteCatalog`;
- `SQLiteResourceStore`;
- `SQLiteVerification`;
- `SQLiteCatalogError` and `SQLiteErrorCode`.

## Lifecycle

- `SQLiteCatalog.open(path, *, timeout=5.0)` opens an existing bridge catalog
  read/write without creating or migrating it.
- `SQLiteCatalog.open_readonly(path, *, timeout=5.0)` opens an existing catalog
  with SQLite `mode=ro` and `query_only=ON`. SQLite may maintain WAL/SHM sidecars
  for a WAL-mode database; the catalog never creates or mutates catalog rows.
- `close()` is idempotent and rolls back an unfinished transaction before closing
  an owned connection.
- The context manager returns the catalog, propagates exceptions and always closes.
- `verify()` rejects active transactions and fails closed on integrity, FK, required
  table/index/FK mapping, or manual FTS drift. It returns only safe aggregate counts.

## Concurrency and transactions

A catalog has one sqlite3 thread-bound connection. Writes are serialized by the
adapter. Concurrent readers use separately opened read-only catalogs. Resource
replace/delete own one `BEGIN IMMEDIATE` transaction and reject a caller-owned
transaction. Preflight validation completes before the transaction; failures roll
back the complete graph.

## Errors

Lifecycle errors expose only stable codes. Catalog/search operations retain the
safe `mdrack_core` `CatalogExecutionError`/`BranchExecutionError` categories.
Paths, SQL, query text, vectors, metadata and chained exception messages are not
included in public errors.

## Stage-3A boundary

This package owns the adapter implementation and bridge API only. App migrations
`0000`–`0007` remain immutable and app-owned. Clean package migrations, standalone
schema creation, installed-wheel conformance and app cutover are explicitly not
claimed by this slice.
