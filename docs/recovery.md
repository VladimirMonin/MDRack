# Historical: MDRack generation migration and recovery

> This compact archival note replaces the superseded generation/pointer rollout.
> It is not a current operating procedure; it preserves no generation command
> syntax or activation steps.

# MDRack 1.3 store recovery

This is the current recovery boundary for the fixed application catalog. Normal
MDRack uses exactly one database at `<store>/catalog.sqlite3`. It has no
candidate catalog, activation, rollback, retention, pointer, or old-store
migration procedure.

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

## Evidence boundaries

`status`, `doctor`, and `storage-analyze` establish only local catalog health.
Offline fake-provider and temporary fixture evidence do not establish live LM
Studio behavior, real-source usefulness, Windows behavior, or production model
quality. For the bounded synthetic store check, see
[one-store acceptance evidence](one-store-acceptance.md).
