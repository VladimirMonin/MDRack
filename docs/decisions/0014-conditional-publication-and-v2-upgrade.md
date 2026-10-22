# ADR-0014: conditional publication and explicit recognized app-v2 upgrade

- Status: implemented; independently accepted in the W3 Windows/CPython 3.14.2 source/offline, actual Root and fresh installed synthetic scope; not live-runtime or release acceptance
- Date: 2026-10-22
- Supersedes only the no-upgrade rule for exact completed clean v2/0004
- Core contract: `1.0.0-rc.2`; distributions core/media `1.0.0rc2`, SQLite `1.0.0rc3`, app `1.4.0rc2`

## Decision

Core adds immutable `PublicationState`, `ExpectedPublication`, `ResourceSnapshot`,
`PublicationResult` and optional `PublicationPort`. Existing minimal CatalogPort,
DTOs, retrieval execution/grouping/RRF and media contracts remain intact.
`CoreIndexingService.index_if_current` shares `application.validation` with index.
The former private validator remains a delegation alias, not another owner.

State distinguishes never-created, published, live_without_projections and deleted.
A snapshot owns a read transaction and eagerly reads full graph plus state. CAS
compares the complete expected state under BEGIN IMMEDIATE, then writes graph and
new token atomically. A conflict returns applied=false and current state without
writing. ExpectedPublication defaults to a live expectation; absent/deleted creation
requires create=true and its exact current state. Deleted resources cannot be
resurrected by stale live jobs. Explicit recreate has a fresh lifecycle even with
identical ID/bytes. Tokens are opaque local authority, not content hashes or logs.

Unconditional replacement remains full replacement; every success advances token,
keeping an existing live lifecycle and source binding. Withdrawal removes the graph
but preserves live identity/binding; every successful withdrawal advances token.
Delete ends a live lifecycle even without graph; repeated delete does not mutate
its tombstone. `deleted` is true iff a live lifecycle ended. Minimal tombstones
retain binding/token but discard source metadata. Live source metadata is retained
for compatibility deletion/rescan after withdrawal; it is not searchable content.
No initial empty batch is accepted. Source revision is never inferred from the
old artifact-oriented content_hash. Source guard remains in the conditional port.

SQLite owns one internal graph writer and eager graph reader. Existing export
facade delegates to that reader, without using the standalone v1 opener. App
composition retains the original verified catalog/connection/lifecycle rather than
constructing a second store. FTS rebuild is serialized SQLite-owned repair from
published unit.text only and does not change tokens. Embedding rebuild remains
forced Markdown indexing, not native reprepare-all. Purpose/variant merge, bounded
re-enrichment retry and durable manual artifacts are W4, not this storage contract.

## Schema and maintenance

Family remains `mdrack_sqlite_catalog_v2`; immutable 0000–0004 are unchanged.
`0005_publication.sql` adds independent core_publication_states (no cascading FK
to graph), finite status CHECKs and partial unique live source binding. Upgrade
initializes fresh local lifecycle/token only for extant resources; no deleted
history or raw-source authority is invented. Graph, vector payload/codec,
fingerprints, locators, metadata/facets and FTS are not recomputed.

Normal writable/readonly open never upgrades. Exact old-v2 returns upgrade_required;
unknown/damaged/incomplete/v1/bridge remain unsupported by normal app. Maintenance
requires caller confirmation that ALL consumers are stopped and handles closed.
A SQLite-aware verified backup at an absent path outside store precedes mutation.
Source identity, complete manifest/object fingerprint, graph/FTS, integrity/FK are
checked before backup, on the backup itself, and again under the write transaction.
Every extant resource must reconstruct a complete graph accepted by the shared
I/O-free core validator. An empty resource graph is damage, not withdrawal;
withdrawn/deleted states have no resource row. FTS must contain exactly the eligible
published unit IDs and their original text, without duplicates or extra dense-only
entries. Counts or equivalent lexical matches alone do not establish cleanliness.
These same semantic checks apply to target verification and reopen. No text is
extracted or repaired during verification. DDL, backfill, ledger
and target identity commit together, then target reopen verifies. Managed failures
before commit roll back; existing catalog is never removed by fresh-create cleanup.
Around unknown commit outcome, reopen determines the completed identity. Restore of
backup is explicit offline host action; backup is never a fallback active catalog.
A write lock alone does not prove old processes stopped. Do not restore an old
backup after new user writes without a separately designed preservation procedure.

Public maintenance entries: `MDRackEngine.upgrade_catalog(backup_path, maintenance=True)`
on a fresh engine with no open/injected storage, and
`mdrack --root ./notes rebuild upgrade --backup ../catalog-backup.sqlite3 --maintenance`.
Already-target is verified and returns false without creating backup. Unknown
signature refuses before backup creation. No model/source extraction is involved.

## Compatibility and evidence

Ordered core exports and exact first-party pins/lock consumers advance together;
media is metadata-only repackaging to pin the new core, not a media API revision.
SQLite API version advances to 1.0.0rc2, separately from distribution/schema.
Historical packets and old migration SQL remain immutable. Tests use real current
file catalogs and independent connections/barriers; author execution is not
independent acceptance, full/native release or live-pixels/model-quality evidence.
