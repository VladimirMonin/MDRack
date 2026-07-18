# System overview

MDRack now has standalone `mdrack-core` and `mdrack-sqlite` distributions beside
the `mdrack` app. `mdrack_core` is the stdlib-only provider- and
persistence-neutral kernel. `mdrack_sqlite` depends only on core and stdlib and is
the single generic resource catalog/search adapter owner. `mdrack` owns
Click/engine composition, Markdown and explicit image ingestion, app migration
generations, and LM Studio HTTP integration.

## Dependency direction

```mermaid
graph TD
    subgraph Entry ["Public entry points"]
        CLI["Click CLI"]
        Engine["MDRackEngine"]
    end

    subgraph App ["Application services"]
        Indexing["IndexingService"]
        Retrieval["RetrievalService"]
        Read["ReadService"]
    end

    subgraph Core ["mdrack_core (stdlib only)"]
        Domain["Resources, units, vectors, search DTOs"]
        Ports["Catalog and lexical/vector ports"]
        CoreServices["Indexing, retrieval, grouping, weighted RRF"]
    end

    subgraph Adapters ["Adapters"]
        Markdown["markdown-it parser"]
        SQLiteAdapter["mdrack_sqlite resource adapter"]
        SQLiteLegacy["mdrack legacy adapter + migrations"]
        LMStudio["LM Studio HTTP provider"]
    end

    subgraph Persistence ["Persistent store"]
        SQLite["SQLite: legacy 0000-0006 and resource 0007"]
    end

    CLI --> App
    Engine --> App
    App --> Domain
    App --> Ports
    CoreServices --> Domain
    CoreServices --> Ports
    Indexing -->|"current default-parser exception"| Markdown
    SQLiteAdapter -->|"implements storage ports"| Ports
    LMStudio -->|"implements embedding ports"| Ports
    SQLiteAdapter --> SQLite
    SQLiteLegacy --> SQLite

    classDef entry fill:#fff5ad,stroke:#d4c46a,color:#333
    classDef service fill:#4ecdc4,stroke:#0a9396,color:#fff
    classDef contract fill:#ff6b6b,stroke:#c92a2a,color:#fff
    classDef adapter fill:#e1f5fe,stroke:#01579b,color:#333
    class CLI,Engine entry
    class Indexing,Retrieval,Read,CoreServices service
    class Domain,Ports contract
    class Markdown,SQLiteAdapter,SQLiteLegacy,LMStudio,SQLite adapter
```

Unlabelled arrows show runtime dependencies, not inheritance; labelled
adapter-to-port arrows show implementation direction. `mdrack_core` never imports
`mdrack`, Click, SQLite, HTTP, Markdown, filesystem, or provider code.
`mdrack_sqlite` imports `mdrack_core` but never `mdrack`. The app prepares
caller-owned IDs, text and vectors before invoking core.
The current bounded exception is `IndexingService`: it imports and constructs
`MarkdownItParser` when no parser is injected. Callers can still inject the
`MarkdownParser` port; this concrete default is not an edge-only composition.

## Layers and ownership

| Layer | Current responsibility |
|---|---|
| `domain/` | Parser-independent documents and blocks, chunks, logical identities, source locators, profiles, and retrieval DTOs. |
| `ports/` | Storage, parser, embedding, model-catalog, lifecycle, and reranker contracts. |
| `application/` | Canonical Markdown indexing, chunking, reads, and text/semantic/hybrid orchestration. |
| `adapters/` | markdown-it parsing, app SQLite compatibility/composition, and LM Studio-specific adapters. |
| `storage/sqlite/` | App-owned connections, immutable migration history, repositories, and legacy FTS/vector operations. |
| `packages/mdrack-sqlite/` | Generic `core_*` catalog/search adapter, FTS fallback, context-managed bridge lifecycle, and safe verification. |
| `cli/` | Click argument handling, service composition, error mapping, and JSON envelopes. |
| `public_api/` | `MDRackEngine` and public DTO access without a Click dependency. |
| `mdrack_core/domain/` | Immutable generic resource, locator, vector, facet, request/result, error, and degradation records. |
| `mdrack_core/ports/` | Logical-ID-only catalog and lexical/vector search protocols. |
| `mdrack_core/application/` | Complete-graph validation, provider-free indexing, grouping, weighted RRF, and discovery. |
| `application/store_generations.py` | Durable generation state and active-pointer records. |
| `packages/mdrack-sqlite/src/mdrack_sqlite/resource_store.py` | Atomic `core_*` resource graph and pre-limit scoped search implementation. |
| `src/mdrack/adapters/sqlite/resource_store.py` | Compatibility re-export of the standalone owner. |

The canonical service path is `IndexingService`, `RetrievalService`, and
`ReadService`. `SearchService`, the old `markdown/` parser/chunker, the
`indexing/indexer.py` wrapper, and thin `search/` modules are compatibility
surfaces rather than the preferred home for new behavior.

## Fixed architecture boundaries

- SQLite is the only persistent database; there is no vector database or
  `sqlite-vec` dependency.
- Production embeddings use the LM Studio HTTP boundary. MDRack does not load
  model weights through Python ML libraries.
- The default parser is `markdown_it`; `legacy` remains selectable for baseline
  comparisons.
- Markdown image syntax is projected only into eligible prose; Markdown indexing
  creates no asset graph and never inspects referenced files. Explicit direct-image
  ingestion is a separate caller-selected local-file operation. Neither path fetches
  remote assets or mutates source files.
- Hybrid fusion is implemented in the application layer.
- Production reranking is unsupported and non-null injection fails closed.
- Public retrieval identity is a logical ID plus `SourceLocator`, not a SQLite
  record UUID.
- Only a verified `ready` resource generation can serve core-backed writes/search;
  the active legacy database remains on migration `0006`.

## Primary source anchors

- Entry points: `src/mdrack/cli/__init__.py`, `src/mdrack/public_api/engine.py`
- Pure core distribution/source: `packages/mdrack-core/`,
  `packages/mdrack-core/src/mdrack_core/`
- SQLite distribution/source: `packages/mdrack-sqlite/`,
  `packages/mdrack-sqlite/src/mdrack_sqlite/`
- Store generations: `src/mdrack/application/generation_manager.py`,
  `src/mdrack/application/store_generations.py`
- Resource adapter compatibility import: `src/mdrack/adapters/sqlite/resource_store.py`
- Services: `src/mdrack/application/indexing.py`,
  `src/mdrack/application/retrieval.py`, `src/mdrack/application/query.py`
- Ports: `src/mdrack/ports/storage.py`, `src/mdrack/ports/embeddings.py`
- Default composition: `src/mdrack/adapters/sqlite/index_storage.py`
- Project invariants: `AGENTS.md`, `instructions/ARCH.system.instructions.md`
