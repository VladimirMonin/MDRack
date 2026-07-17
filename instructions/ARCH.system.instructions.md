---
applyTo: "src/mdrack/**/*.py"
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
- `application/` orchestrates indexing, chunking, assets, reads, and retrieval primarily
  through ports. The current bounded exception is `IndexingService`: it imports and
  constructs the concrete `MarkdownItParser` default when no parser is injected.
- `adapters/` implements ports, including the canonical SQLite composition.
- `storage/sqlite/` owns connection, migrations, repositories, FTS, and vector persistence.
- `cli/` is a Click presentation adapter. It may compose services but must not become business logic.
- `public_api/` exposes `MDRackEngine` and DTOs without importing Click.

Canonical services are `IndexingService`, `RetrievalService`, and `ReadService`.
Compatibility modules under legacy parser/chunker/search/indexer paths may remain,
but new behavior belongs in the canonical service path unless a scoped migration
explicitly changes that architecture.

## Invariants

- SQLite remains the only persistent database.
- LM Studio HTTP is the only production embedding boundary; Python never loads embedding models directly.
- The default parser is the markdown-it adapter and produces parser-independent domain blocks.
- Parser injection still uses the `MarkdownParser` port; do not broaden the concrete
  default-parser exception or describe it as edge-only composition. Moving that default
  to the composition edge requires a separately scoped architecture change.
- Structural chunking owns exact source spans and distinct display/embedding text.
- Asset discovery is local/offline and never mutates Markdown or fetches remote files.
- Text, semantic, and hybrid retrieval converge on the same public result DTO.
- Hybrid fusion and ranking policy live in the application layer, not SQLite.
- Production reranking is unsupported; non-null reranker injection must fail closed.
- Public APIs prefer logical IDs and `SourceLocator`; internal SQLite record IDs are not new public contracts.

## Explicit non-goals

Do not add a GUI, web server, MCP server, specialized vector database, cloud
embedding provider, direct model runtime, or network asset fetcher without an
approved architecture/specification change.

## Safe change process

1. Trace the affected public entry point through application service, port, and adapter.
2. Inspect sibling CLI and engine call paths for contract parity.
3. Keep provider/database specifics behind ports.
4. Update current architecture/contracts and ADRs when a boundary or limitation changes.
5. Run the gates in `TEST.quality-gates.instructions.md`.

## Review questions

- Does domain/application code import Click, sqlite3, or an HTTP client directly?
- Did a CLI-only behavior diverge from `MDRackEngine` without documentation?
- Did internal record identity leak into a new public response?
- Did ranking, chunk ownership, or source-location semantics move to the wrong layer?
