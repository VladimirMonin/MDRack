# ADR-0004: SQLite operating envelope and backend neutrality

- **Status:** Accepted for the measured W5-B13 evidence slice; no backend migration selected
- **Date:** 2026-07-20
- **Scope:** provider-free local resource-catalog search in MDRack v0.4
- **Evidence:** [W5-B13 SQLite envelope](../evaluation/w5-sqlite-envelope.md) and its [JSON report](../evaluation/w5-sqlite-envelope.json)

## Context

MDRack's resource catalog currently stores JSON-encoded vectors in SQLite and scans
candidates in Python. The implementation needs a measured operating envelope without
quietly turning incomplete capacity observations into a backend preselection. The
benchmark must remain reproducible, privacy-safe, and independent of LM Studio,
private source data, network services, PostgreSQL, or ANN extensions.

## Decision

### A. Keep SQLite as the current implementation backend

SQLite remains the current and default local persistence adapter. The measured clean
cells establish an operating envelope, not a portable service-level guarantee. The
harness reports wall/CPU p50, p95, p99, isolated-process RSS p50/p95/p99, database
bytes, returned candidate counts, and deterministic fusion overhead.

### B. Use calibrated thresholds as warnings, not backend selection

For the current synthetic local evidence, a cell is bounded/slow when p95 wall time
exceeds 6,000 ms or p95 RSS exceeds 75,000 KiB. These thresholds are review triggers
for a future capacity run; they do not change the public API, select an ANN index, or
name a replacement backend. The measured 10,000 × 1,024 cell crosses the latency
threshold and is explicitly bounded/slow.

### C. Defer backend selection until equivalent cells are complete

The 50,000 and 100,000 unit cells are harness-feasible but unmeasured in this
revision. Incomplete or historical cells cannot select SQLite, PostgreSQL, an ANN
extension, or any other backend. A future backend comparison must run equivalent
cells, operations, warmups, repetitions, privacy checks, and host/configuration
fingerprints before an ADR amendment can select or reject an implementation.

## Consequences

- No backend implementation or schema change is required by this ADR.
- The current adapter has an explicit, conservative local warning envelope.
- Large-cell evidence remains a separately budgeted, disposable benchmark task.
- A future backend experiment must be additive and must not mutate active data.
- The report's `local components` boundary cannot be promoted to installed-package,
  real-source, Windows, live-provider, or relevance-quality evidence.

## Rejected alternatives

- **Preselect PostgreSQL/pgvector:** rejected because equivalent cells and a second
  backend were not measured, and the task explicitly forbids preselection.
- **Claim universal SQLite support limits:** rejected because this is one Linux host,
  synthetic data, and a bounded matrix.
- **Auto-migrate when thresholds are crossed:** rejected; thresholds are evidence
  and review triggers, not runtime backend policy.

## Verification

The report was generated with one warm-up and five repetitions per measured cell:

```text
uv run python scripts/sqlite_envelope_benchmark.py --cells \
  1000x384,10000x384,1000x768,10000x768,1000x1024,10000x1024 \
  --warmups 1 --repetitions 5
```

`uv run ruff check scripts/sqlite_envelope_benchmark.py` and `git diff --check`
passed. The full repository gates and independent review remain downstream gates.
