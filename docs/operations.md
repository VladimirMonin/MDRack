# MDRack operations and troubleshooting

This runbook covers ordinary local operation. MDRack stores derived SQLite state;
Markdown and explicitly supplied media/image inputs remain read-only sources.
Normal startup uses only `<store>/catalog.sqlite3`; it has no generation,
candidate, activation, rollback, retention, or old-store migration mode. For a
bounded offline acceptance pass over synthetic fixtures, use
[one-store acceptance evidence](one-store-acceptance.md); it is not a normal
operational command or a recovery procedure.

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
The normal command surface has no alternate-catalog or generation-management
commands. `rebuild fts` and `rebuild embeddings` update only derived search data
in the fixed catalog.

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
migration versions, non-v2 schemas, and mixed SQLite store layouts fail closed.

### The fixed catalog fails validation or integrity checks

Stop all writers and preserve the complete store directory (catalog plus any
WAL/SHM sidecars) for diagnosis. Normal MDRack does not select a replacement
catalog or migrate old data. Cleanup or recreation is destructive and separately
authorized; use the privacy-safe `status`, `doctor`, and `storage-analyze` output
to report the failure without sharing the store itself.

### A command exposes an unexpected identifier or capability

Consult [CLI contracts](cli-contracts.md) and the
[public interface matrix](current-architecture/public-interfaces.md). `files`,
`read`, resource, and search contracts use logical IDs and portable locators;
the removed `sections` command has no normal-operation contract. See
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
