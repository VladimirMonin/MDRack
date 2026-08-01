# One-store acceptance evidence

This is the current maintainer runbook for a bounded offline acceptance pass over
MDRack's canonical fresh-store fixture. It is not a normal application command,
a store migration procedure, or a release-publication claim.

## Scope

`scripts/run_one_store_acceptance.py` runs the fixed
`tests/fixtures/one_store_v1/` contract through the public Click CLI and
`MDRackEngine`. The selected checks cover:

- the current public CLI/engine inventory and the absence of a normal
  alternate-catalog path;
- creation, reopen, inspection, and foreign-key/integrity checks for exactly
  one `catalog.sqlite3` store;
- Markdown scan/read/search plus explicit image, transcript, and video ingest;
- every frozen `queries.json` search case, the 16 textual
  resource-similarity cells, deterministic ordering, and source fixture hashes;
- resource export/delete/reopen and CLI-to-engine retrieval parity;
- privacy sentinels across successful, degraded, failure, and cleanup paths;
- a temporary wheel-target import check for `mdrack`, `mdrack-core`,
  `mdrack-media`, and `mdrack-sqlite`.

The runner never reads a private vault or user source. It uses temporary
fixtures, fake embeddings, local SQLite/filesystem components, and temporary
wheel targets only.

## Run it

Choose a disposable directory outside the checkout. The runner owns only its
`latest/` child. Every run that can write to a valid evidence root replaces that
child: success publishes passing evidence, while a failure publishes a bounded
failed-state pack so an older successful `latest/` cannot be mistaken for the
current result. Its acceptance check marks whether a previous `latest/` was
invalidated:

```bash
evidence_root="$(mktemp -d /tmp/mdrack-one-store-evidence.XXXXXX)"
uv run python scripts/run_one_store_acceptance.py --evidence-root "$evidence_root"
python -m json.tool "$evidence_root/latest/manifest.json"
```

Stdout is exactly one JSON result object. The generated `latest/` directory has
only these privacy-safe files:

- `summary.json` — test count, source hashes, evidence boundaries, and the
  installed-wheel result;
- `runner.log` — closed-vocabulary lifecycle events;
- `manifest.json` — file hashes and a privacy-scan result. Its own hash is
  deliberately excluded to avoid a self-referential manifest.

Raw pytest output, command arguments, source content, vectors, local paths,
provider bodies, endpoints, and exception text are not retained as evidence.
A privacy-sentinel finding fails the run rather than publishing the value.

For a short local development iteration only, `--skip-installed-package` omits
the wheel-target import check. It is not the full acceptance command and must
be labeled as such in any report.

## Lifecycle evidence

`runner.log` uses the frozen `mdrack_core.observability.SafeEvent` schema.
Events use one opaque SHA-256 `execution_id` across catalog, indexing/ingestion,
and retrieval observations. Each terminal event records the elapsed milliseconds
and passed-check count for its controlled pytest stage. The catalog, indexing,
ingestion, and successful retrieval stages exercise named public fixture paths.
The `degraded` and `failed` retrieval events are emitted only after their
controlled provider-unavailable/provider-failure stages pass; `recovered` is
emitted only after the later healthy hybrid retrieval stage passes. They are not
synthetic events inferred from an unrelated all-pass batch.

The only visible operation categories are `acceptance`, `catalog`, `indexing`,
`ingestion`, and `retrieval`; reasons are closed categories such as
`dependency_unavailable` and `dependency_failed`. Known validation, integrity,
and dependency failures retain only their closed reason. Unexpected
exceptions remain unclassified rather than being mislabeled. Failed evidence
contains no exception text or raw execution value.

This makes the offline path inspectable without putting private inputs in logs.
It does not make the runner a production telemetry service and does not add a
new Click or engine API.

## Evidence boundary

A successful runner proves only Linux local/offline behavior for the exact
checked-out revision and fixture. It does not prove real-source usefulness,
live LM Studio/provider behavior, model quality, OCR/Whisper/VLM behavior,
Windows, package publication, deployment, or production latency. Record those
boundaries separately if they are later authorized and executed.
