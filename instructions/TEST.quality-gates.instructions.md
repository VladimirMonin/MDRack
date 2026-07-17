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
  foreign keys, atomic replacement, FTS/vector/profile/asset integrity.
- Assets: traversal/external rejection, existing/missing status, deduplication, and
  unambiguous asset-to-chunk ownership.
- Documentation: relative links, heading anchors, Mermaid syntax/rendering or waiver,
  instruction routing/frontmatter, and `git diff --check`.
- Packaging/public CLI: use installed-package or live CLI checks when the claim crosses
  beyond unit-level source behavior.

## Test design

- Prefer public behavior and stable invariants over incidental implementation details.
- Every repaired semantic defect gets a regression test that fails for the original cause.
- Golden fixtures must state what is stable and must not hide gaps, stripped separators,
  nondeterminism, or lossy normalization.
- Fakes prove orchestration only. They do not prove LM Studio, filesystem, package,
  or external runtime integration.
- Do not weaken an assertion merely to accommodate current output; first decide whether
  the output violates the contract.

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
- Storage generations: expected migration manifest, fresh/populated migration,
  non-ready fail-closed states, candidate interruption, atomic pointer switch,
  reader old/new visibility, rollback, and separate cleanup authorization.
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
