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

## Agent workflow and private data

MDRack is normally operated by an agent under a human's explicit authority. The
agent must distinguish a source-checkout command (`uv run mdrack`) from a separately
installed `mdrack` command, obtain an explicit `ROOT`, and name every operation that
will alter derived state before it runs. `guide`, `--help`, text search, `status`,
and `doctor` are suitable read-only discovery steps; initialization, scanning,
explicit ingest, rebuild, deletion, and provider/model actions are not.

Keep private human material outside the repository checkout. An authorized workflow
may scan Markdown below the selected root or ingest exactly selected raw
text/Markdown, direct-image, WAVE, or ISO-BMFF inputs. The WAVE and ISO-BMFF paths
need their explicit authorization flags and a caller-selected local executable; do
not silently substitute a tool, provider, or remote service. Search with text mode
and, when useful, a unified scope. Retain logical IDs and portable locators or
available line/time bounds rather than copying source text or paths into an issue.

After a requested write completes, start a fresh CLI process with the same root and
run `status`. It reopens the existing derived store and reports aggregate state;
also verify independently that selected source bytes did not change. This is a
real agent-use workflow only when the installed public CLI or engine is exercised
on separately authorized human-like private data. Synthetic fixtures, fake
providers, and offline checks remain supporting evidence and must not be reported
as that real-use boundary.

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
