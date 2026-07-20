# ADR-0010: Clean standalone SQLite catalog

- **Status:** Accepted and implemented for the `mdrack-sqlite` 1.0 release candidate
- **Date:** 2026-07-20
- **Scope:** standalone generic resource catalog identity, migrations, and lifecycle
- **Related:** ADR-0004 measures the operating envelope; this ADR owns schema and lifecycle identity
- **Evidence:** `packages/mdrack-sqlite/API.md` and `tests/integration/test_sqlite_clean_migrations.py`

## Context

A reusable resource catalog must not inherit the application database's Markdown tables or migration history. It needs a clean, immutable schema identity that can be installed and consumed independently while remaining compatible with the frozen core records.

## Decision

`mdrack_sqlite` owns the standalone schema ID `mdrack_sqlite_catalog_v1`, schema version `0003`, and the exact ordered migration manifest `0000`–`0003` with a framed digest. `SQLiteCatalog.create()` exclusively creates a new catalog and refuses any existing path. `open()` and `open_readonly()` require an existing verified clean or frozen bridge catalog and never create or migrate it.

Each migration file and ledger row is immutable and applied atomically. Open and verification fail closed on unknown, missing, reordered, or tampered history; schema/index/foreign-key/FTS/integrity drift; and unmanifested application objects. Resource replacement and deletion own one serialized transaction and preserve the previous complete graph on failure.

The app-owned `0000`–`0007` history remains separate. Its `0007` schema is a compatibility bridge, not the standalone package's migration identity.

## Consequences

- The standalone package contains only generic resource/catalog structures, not legacy Markdown `files`, `chunks`, or `assets` tables.
- Existing databases are never silently adopted, truncated, or upgraded by the clean lifecycle.
- SQLite remains the sole persistent backend; ADR-0004 records measured limits without selecting a replacement.
- This decision does not migrate active application data, switch generations, delete retained data, or claim Windows evidence.
