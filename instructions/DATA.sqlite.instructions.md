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

## Current persistence responsibilities

- `0000`: migration ledger.
- `0001`: files, sections, chunks, embedding profiles/vectors, runs, diagnostics.
- `0002`: manually maintained content-bearing FTS5 index.
- `0003`: logical IDs and indexing provenance.
- `0004`: complete embedding-profile identity and fingerprints.
- `0005`: assets, references, and reserved descriptions.
- `0006`: complete chunk offsets and block/chunk kinds.

Future migrations extend this ledger; this instruction must be updated when the
current schema advances.

## Transaction and integrity invariants

- WAL, foreign keys, and row access remain enabled on canonical connections.
- Replacing one indexed file is atomic: remove stale derived rows, write the new
  file graph, validate counts, then commit or roll back the whole replacement.
- Per-file failures may yield `partial_success`, but must not leave a half-replaced file.
- FTS rows are maintained with chunk writes/deletes; do not assume automatic triggers.
- Embedding profile name, fingerprint, and dimensions must match before vector use.
- Vectors remain JSON-encoded float arrays in SQLite and are scanned in Python.
- Asset references reject external, absolute, and traversal targets and resolve beneath the configured root.
- Ambiguous chunk-to-asset mapping fails closed.

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
