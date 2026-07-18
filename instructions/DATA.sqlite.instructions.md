---
applyTo: "src/mdrack/**/*.py"
name: "DATA.SQLite"
description: "When to use: SQLite migrations, schema, repositories, FTS5, vector storage, assets, transactions, logical IDs, or persistence integrity."
---

# SQLite persistence

## Responsibility

Protect schema evolution, transactional indexing, identity semantics, and
cross-table integrity. SQLite is MDRack's only persistent database.

## Migration rules

- Migrations are immutable, uniquely numbered, and contiguous from `0000`.
- Add a new migration for schema changes; never rewrite an applied migration.
- The migration runner must fail closed when files are missing, duplicated, non-contiguous,
  or when the database version is unknown to the running build.
- Apply each migration atomically with its `schema_migrations` record.
- Bump documented schema state and add migration notes whenever a migration is added.
- Verify actual foreign-key actions from SQL; do not infer `CASCADE` from model relationships.
- Before adding a new expected migration, freeze and enforce a compiled expected
  schema version plus the exact ordered migration manifest/digest. Directory
  contiguity alone is not package identity.

## Current persistence responsibilities

- `0000`: migration ledger.
- `0001`: files, sections, chunks, embedding profiles/vectors, runs, diagnostics.
- `0002`: manually maintained content-bearing FTS5 index.
- `0003`: logical IDs and indexing provenance.
- `0004`: complete embedding-profile identity and fingerprints.
- `0005`: immutable historical asset/reference/description tables; current
  Markdown indexing has no production owner for them.
- `0006`: complete chunk offsets and block/chunk kinds.
- `0007`: provider-neutral resources, representations, search units, embedding spaces,
  unit embeddings, facets, resource-facet assignments, FTS, and supporting indexes.

Future migrations extend this ledger; this instruction must be updated when the
current schema advances.

## Implemented v0.3 generation and schema contract

- Build a v0.3 resource index in a separate candidate database/store generation,
  never in the active v0.2 file. A v0.2 build must never be asked to open `0007`.
- Schema version and store readiness are separate. Persist generation identity and
  fail-closed states `legacy_only`, `rebuild_required`, `building`, `ready`, and
  `failed`; only `ready` may serve production search/write.
- Verify and close/checkpoint/fsync the candidate, then atomically switch an
  app-owned active-generation pointer under one-writer quiescence. Readers see old
  or new only. Rollback switches to the untouched retained v0.2 generation.
- Retain the complete old generation read-only for at least one compatibility
  release. Cleanup is a separate explicitly authorized destructive action.
- Migration `0007` was authored after independent schema review mapped every frozen
  core field/invariant to exact DDL, FK action, CHECK, UNIQUE/index, transaction, and
  contract test. Any later schema change requires a new immutable migration and review.
- The create-only `0007` must not mutate/backfill legacy rows or drop legacy tables.
- The accepted schema review settled canonical source identity/rename semantics,
  same-resource graph constraints, explicit delete actions, ordinal/range/type
  checks, NULL-safe facet deduplication, orphan policy, vector codec/finite values/
  dimensions/metrics/fingerprint behavior, and indexes for pre-limit filters.
- `replace_resource()` initially owns one serialized SQLite transaction and rejects
  an active caller transaction. Validation, provider calls, and filesystem work
  finish before it opens; graph/FTS/vector/facet checks commit together; any failure
  preserves the prior complete graph.
- Test coverage must continue to protect candidate path/ID, lock and busy behavior,
  WAL/SHM, reader lifecycle, checkpoint/fsync, atomic switch, interruption recovery,
  retention, and separately authorized cleanup semantics.

## Transaction and integrity invariants

- WAL, foreign keys, and row access remain enabled on canonical connections.
- Replacing one indexed file is atomic: remove stale derived rows, write the new
  file graph, validate counts, then commit or roll back the whole replacement.
- Per-file failures may yield `partial_success`, but must not leave a half-replaced file.
- FTS rows are maintained with chunk writes/deletes; do not assume automatic triggers.
- Embedding profile name, fingerprint, and dimensions must match before vector use.
- Vectors remain JSON-encoded float arrays in SQLite and are scanned in Python.
- Current Markdown indexing treats eligible image alt/alias text as prose only. It
  does not resolve image targets or create, update, or delete rows in the dormant
  `0005` asset/reference tables. Explicit direct-image ingestion persists a typed
  resource through the separate `0007` resource-store transaction.

## Identity rules

- Logical IDs and portable source locators are public/stable identities.
- SQLite record IDs and relationship IDs are implementation details.
- Preserve exact line and half-open character spans through reindexing.
- Do not advertise the raw-ID behavior of legacy `files`/`sections` listing commands
  as the desired public contract; document the asymmetry until it is repaired.

## Safe change process

1. Read the migration runner and every migration touching the affected table.
2. Trace writes, reads, deletes, and integrity checks through the storage port and adapter.
3. Add migration/repository/round-trip and failure-atomicity tests.
4. Update current schema/architecture documentation and this migration ledger.
5. Run the full quality gates and `git diff --check`.
6. For v0.3, require contract-freeze PASS before data design, schema-review PASS
   before SQL, and executable generation rollback review before active cutover.
