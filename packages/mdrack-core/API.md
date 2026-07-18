# mdrack-core API

The exact ordered exports are machine-frozen by the MDRack repository's
`tests/core/test_contract_freeze.py` oracle. Import only the public surfaces below.

## Package root: `mdrack_core`

The package root exports:

- core contract identity (`CORE_CONTRACT_VERSION`);
- domain records, constants and error/degradation categories;
- catalog and search ports;
- safe observability records and helpers.

The root intentionally does not re-export application services.

## `mdrack_core.application`

- `CoreIndexingService`: validates and atomically replaces or deletes one prepared
  resource graph through a catalog port.
- `RetrievalService`: executes lexical/vector branches, applies categorical
  branch-local narrowing, groups evidence and returns deterministic weighted-RRF
  results.
- `ResourceDiscoveryService`: exact duplicate and provider-free whole-resource
  similarity orchestration.
- `weighted_rrf` and its fusion records: deterministic fusion primitives retained
  for compatibility.

## `mdrack_core.domain`

Immutable records cover resources, representations, search units, embedding
spaces, vectors, facets, locators, prepared batches, search requests/results,
similarity requests/results and degradation/error categories.

Important semantics:

- callers provide logical IDs and ready vectors;
- `Locator` is an opaque canonical JSON envelope and is never opened by core;
- adapter candidate ranks are dense and one-based;
- ordinary retrieval result scores are weighted RRF, even for one branch;
- similarity results retain adapter-raw score semantics and an explicit opaque
  similarity basis;
- branch overrides narrow only categorical scope fields; global facet clauses stay
  global.

## `mdrack_core.ports`

- `ResourceWritePort`: replace/delete prepared resource graphs.
- `ResourceReadPort`: read resources, units, vectors and exact-hash matches.
- `LexicalSearchPort` and `VectorSearchPort`: candidate generation after scope.
- `CatalogPort` and `SearchPort`: combined protocol aliases.

Implementations may be in-memory, SQLite-backed, or host-specific, but those
implementations and dependencies do not belong in this distribution.

## Compatibility policy

`1.0.0rc1` is a release candidate. Any RC export change requires an ADR, updated
ordered export snapshot, compatibility evidence and a migration note. Final 1.x
removal policy begins only after a final `1.0.0` contract is declared.
