# MDRack current architecture

This package describes the checked-out MDRack 1.4.0rc2 source candidate.
The app-owned [native image contract](../contracts/native-image-v1.md) adds typed
routes; production pixels remain unsupported, with runtime/quality acceptance
separate from deterministic offline contract evidence. It is the
maintainer entry point for current module boundaries, indexing, persistence,
retrieval, asset handling, public interfaces, and known limitations.

## Source of truth

When sources disagree, use this order:

1. executed behavior and current tests;
2. SQL migrations and current domain, port, application, and adapter code;
3. Click CLI and `MDRackEngine` public contracts;
4. this package and [CLI contracts](../cli-contracts.md);
5. historical plans and superseded design notes.

## Architecture map

- [System overview](system-overview.md) — layers, dependency direction, app-owned local-file adapters, prepared-resource boundaries, component diagram, and source anchors.
- [Indexing and structural chunking](indexing-and-chunking.md) — scan, parsing, identities, exact spans, chunks, embeddings, and atomic replacement.
- [SQLite persistence](sqlite-persistence.md) — the single clean-v2 application catalog, historical schemas, transactions, FTS, vectors, and identity.
- [Retrieval](retrieval.md) — text, semantic, hybrid RRF, degradation, and the reranking boundary.
- [Images](assets.md) — Markdown text projection versus explicit direct-image ingestion.
- [Public interfaces](public-interfaces.md) — CLI capability matrix, explicit local-file adapter boundaries, embedded engine, class/port diagram, and DTO boundaries.
- [Limitations](limitations.md) — explicit unsupported or asymmetric behavior, including raw-adapter and external-process limits.

## Supporting current contracts

- [Ready OCR publication v1](../contracts/ready-ocr-v1.md) — app-owned merge, caller custody, source binding, normal image rescan and explicit limits.
- [ADR-0015: ready OCR publication](../decisions/0015-ready-ocr-publication.md)

- [Getting started](../getting-started.md)
- [Operations and troubleshooting](../operations.md)
- [Development guide](../development.md)
- [CLI contracts](../cli-contracts.md)
- [v1.2 unified text search](../contracts/v1.2-unified-search.md)
- [MDRack 1.3.0 release notes and source-publication status](../release-1.3.md)
- [ADR-0001: reranking deferred](../decisions/0001-reranking-deferred.md)
- [ADR-0002: provider/storage-neutral core](../decisions/0002-provider-storage-neutral-core.md)
- [ADR-0003: core 1.0 RC contract](../decisions/0003-core-1.0-rc-contract.md)
- [ADR-0004: SQLite operating envelope](../decisions/0004-sqlite-operating-envelope.md)
- [ADR-0005: timed-text granularity](../decisions/0005-timed-text-granularity.md)
- [ADR-0006: temporal locator specification](../decisions/0006-temporal-locator-specification.md)
- [ADR-0007: no-overlap default](../decisions/0007-no-overlap-default.md)
- [ADR-0008: whole-resource text aggregation](../decisions/0008-whole-resource-text-aggregation.md)
- [ADR-0009: text-only media capability](../decisions/0009-text-only-media-capability.md)
- [ADR-0010: clean standalone SQLite catalog](../decisions/0010-clean-standalone-sqlite-catalog.md)
- [v0.3 compatibility registry](../compatibility/v0.3-compatibility-registry.md)
- [v0.3 release evidence](../evidence/v0.3-release-gate.md)
- [W5-B13 SQLite envelope](../evaluation/w5-sqlite-envelope.md)
- [Windows executable build](../windows-exe-build.md)
- [One-store acceptance evidence](../one-store-acceptance.md) — bounded fixture runner, safe lifecycle evidence, and installed-wheel boundary.
- [Offline release verification](../offline-release-verification.md) and [W5-CI contract](../contracts/v0.4-w5-ci-contract.md)
- [v0.4 offline release-candidate packet](../evidence/v0.4-release-packet.md) and [public-surface compatibility ledger](../compatibility/v0.4-public-surface-ledger.json)

## Operation-owned search snapshots

The current unreleased application source supplies
[`SearchReadSessionFactory`](../../src/mdrack/ports/search_sessions.py) through
[`ApplicationSearchReadSessions`](../../src/mdrack/adapters/sqlite/canonical_catalog.py).
Each ordinary application search opens its own verified readonly v2 connection.
Catalog and compatibility projection share that connection and one read snapshot
for catalog-dependent space resolution/canonicalization, branches, filters,
fallback, fusion and eager result hydration. Results are materialized before the
reader closes. Readers also close on error or cancellation; concurrent operations
do not share a reader handle.

This covers Markdown text/semantic/hybrid retrieval, UnifiedText, resource presets,
image/timed search, metadata body/metadata lexical branches and resource discovery,
including registered CLI consumers. Query-provider preparation finishes before
session entry. Ordinary hybrid search rejects empty/whitespace queries before
provider preparation or reader entry. Verified open and public `verify()` use a
separate consistent preflight snapshot; the active-caller-transaction guard remains.
See the [SQLite transaction contract](../../packages/mdrack-sqlite/API.md#concurrency-and-transactions).

The guarantee is one logical search response, not a later independent read/enrich
request, a filesystem snapshot or CAS/source-revision validation. Injected legacy
storage without the optional factory retains its own consistency boundary; it is
not silently replaced with a disk reader. Qualification is Windows/CPython 3.14.2
source/local SQLite with synthetic vectors and MockTransport, not installed-package,
native-media or model-quality acceptance. Further bounds are in
[search response consistency](limitations.md#search-response-consistency).

## Conditional publication and recognized v2 maintenance

[ADR-0014](../decisions/0014-conditional-publication-and-v2-upgrade.md) defines the
optional neutral core publication contract and current app-v2/0005. SQLite owns
snapshot graph+state, conditional/unconditional graph writing, withdrawal/deletion
and FTS repair. App composition retains its verified catalog. Explicit old-v2/0004
upgrade requires offline maintenance and verified backup outside store; normal open
never migrates. See [recovery](../recovery.md) and [SQLite API](../../packages/mdrack-sqlite/API.md).
The first [ready-OCR family](../contracts/ready-ocr-v1.md), including purpose-aware
merge and normal image rescan, is independently accepted on Windows/CPython 3.14.2
source/local components and non-editable installed packages. Caller-owned retention
is verified; host archival and the remaining W4 families are still open, not storage proof.

## Future / proposed

[Native media and late enrichment](../proposals/native-media/README.md) is the full
proposed 01–04 design bundle and implementation programme. The search-snapshot
foundation above is implemented in unreleased source; it does not make native-media
full native-media or late enrichment current implemented contracts.

## Historical material

Files under `docs/plans/` and legacy planning documents such as `docs/plan.md`,
`docs/remediation-plan.md`, `docs/chunking-refactor-plan.md`,
`docs/model-management-plan-3.md`, and
`docs/mdrack-v0.2-retrieval-modernization-plan.md` record implementation history.
They are not current architecture contracts unless an individual file is explicitly
marked as the active plan for an in-progress task.

The older [architecture](../architecture.md),
[storage design](../storage-design.md), and
[retrieval design](../retrieval-design.md) documents are retained as historical
context and route maintainers back to this package.

[Store recovery](../recovery.md) documents the current fixed
`catalog.sqlite3` operational boundary. It does not prescribe a generation,
candidate, activation or retention procedure. Only the explicit recognized old-v2
maintenance transition above is supported, not legacy-store adoption.
