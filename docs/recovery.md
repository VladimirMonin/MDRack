# Historical: MDRack generation migration and recovery

> This compact archival note replaces the superseded generation/pointer rollout.
> It is not a current operating procedure; it preserves no generation command
> syntax or activation steps.

# MDRack 1.3 store recovery

This is the current recovery boundary for the fixed application catalog. Normal
MDRack uses exactly one database at `<store>/catalog.sqlite3`. It has no
candidate catalog, activation, retention or pointer. Normal open never migrates;
the narrow recognized old-v2 maintenance procedure below is explicit.

Markdown and explicitly supplied media remain source inputs. Indexing does not
rewrite them, and recovery does not make the derived SQLite store a source of
truth for their original bytes.

## Diagnose before changing anything

1. Stop processes that can write the affected store. Keep the source root and
   complete `.mdrack/` directory unchanged while investigating.
2. Save only the privacy-safe outputs of these read-only commands:

   ```bash
   uv run mdrack --root ./notes status
   uv run mdrack --root ./notes doctor
   uv run mdrack --root ./notes storage-analyze
   ```

3. If text search alone is inconsistent and diagnostics otherwise show a healthy
   store, use the explicit derived-data operation:

   ```bash
   uv run mdrack --root ./notes rebuild fts
   ```

   Do not use an FTS rebuild to hide a schema, integrity, or provider/profile
   failure.

The public error envelope and logs intentionally omit private paths, source
content, vectors, credentials, provider bodies, endpoints, metadata values, and
exception text. Preserve the safe error code and aggregate counts instead of the
raw database or logs.

## Preserve a failing fixed store

For a failed integrity, schema, or catalog-opening check, stop writers and copy
the complete store directory, including any SQLite WAL/SHM sidecars, before a
separately authorized destructive action. MDRack does not select a fallback
catalog, read an old `knowledge.db`, or migrate/copy old rows as part of normal
startup.

A later fresh local rebuild must use authorized source inputs and a compatible
embedding profile. It is a new derived-data operation, not a rollback to an old
catalog. Deleting or recreating a catalog is destructive and requires separate
owner authorization.

## Explicit completed app-v2/0004 upgrade

Only the exact clean v2/0004 identity is eligible, not v1/app bridge, interrupted
creation, damaged or unknown schemas. Stop ALL CLI/embedded/background consumers
and close handles first. A write lock alone cannot prove an old process stopped.
Choose an absent backup file outside the entire store directory:

```bash
uv run mdrack --root ./notes rebuild upgrade --backup ../catalog-backup.sqlite3 --maintenance
```

The command verifies the old identity/objects/graph/integrity/FK, makes and verifies
a SQLite-aware backup, rechecks under lock, then atomically applies 0005/state,
ledger and identity. It reopens target after commit. Already-target is verified
and returns upgraded=false without backup creation. Unknown signature refuses
without changing source. Controlled pre-commit failure preserves the old catalog;
existing source is never removed by fresh-create cleanup. Interrupted unknown
commit outcome requires reopen to establish old or target complete identity.

Backup is a recovery artifact, not a fallback catalog. Restore is a separate
explicit offline host operation handling WAL/SHM. Never restore an old backup over
new user changes without a separately designed preservation procedure. Graph,
vector bytes/codecs/fingerprints and published FTS are preserved; no extraction or
encoder runs and old content_hash is not promoted to a raw-source revision.
See [ADR-0014](decisions/0014-conditional-publication-and-v2-upgrade.md).

## Evidence boundaries

`status`, `doctor`, and `storage-analyze` establish only local catalog health.
Offline fake-provider and temporary fixture evidence do not establish live LM
Studio behavior, real-source usefulness, Windows behavior, or production model
quality. For the bounded synthetic store check, see
[one-store acceptance evidence](one-store-acceptance.md).
