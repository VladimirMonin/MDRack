# mdrack-sqlite API

## Frozen package root

`mdrack_sqlite` exports:

- `SQLITE_CATALOG_API_VERSION` (`1.0.0rc2`);
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
  Verification owns a read transaction: all checks and counts observe one committed
  state, including during `open`/`open_readonly`. Each resource's complete graph is
  reconstructed by the common eager reader and checked by the shared I/O-free core
  validator. FTS must exactly match eligible published unit IDs/text, not merely
  row counts or tokenized search equivalence. Empty published graphs and extra
  dense-only FTS entries are rejected; legitimate withdrawn/deleted states have no
  resource row. Verification never extracts text or repairs damaged data. The
  active-caller-transaction rejection remains unchanged.

## Concurrency and transactions

A catalog has one sqlite3 thread-bound connection. Writes are serialized by the
adapter. Concurrent readers use separately opened read-only catalogs. Resource
replace/delete own one `BEGIN IMMEDIATE` transaction and reject a caller-owned
transaction. Preflight validation completes before the transaction; failures roll
back the complete graph.

Unreleased internal application integration uses an adapter-owned read scope plus
an operation-owned readonly reader factory. It pins one snapshot for space
resolution, branches, filtering, fusion and eager result hydration after query
provider preparation. No transaction spans HTTP or a later independent read.
The internal scope is not a new exported standalone API or a required method on
legacy core catalog ports. Standalone/injected services without the optional app
factory retain their legacy consistency boundary; a catalog context manager alone
owns lifetime, not a multi-call search snapshot. Each concurrent app operation
needs its own reader. Schema, result shapes and package exports are unchanged.

The app capability is `mdrack.ports.search_sessions.SearchReadSessionFactory`;
its reader combines read/search/space-resolution protocols without write methods.
Normal application composition supplies it to UnifiedText, compatibility retrieval,
resource presets, timed/image search, metadata lexical search and resource discovery,
including the direct CLI consumers. App-side provider preparation precedes entry;
subsequent independent unit reads use a new operation rather than that old session.

## Publication and explicit v2 maintenance

Current app-v2 uses compiled schema 0005 (0000–0004 unchanged). `read_snapshot(id)`
owns a read transaction and rejects an active external transaction. `export_graph`
eagerly reconstructs the same graph and can join an adapter-owned read scope.
`compare_and_publish(batch, ExpectedPublication(state))`, `withdraw_if_current` and
`delete_if_current` atomically compare and return PublicationResult(applied,state).
Absent/deleted creation requires explicit create=True with the exact observed state.
Unconditional replace/delete also advance state. Repeated delete preserves tombstone.
`delete_live_resource` returns whether a live lifecycle ended, including withdrawal.
`rebuild_fts_index` repairs only published unit.text without changing lifecycle/token.

`SQLiteCatalog.upgrade_v2(path, backup_path, maintenance=True)` is an explicit
maintenance boundary for exact completed old-v2/0004 only. Normal open returns
upgrade_required, never migrates. All consumers must first stop and close handles.
A verified SQLite backup outside store precedes atomic DDL/state/ledger/identity;
rollback never removes an existing catalog. Target reopen verifies; already-target
returns False. V1/bridge/unknown/incomplete are not guessed into an upgrade.
See [ADR-0014](../../docs/decisions/0014-conditional-publication-and-v2-upgrade.md).

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

Generic v1/bridge compatibility remains separate from normal app-v2. Only the
explicit recognized old-v2 maintenance transition above is supported; no automatic
migration, generation switching, retained-data cleanup or native quality claim.
