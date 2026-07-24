# Migration, Store Generations, Rollback and Recovery

MDRack stores derived state in SQLite. Markdown and explicitly ingested image
files are read-only inputs and are never recovery targets.

## Safety boundaries

- Stop every writer for the selected store before backup, activation, or rollback.
- Preserve the complete store directory, including database, WAL/SHM, generation
  metadata, and active pointer. Copying one `.db` file is not a complete backup.
- A fresh compact candidate is a clean `mdrack_sqlite_catalog_v2` database. Do not
  use an existing v1/app-bridge `0007` database as a rebuild source or target.
- Never activate `building`, `failed`, `rebuild_required`, or `legacy_only` as a
  resource generation. Only a verified `ready` candidate is eligible.
- Cleanup is destructive, separately authorized, and not part of rollback.

## Before an upgrade

1. Quiesce commands/processes that can write the store and close long-lived readers.
2. Copy the complete store directory and verify the copy can be listed/read.
3. Record the current active generation ID and retain the previous generation
   read-only for diagnosis and preservation; do not plan a runtime rollback to it.
4. Run `scripts/verify.sh` on Linux. The PowerShell script is provided for Windows,
   but Linux execution is not evidence that Windows passed.
5. Build the release artifacts and run `scripts/check_installed_package.py` from an
   isolated installed wheel outside the source tree.

## Candidate build and activation

The MDRack 1.3.0 compact resource index is rebuilt into a separate clean v2
candidate generation. Active legacy bytes are neither migrated, copied, nor
backfilled. The default candidate records canonical `ieee754-f32-le-v1` vectors
and uses the builtin exact backend.

1. Create the candidate exclusively and persist `building` metadata.
2. Apply the independent compiled v2 migration manifest through `0004`.
3. Rebuild the complete resource graph from the authorized source using the same
   producer/profile configuration intended for serving.
4. Verify migration identity, foreign keys, canonical resource/unit/vector/facet
   records, graph counts, manual FTS consistency, producer fingerprints, and
   resource contract version.
5. Checkpoint, close, fsync the candidate database and directory, then persist
   verified `ready` metadata durably.
6. Under one-writer quiescence, atomically replace and fsync the active-generation
   pointer using the explicit one-way activation. Existing readers may finish on
   the old generation; new readers resolve the new one.
7. Run `mdrack status` and `mdrack doctor`; retain only their privacy-safe output.

An interruption before pointer replacement leaves the old generation active. An
interruption after durable replacement recovers from the new pointer. Missing,
corrupt, non-ready, or manifest-mismatched pointers fail closed.

The CLI sequence is explicit: run `storage rebuild-fresh`, verify the returned
generation ID with `storage verify GENERATION_ID`, and only then run `storage
activate GENERATION_ID`. The rebuild command accepts only the `float32` codec and
the builtin backend; it reparses authorized source Markdown instead of reading an
old database.

## No runtime rollback for fresh v2 cutover

Fresh-v2 activation is one-way. The runtime refuses to promote a non-v2 candidate
and does not switch the active pointer back to a retained legacy generation.
If activation fails before durable pointer replacement, the former pointer remains
active. If a post-activation operational decision requires a different catalog,
stop writers, preserve the failed generation, and perform a separately authorized
fresh rebuild/cutover; do not modify source files, reverse migrations, copy rows,
or treat the retained database as a rollback target.

Normal application composition, `mdrack status`, and `mdrack doctor` resolve an
existing managed store through the same verified active pointer. A resource
generation must be verified `ready`; diagnostics read the resolved generation
database rather than an unselected `knowledge.db`. Invalid or missing managed
pointers fail closed without inspecting a fallback database.

This is consumption of an already registered and verified pointer, not automatic
adoption. A clean store with no pointer continues to use `knowledge.db`. MDRack does
not discover, register, copy, rebuild, activate, roll back, or remove generations as
a side effect of normal composition or diagnostics.

## Candidate or indexing failure

- A failed candidate never becomes active and records only a stable reason code.
- Core validates a complete resource graph before the SQLite transaction opens.
- Resource replacement/deletion commits resource children, manual FTS, vectors,
  facets, and integrity together. Any failure preserves the previous complete graph.
- Legacy per-file indexing remains savepoint-atomic for its compatibility tables.
- Preserve failing artifacts and use safe diagnostics; never paste raw paths,
  content, vectors, endpoints, provider bodies, or private exception text into
  support/recovery/release evidence.

## Model/profile changes

Embedding space identity includes dimensions, metric, and fingerprint. A model or
dimension change must produce a new compatible space/profile and rebuild derived
vectors; incompatible vectors fail closed rather than mix. Use a live provider only
when separately authorized. Offline fake evidence is not live-provider evidence.

## Evidence boundaries

Release evidence labels are exact:

- `unit/offline`: isolated tests and deterministic fakes;
- `local components`: explicitly named local SQLite/filesystem components;
- `installed package`: wheel installed and exercised outside source imports;
- `real source`, `live external`, and `Windows`: only when separately authorized
  and actually executed.

The MDRack 1.3.0 base release packet makes no claim for a real vault/source corpus,
live LM Studio/OCR/caption/visual runtime, external network, or Windows execution.