# MDRack Architecture

## Project Overview

MDRack is a local command-line Markdown knowledge rack for AI agents. It indexes Markdown files, splits them into meaningful structural chunks, stores metadata and search indexes in SQLite, creates embeddings through LM Studio only, and allows agents to search, inspect, and retrieve document context through stable JSON commands.

**Technology Stack:**
- Pure Python 3.10+
- SQLite for all persistent storage
- Click for CLI
- Pydantic for configuration
- LM Studio HTTP API for embeddings only
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
- Pydantic models: `MDRackConfig`, `PathsConfig`, `ScanConfig`, `ChunkingConfig`, `EmbeddingConfig`, `SearchConfig`, `ProfilingConfig`
- Default values and type validation
- Config file location: `.mdrack/config.toml`

### `cli/`
- Main Click group with global options: `--root`, `--config-file`, `--json`
- Subcommand registration: `scan`, `search`, `status`, `doctor`, `rebuild`, `read` (group), `files` (group), `sections` (group)
- Centralized error handling via JSON envelope
- Commands delegate to service layer

### `markdown/`
- **`parser.py`**: Line-by-line state machine → `ParsedDocument` with `MarkdownBlock`s (headings, paragraphs, code, tables, lists, blockquotes, thematic breaks)
- **`ir.py`**: Data models - `BlockType`, `ContentType`, `MarkdownBlock`, `SectionNode`, `FinalChunk`, `ParsedDocument`
- **`section_builder.py`**: Converts H2–H4 headings into hierarchical `SectionNode` tree (H1 treated as title); synthetic section if no headings
- **`chunk_builder.py`**: Splits sections into `FinalChunk`s with configurable sizes (default: min 600, target 1200, hard limit 2200, overlap 180). Preserves code/mermaid/tables as atomic chunks. Forms doubly-linked list via `previous_chunk_id/next_chunk_id`.
- **`embedding_text.py`**: Constructs embedding input text from chunks (combines content + context)
- **`frontmatter.py`**: Parses YAML frontmatter

### `storage/sqlite/`
- **`connection.py`**: Factory `get_connection()` — enables WAL, foreign keys, `row_factory = sqlite3.Row`
- **`migrations.py`**: Custom migration runner — applies `NNNN_name.sql` files in order, tracks in `schema_migrations`
- **`repositories.py`**: CRUD queries for `files`, `sections`, `chunks`; also `get_neighbors()`, `count_*()` helpers
- **`fts.py`**: FTS5 operations — `upsert_fts()`, `delete_fts()`, `search_fts()` (with snippet), `rebuild_fts()`
- **`vector.py`**: `VectorIndex` class — stores embeddings as JSON blobs in `chunk_embeddings`, computes cosine similarity in pure Python, supports `upsert()`, `search()`, `delete()`, `count()`

### `embeddings/`
- **`protocol.py`**: `EmbeddingProvider` protocol (async `embed()`, `embed_query()`, `health()`), `EmbeddingError`, `EmbeddingHealth`
- **`lmstudio.py`**: LM Studio provider — HTTP POST to `/v1/embeddings`, async, configurable endpoint/timeout/dimensions
- **`fake.py`**: Deterministic fake provider for testing (hash-based vectors)
- **`hashing.py`**: Text hashing utilities for embedding cache keys

### `indexing/`
- **`scanner.py`**: `scan_markdown_files(root, include, exclude)` — walks filesystem, applies glob patterns (`**/*.md` by default), excludes `.git`, `node_modules`, `.venv`, `.mdrack`, `tests/**`
- **`change_detector.py`**: `detect_changes(conn, current_files, root)` — compares disk SHA-256 vs. `source_hash` in DB → `ChangePlan` (new/changed/unchanged/deleted)
- **`indexer.py`**: end-to-end indexing pipeline — scan, change detection,
  parse, section build, chunk build, embed, persist, and record diagnostics

### `search/`
- **`text.py`**: Full-text search via FTS5 — `text_search(conn, query, limit, offset)` returns `TextSearchResult` with `TextSearchItem` (chunk_id, rank, snippet, file_relative_path, section_title, heading_path). Enriches FTS results with provenance via joins.
- **`semantic.py`**: Semantic search — `semantic_search(conn, query, provider, profile, limit)` embeds query → `VectorIndex.search()` → joins to get file/section context → returns `SemanticSearchResult` with `SearchResultItem` (content_preview, scores). Handles embedding failures gracefully.
- **`hybrid.py`**: Hybrid search using Reciprocal Rank Fusion (RRF) with
  explicit degraded-mode reporting when semantic embedding fails

### `diagnostics/`
- **`integrity.py`**: `get_store_status(conn)` — returns files_count, chunks_count, embeddings_count, active_profile, schema_version
- **`doctor.py`**: comprehensive health checks for FTS coverage, embeddings,
  stale vectors, and schema migration status

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
│  parse_markdown(file_path, content) → ParsedDocument      │
│    • Blocks (MarkdownBlock)                               │
│    • Metadata (title, frontmatter)                        │
│    • source_hash (SHA-256)                                │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  3. SECTIONS                                              │
│  build_sections(blocks, file_id) → list[SectionNode]     │
│    • H2–H4 headings → hierarchy with parent_id           │
│    • heading_path computed via parent chain              │
│    • Synthetic section if no headings                    │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  4. CHUNKS                                                │
│  build_chunks(blocks, sections, file_id) → list[FinalChunk]│
│    • Text chunks: 600–2200 chars, target 1200, overlap 180│
│    • Atomic chunks: CODE/MERMAID/TABLE (never split)     │
│    • Doubly-linked list (prev/next IDs)                  │
│    • heading_path inherited from section                │
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
│    • upsert_fts() for FTS5                              │
│    • VectorIndex.upsert() for embeddings                │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  7. SEARCH                                               │
│  • text_search()  → FTS5 + snippet + provenance          │
│  • semantic_search() → vector cosine + provenance        │
│  • hybrid_search() → RRF fusion (future)                │
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
