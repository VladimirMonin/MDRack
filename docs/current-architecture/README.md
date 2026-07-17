# MDRack current architecture

This package describes the checked-out MDRack v0.2 implementation. It is the
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

- [System overview](system-overview.md) — layers, dependency direction, component diagram, and source anchors.
- [Indexing and structural chunking](indexing-and-chunking.md) — scan, parsing, identities, exact spans, chunks, embeddings, and atomic replacement.
- [SQLite persistence](sqlite-persistence.md) — migrations `0000`–`0006`, current ER model, transactions, FTS, vectors, and identity.
- [Retrieval](retrieval.md) — text, semantic, hybrid RRF, degradation, and the reranking boundary.
- [Assets](assets.md) — supported image-reference syntax, safe local resolution, searchable text, and persisted provenance.
- [Public interfaces](public-interfaces.md) — CLI capability matrix, embedded engine, class/port diagram, and DTO boundaries.
- [Limitations](limitations.md) — explicit unsupported or asymmetric behavior.

## Supporting current contracts

- [CLI contracts](../cli-contracts.md)
- [ADR-0001: reranking deferred](../decisions/0001-reranking-deferred.md)
- [Recovery and migration procedures](../recovery.md)
- [Windows executable build](../windows-exe-build.md)

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
