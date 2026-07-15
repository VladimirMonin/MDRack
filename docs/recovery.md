# Migration, Reindex and Recovery

MDRack stores all persistent state in the configured SQLite store. Source Markdown and referenced assets are read-only inputs and are never recovery targets.

## Before an upgrade

1. Stop MDRack commands that write the same store.
2. Copy the complete store directory, including `knowledge.db`, `knowledge.db-wal`, and `knowledge.db-shm` when present.
3. Keep the configured root and store mapping unchanged until verification finishes.
4. Run `scripts/verify.sh` on Linux or `scripts/verify.ps1` on Windows.

Migrations are forward-only, contiguous, and transactional. Duplicate, missing, malformed, or unknown future migration versions fail closed before pending schema changes are applied. MDRack does not silently downgrade a newer database.

## Reindex

Use a full rebuild when the parser backend, chunk strategy version, or active embedding profile changes incompatibly:

```bash
uv run mdrack --root /path/to/vault rebuild all --provider fake
```

Use the real provider only in an explicitly authorized LIVE session with LM Studio GUI/server available. A model or output-dimension change creates a different embedding profile fingerprint; incompatible vectors are rejected rather than mixed.

## Recovery

If migration or indexing fails:

1. Preserve the failed store for diagnosis.
2. Restore the complete pre-upgrade store directory, not only the main `.db` file.
3. Run `uv run mdrack --root /path/to/vault doctor`.
4. Retry with the same parser/chunk/profile configuration, or create a new empty store and run a full reindex.

Per-file indexing uses a savepoint. A failed replacement rolls back the file's chunks, vectors, assets, and asset references together, leaving the previous indexed version available. Asset rows no longer referenced by any document are removed during successful replacement/deletion.

## Asset limitations

The offline asset foundation resolves only root-contained relative references. External URLs and traversal/absolute paths are retained as unresolved references but are never fetched or opened. Search text comes only from image alt text and adjacent document text. No OCR, vision model, visual embedding, network request, or asset mutation occurs.

## LIVE evaluation boundary

`scripts/live_lmstudio_eval.py` is intentionally guarded. Without `--confirm-live` it exits with status 2 and `calls_attempted: 0`. In this offline stage, even the confirmation path performs no LM Studio call; implementation and execution belong to the dedicated LIVE stage.
