---
applyTo: "src/**/*.py"
name: "ARCH.System"
description: "When to use: architecture, module boundaries, dependency direction, public CLI/engine surfaces, parser/chunker or retrieval composition."
---

# MDRack system architecture

## Responsibility

Preserve the dependency direction and public boundaries of the local Markdown
indexing and retrieval system.

## Stable boundaries

- `domain/` owns value objects and invariants without CLI, SQLite, HTTP, or Click dependencies.
- `ports/` defines storage and embedding capabilities consumed by application services.
- `application/` orchestrates Markdown indexing, chunking, reads, retrieval, and
  explicit direct-image ingestion primarily
  through ports. The current bounded exception is `IndexingService`: it imports and
  constructs the concrete `MarkdownItParser` default when no parser is injected.
- `adapters/` implements app-specific ports and retains compatibility delegates.
- `packages/mdrack-sqlite/` is the single owner of the generic resource catalog/search
  adapter and its FTS query helper; it depends only on `mdrack-core` and stdlib.
- `storage/sqlite/` retains app-owned legacy connection, migrations, repositories,
  legacy FTS, and vector persistence. The old resource-adapter import path re-exports
  the standalone owner.
- `cli/` is a Click presentation adapter. It may compose services but must not become business logic.
- `public_api/` exposes `MDRackEngine` and DTOs without importing Click.

Canonical services are `IndexingService`, `RetrievalService`, and `ReadService`.
Compatibility modules under legacy parser/chunker/search/indexer paths may remain,
but new behavior belongs in the canonical service path unless a scoped migration
explicitly changes that architecture.

## Invariants

- SQLite remains the only persistent database.
- Default production embeddings remain LM Studio text HTTP, with legacy identity unchanged.
  The explicit trusted outer `mdrack.hosts.cuda_encoder` / CLI composition may inject a
  caller-owned `PreparedEmbeddingProvider` backed by a separately selected bounded process.
  It is never implicit fallback or indexed configuration. Core/application/default providers
  do not load weights/vendor DLLs; host Win32 process/job calls are OS ownership, not model
  loading. Keep producer qualification, complete space identity and lifecycle at that outer
  edge; see `docs/decisions/0018-caller-owned-native-encoder.md`.
- The default parser is the markdown-it adapter and produces parser-independent domain blocks.
- Parser injection still uses the `MarkdownParser` port; do not broaden the concrete
  default-parser exception or describe it as edge-only composition. Moving that default
  to the composition edge requires a separately scoped architecture change.
- Structural chunking owns exact source spans and distinct display/embedding text.
- Markdown image syntax contributes eligible alt/textual alias as prose only; normal
  Markdown indexing neither inspects referenced files nor creates an asset graph.
  Explicit direct-image ingestion is a separate local-file path. Neither path mutates
  Markdown or fetches remote files.
- Text, semantic, and hybrid retrieval converge on the same public result DTO.
- Reusable retrieval execution, branch grouping and weighted RRF live in core.
  The application planner selects policy, branches, scopes, weights and budgets;
  SQLite supplies candidates, not a second fusion implementation.
- Production reranking is unsupported; non-null reranker injection must fail closed.
- Public APIs prefer logical IDs and `SourceLocator`; internal SQLite record IDs are not new public contracts.

## Approved v0.3 transition contract

- The approved reusable boundary is the standalone `mdrack-core` distribution at
  `packages/mdrack-core/`, with its sole import source under
  `packages/mdrack-core/src/mdrack_core/`. The `mdrack` app distribution depends on
  it and remains the compatibility/application owner around the reusable core.
- The approved SQLite boundary is the standalone `mdrack-sqlite` distribution at
  `packages/mdrack-sqlite/`. Its generic API may open recognized bridge/v1 stores,
  but normal app composition uses only `<store>/catalog.sqlite3` and requires the
  independent clean `mdrack_sqlite_catalog_v2` `0000`–`0005` identity. Normal open
  never upgrades. Explicit offline maintenance may upgrade only exact completed
  v2/0004 with verified external backup; legacy v1/bridge remains unsupported.
  Retain the verified catalog through app composition, not a second SQL owner.
- `mdrack_core` is stdlib-only and must not import `mdrack`, Click, HTTP, SQLite,
  Markdown/parser, provider/model, filesystem, or network code.
- Current Markdown IR, `PreparedFile`, `SourceLocator`, `EmbeddingProfile`, and
  public retrieval DTOs remain app compatibility types; do not alias them as core records.
- App/source producers own deterministic logical-ID generation. Core validates
  caller-supplied IDs, uniqueness, batch ownership, and graph relationships only.
- Freeze complete core domain/locator/error/export, catalog/search port, and shared
  observability surfaces before retrieval and indexing implementation lanes diverge.
- Exactly one production owner exists for resource validation, weighted RRF,
  branch grouping, query-vector preparation, ID generation, compatibility mapping,
  migration identity, and active-store switching. Compatibility wrappers delegate.
- Query and index providers remain app-side and pass ready vectors plus explicit
  embedding-space identity to core.
- TEXT-profile admission belongs to the shared application preflight. Service scan
  checks before run/traversal/writes; CLI scan/rebuild/switch must also check before
  external provider/control construction or dispatch. Keep full identity proof,
  unknown app bindings and unrelated typed spaces distinct; confirmations/force do
  not authorize hot migration or metadata adoption. See
  [TEXT profile admission](../docs/current-architecture/indexing-and-chunking.md#text-profile-admission).
- Injected `IndexStorage`/`KnowledgeStorage` must provide the declared authoritative
  `read_text_embedding_spaces` capability, including for empty/providerless scans.
  Canonical records and actual `LegacyTextEmbeddingBinding` rows use the same
  application comparison; unknown is not empty and never enables a disk fallback.
- Activation consumers must inspect the full indexing result status. A performed
  rebuild with partial/failed/missing status or an operation exception must not
  activate config or unload the previous model. Preserve the scan CLI's existing
  partial-result envelope; do not infer full success from truthiness alone.
- Legacy public/import surfaces survive only according to
  `docs/compatibility/v0.3-compatibility-registry.md`; removal requires its exact
  importer, parity, and installed-package oracles.
- Markdown image syntax and explicit direct-image ingestion are separate product
  paths. Markdown may project alt/textual alias once as prose but must not create or
  inspect image resources; direct image ingestion is explicit app behavior.

## Explicit non-goals

Do not add a GUI, web server, MCP server, specialized vector database, cloud
embedding provider, in-process model runtime, or network asset fetcher without an
approved architecture/specification change. The named bounded caller-process composition
above is the explicit additive exception, not authority for another runtime/default.

## Safe change process

1. Trace the affected public entry point through application service, port, and adapter.
2. Inspect sibling CLI and engine call paths for contract parity.
3. Keep provider/database specifics behind ports.
4. Update current architecture/contracts and ADRs when a boundary or limitation changes.
5. Run the gates in `TEST.quality-gates.instructions.md`.
6. For v0.3 work, follow ADR-0002 and the audited phase/ownership graph; return to
   the frozen contract owner if a downstream slice needs to change a shared surface.

## Review questions

- Does domain/application code import Click, sqlite3, or an HTTP client directly?
- Did a CLI-only behavior diverge from `MDRackEngine` without documentation?
- Did internal record identity leak into a new public response?
- Did ranking, chunk ownership, or source-location semantics move to the wrong layer?
