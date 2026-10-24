---
applyTo: "**"
name: "TEST.QualityGates"
description: "When to use: tests, fixtures, regressions, verification commands, release evidence, schema/contract checks, or claims that a change passes."
---

# Quality gates and evidence

## Responsibility

Define the minimum reproducible verification and evidence standard for MDRack changes.

## Required repository gates

Run from the repository root, in order:

```bash
uv run pytest
uv run ruff check src/ tests/
uv run python scripts/check_no_forbidden_deps.py
git diff --check
```

Use narrower tests during development, but run the full gates before final acceptance
unless the task explicitly limits verification. Report the exact command, outcome,
and any skipped gate with a concrete reason.

## Change-specific obligations

- Parser/chunker: exact source-span ownership, LF/CRLF, Unicode, malformed input,
  stable identities, and byte-exact reconstruction where applicable.
- Retrieval/public DTO: text/semantic/hybrid parity across CLI and `MDRackEngine`,
  scores/ranks, logical IDs, heading arrays, source locators, degradation, pagination.
- SQLite/migrations: fresh database, upgrade path, fail-closed migration discovery,
  foreign keys, atomic replacement, FTS/vector/profile/resource integrity, and the
  dormant historical status of legacy `0005` asset tables.
- Markdown images: eligible alt/textual alias appears once as prose; referenced files
  are never resolved, inspected, or diagnosed; no asset graph/reference or image
  resource is created; source bytes and stable IDs remain unchanged.
- Direct images: explicit local-file ingestion, deterministic resource identity,
  typed text/visual spaces, atomic replacement/delete, pre-limit filters, and safe
  missing/inaccessible-path failures across CLI and `MDRackEngine`.
- Documentation: relative links, heading anchors, Mermaid syntax/rendering or waiver,
  instruction routing/frontmatter, and `git diff --check`.
- Performance: distinguish verified catalog open, lazy engine construction, already-open
  injected search and normal per-operation read sessions; retain per-call connection
  counts, encoding absence/cost and separate serialization timing. An injected warm
  result does not qualify the default route or concurrent snapshot safety. State sample
  count and cache definition; small-series percentiles are descriptive, not production
  tail bounds. Without an applicable predeclared workload/profile SLO, retain measurements
  only rather than inventing a performance PASS or subtracting unpaired stage medians.
- Accelerator evidence: bind source/executable, runtime/weights, recipe and selected
  device. Offload/allocation logs alone do not prove execution: observe actual evaluated
  operation outputs in each claimed real stage with device checks and finite nonzero
  synchronous readback. State sampled-node limits and CPU preparation/normalization;
  do not promote an external-host canary to integrated engine/catalog/CLI or quality/SLO
  acceptance. Keep raw exits and cleanup observations even when ordinary native stderr
  is nonempty; current absence never supplies a missing historical exit.
- Packaging/public CLI: use installed-package or live CLI checks when the claim crosses
  beyond unit-level source behavior. Keep normal PEP 517 build isolation; offline builds
  need an explicitly provisioned, identity-checked backend wheelhouse, not a switch that
  silently removes isolation. Source/callable CLI checks and native console-launcher
  checks are different boundaries; do not attribute Python `-S` startup to a normal PE.

## Test design

- Prefer public behavior and stable invariants over incidental implementation details.
- Every repaired semantic defect gets a regression test that fails for the original cause.
- Golden fixtures must state what is stable and must not hide gaps, stripped separators,
  nondeterminism, or lossy normalization. A shared directory does not merge versioned
  freezes: use `uv run python scripts/check_v11_input_freeze.py --check` for the original
  v1 inputs plus the exact named v2 additions; do not regenerate the historical manifest.
- Public-surface ledgers distinguish registered membership from catalog-backed ownership.
  Check the real route and a genuine catalog-backed sibling; a retained host-artifact read
  may legitimately work without an application catalog.
- Fakes prove orchestration only. They do not prove LM Studio, filesystem, package,
  or external runtime integration.
- Do not weaken an assertion merely to accommodate current output; first decide whether
  the output violates the contract.

## Safe execution and observable outcomes

Inspect plugins, bootstrap, config and fixtures before first import/collection. Set
owned temporary/cache roots before that boundary; a later tmp_path fixture does not
isolate early reads/writes. Do not disable required guards for a green result. Install
cleanup/finalizers immediately after acquire, including partial setup, exceptions and
cancellation; never clean another owner's resources.

Native/helper release evidence must bind the owned process handle, creation identity
and actual awaited exit. A numeric PID can be reused: its later presence is not an
owned-process leak or permission to kill it, and later absence cannot supply an unknown
historical exit. Preserve the original failure and distinguish identity-qualified
release from a bare-PID observation.

Use focused RED → minimal authorized implementation → the same GREEN test for a new
behavior/regression. Preserve actual command, assertion/source identity and first failure;
a fixture/setup error is not product RED. Verify observable bytes/state/readback/reopen
and the next operation where lifecycle matters, not only called/count/done. Fakes replace
external edges, not a second implementation of the subject. Preserve raw subprocess exit
through wrappers. Exit 0 with skip/blocked cells is not PASS of those cells; report exact
NOT_RUN boundaries. Reuse unchanged source-bound evidence by impact, not by report date.

Documentation CLI tests use disposable synthetic projects and cover positive/refusal,
selected/unselected bodies, no-write/check, fixed updater target, manual-byte/date
preservation and byte/mtime no-op. Wrapper/CI development skips only historical release
identity, never the other applicable gates; strict/default validation remains strict.

## Evidence language

Use these boundaries explicitly:

- `unit/offline`: isolated code with fakes or fixtures;
- `local components`: real local SQLite/filesystem/provider process as stated;
- `installed package`: built/installed artifact exercised outside the source import path;
- `real source`: separately authorized source corpus with before/after and sanitized evidence;
- `live external`: real external service/data with guarded side effects and cleanup.
- `Windows`: commands actually executed on Windows.

Never promote a PASS from one boundary to a stronger one. A green command is evidence
only for the revision and environment where it ran.

For v0.3, deterministic fake extraction/vectors plus real local SQLite/filesystem
fixtures are offline/local orchestration evidence only. They do not prove live
OCR/caption/visual quality, installed artifacts, a real vault, Windows, or an
external runtime. Installed-wheel imports are mandatory for compatibility surfaces;
source-tree imports alone are insufficient.

## v0.3 contract-plan gates

- Core boundary: standalone/copy import, forbidden import/identifier scan, and no
  reverse `mdrack_core -> mdrack` edge.
- Compatibility: exact CLI envelope and DTO key/alias/rank/nullability/locator
  tests, CLI/engine parity, importer inventory, and installed-wheel imports from
  `docs/compatibility/v0.3-compatibility-registry.md`.
- Fixed app store: expected v2 migration manifest, exclusive fresh creation,
  concurrent open, interrupted-create cleanup, v1/bridge and mixed-store rejection,
  reopen/read visibility, integrity/FK checks, and exactly one main SQLite file.
- Adapter contracts: the same memory and SQLite suite for replace/read/delete,
  injected failure atomicity, filters before limits, vector spaces/metrics,
  facets/hash, stable ordering, and no internal IDs.
- Markdown policy: full parser/chunker/indexing/storage removal oracle,
  filesystem spy, source hashes, stable IDs, and production-symbol scan.
- Privacy: scan logs, stdout, stderr, diagnostics, and eval/support/release JSON for
  supplied query/content/path/root/endpoint/vector/metadata/facet/exception sentinels
  across success, empty, degradation, failure, and cleanup branches.
- Release claims: separately record fake/offline, local components, installed
  package, real source, live external, and Windows outcomes plus explicit non-claims.
