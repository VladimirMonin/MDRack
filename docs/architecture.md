# MDRack Architecture

## Project Overview

MDRack is a local command-line Markdown knowledge rack for AI agents. It indexes Markdown files, splits them into meaningful structural chunks, stores metadata and search indexes in SQLite, creates embeddings through LM Studio only, and allows agents to search, inspect, and retrieve document context through stable JSON commands.

**Technology Stack:**
- Pure Python 3.10+
- SQLite for all persistent storage
- Click for CLI
- Pydantic for configuration
- LM Studio HTTP API for embeddings and model lifecycle control
- FTS5 for full-text search
- Custom vector search (cosine similarity in Python)

## System Layers

```
┌─────────────────────────────────────────────────────────────┐
│                       CLI Layer                             │
│  (Click commands: scan, search, read, status, rebuild)    │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                     Commands Layer                          │
│  (Command implementations, error handling, JSON output)   │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                    Services Layer                          │
│  • Markdown processing (parse → sections → chunks)        │
│  • Indexing pipeline (scan → change detection)           │
│  • Search (text, semantic, hybrid)                        │
│  • Diagnostics & health checks                            │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                    Storage Layer                           │
│  • SQLite connection & migrations                         │
│  • Repositories (files, sections, chunks)                │
│  • FTS5 virtual table                                    │
│  • Vector index (JSON embeddings)                        │
└─────────────────────────────────────────────────────────────┘
```

## Module Responsibilities

### `config/`
- Settings loading from TOML + environment variables
- Pydantic models include `ParsingConfig` and token-/structure-aware `ChunkingConfig`
- `[parsing].backend` selects `markdown_it` (default) or `legacy` for A/B runs
- Default values and type validation
- Config file location: `.mdrack/config.toml`

### `cli/`
- Main Click group with global options: `--root`, `--config-file`, `--json`
- Subcommand registration: `scan`, `search`, `status`, `doctor`, `rebuild`, `model`, `read` (group), `files` (group), `sections` (group)
- Centralized error handling via JSON envelope
- Commands delegate to service layer

### Markdown parsing and chunking
- **`ports/parser.py`**: replaceable `MarkdownParser` boundary
- **`adapters/markdown_it/`**: CommonMark/GFM parser with H1–H6, source maps, tables, both fence styles, callouts, images, Obsidian embeds, and YAML frontmatter
- **`domain/blocks.py` / `domain/documents.py`**: stable parser-independent, lossless `Document` / `SourceBlock` IR with deterministic block IDs and source spans
- **`application/chunking.py`**: creates distinct `RetrievalChunk`s; only prose uses sentence/word splitting, while code, tables, and Mermaid use structure-specific line/row policies
- `display_content` remains separate from heading-aware `embedding_text`; every retrieval chunk carries parent block IDs and source spans
- **`domain/assets.py` / `application/assets.py`**: root-relative asset identities and lossless references for Markdown images, Obsidian embeds, and HTML `img`; raw references stay separate from stable asset IDs and exact source spans
- Asset resolution is offline and fail-closed: traversal/absolute/external targets are never opened, while searchable image chunks contain only alt and adjacent text
- **`markdown/`** remains the selectable legacy parser/chunker baseline for A/B evaluation

### `storage/sqlite/`
- **`connection.py`**: Factory `get_connection()` — enables WAL, foreign keys, `row_factory = sqlite3.Row`
- **`migrations.py`**: Custom migration runner — applies `NNNN_name.sql` files in order, tracks in `schema_migrations`
- Migration histories must be unique and contiguous from `0000`; unknown future database versions fail closed
- **`repositories.py`**: CRUD queries for `files`, `sections`, `chunks`; also `get_neighbors()`, `count_*()` helpers
- **`fts.py`**: FTS5 operations — `upsert_fts()`, `delete_fts()`, `search_fts()` (with snippet), `rebuild_fts()`
- **`vector.py`**: `VectorIndex` class — stores embeddings as JSON blobs in `chunk_embeddings`, computes cosine similarity in pure Python, supports `upsert()`, `search()`, `delete()`, `count()`

### `embeddings/`
- **`ports/embeddings.py`**: canonical typed `EmbeddingProvider`; `embeddings/protocol.py` remains a compatibility export
- **`ports/reranker.py`**, **`ports/model_catalog.py`**, and **`ports/model_lifecycle.py`**: independent reranking, discovery, and lifecycle roles
- **`domain/profiles.py`**: complete embedding identity and stable fingerprint over provider, runtime, model key/family, quantization, output dimensions, query instruction, normalization, and endpoint family
- Reduced output dimensions remain an explicit capability result; offline configuration never claims live MRL support
- **`lmstudio.py`**: LM Studio provider and control client — `/v1/embeddings` for vectors, `/api/v1/models*` for model list/load/unload/download, async, configurable endpoint/timeout/dimensions
- **`runtime.py`**: shared provider/control-client construction and safe async cleanup helpers
- **`fake.py`**: Deterministic fake provider for testing (hash-based vectors)
- **`adapters/lmstudio/fakes.py`**: transport-free deterministic catalog, lifecycle, and reranker adapters for offline tests
- **`hashing.py`**: Text hashing utilities for embedding cache keys

### `indexing/`
- **`scanner.py`**: `scan_markdown_files(root, include, exclude)` — walks filesystem, applies glob patterns (`**/*.md` by default), excludes `.git`, `node_modules`, `.venv`, `.mdrack`, `tests/**`
- **`change_detector.py`**: `detect_changes(conn, current_files, root)` — compares disk SHA-256 vs. `source_hash` in DB → `ChangePlan` (new/changed/unchanged/deleted)
- **`indexer.py`**: end-to-end indexing pipeline — scan, change detection,
  parse, section build, chunk build, embed, persist, and record diagnostics

### `search/`
- **`text.py`**: legacy FTS5 compatibility wrapper with provenance enrichment.
- **`semantic.py`** and **`hybrid.py`**: thin compatibility wrappers over the
  canonical application service.
- **`adapters/sqlite/index_storage.py`**: DB-backed normalized text and semantic
  candidates with public logical IDs and complete source locators.
- Public locators include root-relative path, heading path, line/offset spans,
  block/chunk kinds, and logical block/chunk IDs; SQLite UUIDs remain internal.
  Exact-content moves preserve document and fragment identities, while
  replacement/deletion removes stale FTS, vector, and asset rows.
- **`application/retrieval.py`**: the only text/semantic/hybrid orchestration
  path and the only RRF implementation used at runtime. Production v0.2 has no
  reranker call; `rerank_rank` and `rerank_score` remain null.

### `diagnostics/`
- **`integrity.py`**: `get_store_status(conn)` — returns files_count, chunks_count, embeddings_count, active_profile, schema_version, and active profile metadata
- **`doctor.py`**: comprehensive health checks for FTS coverage, embeddings,
  stale vectors, schema migration status, and config/profile mismatch

### `output/`
- **`envelope.py`**: `success(payload, command)` and `error(message, code, command, details)` — standardize JSON output structure
- **`errors.py`**: Exception hierarchy — `MDRackError` (base), `ConfigError`, `StorageError`, `EmbeddingError`, `SearchError` with error codes

## Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│  1. SCAN                                                   │
│  scan_markdown_files(root) → list[Path] (relative paths)  │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  2. PARSE                                                 │
│  MarkdownParser.parse(...) → Document IR                  │
│    • Lossless SourceBlock + SourceSpan                    │
│    • H1–H6 heading paths and stable block IDs             │
│    • Metadata and source_hash (SHA-256)                   │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  3. STRUCTURAL CHUNKS                                    │
│  StructuralChunker.build(Document) → RetrievalChunk      │
│    • Character and estimated-token limits                │
│    • Prose-only semantic boundaries and local overlap    │
│    • Code line, table row, and Mermaid line policies     │
│    • Parent block IDs, source spans, heading paths       │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  5. EMBED                                                │
│  provider.embed(texts) for chunk embedding_text          │
│    • embedding_text: content + context from neighbors    │
│    • Store in chunk_embeddings (chunk_id, profile_name) │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  6. STORE                                                │
│  Repositories:                                            │
│    • upsert_file()                                       │
│    • upsert_section()                                   │
│    • upsert_chunk()                                     │
│    • persist assets + exact source references           │
│    • upsert_fts() for FTS5                              │
│    • VectorIndex.upsert() for embeddings                │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  7. SEARCH                                               │
│  • SQLite adapter → normalized FTS/vector candidates     │
│  • RetrievalService → text, semantic, or RRF hybrid      │
│  • CLI and MDRackEngine → identical public result DTOs   │
└─────────────────────────────────────────────────────────────┘
```

## Constraints

- **No GUI / Web server**: Pure CLI tool for AI agents via JSON output
- **SQLite only**: No external database, no vector DB (Qdrant/Chroma/LanceDB forbidden)
- **LM Studio only**: Embeddings via HTTP API; no local ML libraries (`torch`, `sentence-transformers`, `transformers` banned)
- **Single-threaded**: No async DB operations (embeddings are async, storage is sync)
- **Stateless commands**: Each CLI invocation opens/closes its own DB connection

## Extension Points

- New `EmbeddingProvider` implementations (e.g., OpenAI, Cohere) via protocol
- Additional `ContentType` values in IR
- Custom chunking strategies via `build_chunks()` config
- Alternative search ranking algorithms in `search/`
- Diagnostic checks in `diagnostics/integrity.py`
