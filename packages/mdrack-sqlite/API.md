# mdrack-sqlite API

## Frozen package root

`mdrack_sqlite` exports:

- `SQLITE_CATALOG_API_VERSION` (`1.0.0rc1`);
- `SQLITE_BRIDGE_SCHEMA_ID` (`mdrack-app-core-bridge-v1`);
- `SQLITE_CATALOG_SCHEMA_ID` (`mdrack_sqlite_catalog_v1`);
- `SQLITE_CATALOG_SCHEMA_VERSION` (`0003`);
- `SQLITE_MIGRATION_MANIFEST` and `SQLITE_MIGRATION_MANIFEST_DIGEST`;
- `SQLiteCatalog`;
- `SQLiteResourceStore`;
- `SQLiteVerification`;
- `SQLiteCatalogError` and `SQLiteErrorCode`.

## Lifecycle

- `SQLiteCatalog.create(path, *, timeout=5.0)` exclusively creates a clean catalog;
  an existing path is never opened, truncated, or adopted.
- `SQLiteCatalog.open(path, *, timeout=5.0)` opens an existing clean or frozen
  bridge catalog read/write without creating or migrating it. Identity and
  integrity are verified before WAL mode can be changed.
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

## Clean migration identity

The clean package history is independent and contiguous:

| Version | Filename | SHA-256 |
|---|---|---|
| `0000` | `0000_identity.sql` | `079a67a9e90d423e6e020fe37cbb0829818a6dbbe5d9a5b1817a249c46a6e3c1` |
| `0001` | `0001_catalog.sql` | `5df6b532ffa5247dd08dc0200830d0371b97abd447dc0b81cd5936ca634ac60a` |
| `0002` | `0002_vectors_facets.sql` | `09e8d9a60722ee4c87515e2e1173305db6f6406add7c9ed2e42a685f36ace7b6` |
| `0003` | `0003_search.sql` | `be76b2774d51c344f01b4a497ba91ebd8b4de02c01838cbecd316852708fb00b` |

The ordered `sha256-framed-v1` manifest digest is
`bc0e36bd47ed2fab4a1a0614c431a35ac0e70d23f1f27a222ea4bd72bb997b4c`.
Each SQL file and ledger row commit atomically. Package bytes, the exact ledger,
schema ID/version/digest, schema objects, indexes, foreign keys, integrity and FTS
projection all fail closed on drift. App migrations `0000`–`0007` remain immutable
and app-owned; combined app `0007` is bridge compatibility only.

Clean-catalog verification also compares a compiled normalized `sqlite_master`
fingerprint derived from immutable migrations `0000`–`0003`. Added, changed, or
removed application tables, views, indexes, and triggers return the stable safe
`verify_failed` code. SQLite-owned `sqlite_*` objects are derived internals; the
five exact FTS5 shadow tables are checked as a fixed inventory and excluded from
the DDL fingerprint. A merely similar `core_search_units_fts_*` name is not a
shadow object and is rejected. This exact-schema check applies only to the clean
identity; frozen app-bridge verification behavior is unchanged.

## Failure matrix

- missing path: safe `open_failed`/`readonly_open_failed`, no creation;
- existing create target: `database_exists`, no mutation;
- foreign, future, gapped or tampered identity: `schema_mismatch`;
- invalid packaged history or migration execution: `migration_failed`;
- schema/index/FK/FTS/integrity corruption: `verify_failed`;
- lock/busy during catalog writes: core `adapter_timeout`;
- read-only writes and other adapter failures: privacy-safe core categories.

This package does not adopt the clean catalog as the app default, migrate active
data, switch generations, clean retained data, or claim Windows/backend cutover.
