# MDRack operations and troubleshooting

This runbook covers ordinary local operation. MDRack stores derived SQLite state;
Markdown and explicitly supplied media/image inputs remain read-only sources.

## Routine commands

Run commands from the repository checkout with `uv run` and select the source root
explicitly:

```bash
uv run mdrack --root ./notes init
uv run mdrack --root ./notes scan
uv run mdrack --root ./notes status
uv run mdrack --root ./notes doctor
uv run mdrack --root ./notes search "query" --mode text
```

Use `mdrack <command> --help` before an uncommon or destructive-looking operation.
`storage rebuild-fresh`, `storage verify`, and `storage activate` form a separate,
explicit one-way generation cutover. They are not part of routine scan/search and
must follow the [recovery procedure](recovery.md).

## Output, logs, and privacy

The CLI reserves stdout for one documented JSON object. Application logs go to
stderr and use stable events, reason categories, counts, lengths, dimensions, and
durations where possible. Keep these streams separate when automating:

```bash
uv run mdrack --root ./notes status >status.json 2>status.log
```

Do not publish raw logs or diagnostic output without inspection. Queries, Markdown
or OCR/caption text, vectors, credentials, provider bodies, raw endpoints,
absolute/private paths, metadata/facet values, and private exception strings are
sensitive. `doctor`, evaluation output, support bundles, and release evidence obey
the same privacy boundary; they are not exceptions.

The root `.mdrack/` directory, SQLite files, logs, caches, and local reports are
generated/private state and must not be committed.

## Diagnose common failures

### Configuration fails before the command

An explicit missing, unreadable, or invalid `--config-file` returns
`CONFIG_ERROR` without echoing the private path or parser exception. Check TOML
syntax and precedence:

1. built-in defaults;
2. `<root>/.mdrack/config.toml` or explicit `--config-file`;
3. `MDRACK_<SECTION>_<FIELD>` variables;
4. command-line overrides.

Run `uv run mdrack --root ./notes status` after correction. See
[getting started](getting-started.md#configure-lm-studio) for a minimal config.

### LM Studio or semantic search fails

Text search is provider-free, so first isolate storage/index health:

```bash
uv run mdrack --root ./notes search "query" --mode text
uv run mdrack --root ./notes status
uv run mdrack --root ./notes doctor
```

Then check `uv run mdrack model --help` and verify that the configured endpoint,
model identity, output dimensions, and profile match the running LM Studio
instance. Do not paste provider bodies or raw endpoint details into an issue.
Changing model or dimensions requires an explicit embedding rebuild.

### Text search or FTS is inconsistent

Preserve the store first, stop writers, and inspect diagnostics. If the SQLite
store is otherwise healthy, the explicit command is:

```bash
uv run mdrack --root ./notes rebuild fts
```

Do not delete the database to hide a migration or integrity failure. Unknown
migration versions and invalid generation pointers fail closed.

### A fresh candidate fails or activation is uncertain

Do not activate `building`, `failed`, `rebuild_required`, or `legacy_only` state.
Only a verified `ready` clean v2 candidate is eligible. Preserve the complete
store directory (database, WAL/SHM, metadata, and active pointer), stop all writers,
and follow [recovery and migration procedures](recovery.md). Cleanup is destructive
and separately authorized; retained generations are not removed automatically.

### A command exposes an unexpected identifier or capability

Consult [CLI contracts](cli-contracts.md) and the
[public interface matrix](current-architecture/public-interfaces.md). Legacy
`files` and `sections` commands still expose internal SQLite identities; new
resource/search contracts use logical IDs and portable locators. See
[current limitations](current-architecture/limitations.md) before treating a
missing GUI, server, reranker, visual search, ANN, or remote provider as a defect.

## Safe issue report

Include:

- MDRack version and command name (not private arguments);
- stable JSON error code and safe reason category;
- Python/OS version and whether the boundary was source checkout, installed package,
  local component, or live provider;
- aggregate counts/dimensions and reproducible sanitized steps;
- whether source hashes remained unchanged.

Exclude source content, private paths, database files, vectors, credentials, model
request/response bodies, raw URLs/endpoints, and `.mdrack/` contents.
