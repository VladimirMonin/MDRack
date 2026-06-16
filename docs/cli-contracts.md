# MDRack CLI Contracts

This document specifies every CLI command, its flags, JSON output shape
(success and error), and behavioural notes.  All output is JSON — one
object per invocation, printed to stdout for success and stderr for
unhandled exceptions caught by the Click handler.

---

## JSON Envelope

Every successful response follows this shape:

```json
{
  "ok": true,
  "data": { /* command-specific payload */ },
  "meta": {
    "command": "scan"
  }
}
```

Every error response follows this shape:

```json
{
  "ok": false,
  "error": {
    "message": "Human-readable description",
    "code": "ERROR_CODE",
    "details": { /* optional extra context */ }
  },
  "meta": {
    "command": "search"
  }
}
```

The `meta.command` field contains the dot-separated subcommand chain
that produced the response (e.g. `"read chunk"`, `"rebuild fts"`).

---

## Global Options

```
mdrack [--root <dir>] [--json] [--config-file <path.toml>] <command>
```

| Option | Default | Description |
|---|---|---|
| `--root <dir>` | `.` | Project root directory (must exist, must be a directory). |
| `--json` | `true` | When `true` (default) output is compact JSON. When `false` output is pretty-printed with `indent=2`. |
| `--config-file <path>` | none | Path to a TOML config file. When omitted, `.mdrack/config.toml` is read if it exists; otherwise defaults are used. |

---

## 1. `mdrack init`

```
mdrack init
```

Initialise a local knowledge store.

### Success

```json
{
  "ok": true,
  "data": { "status": "not yet implemented" },
  "meta": { "command": "init" }
}
```

### Notes

- Returns the `"not yet implemented"` stub unconditionally.
- The knowledge store directory (default `.mdrack/`) and database are
  created lazily by `mdrack scan` — running `init` is **not required**
  before scanning.

---

## 2. `mdrack scan`

```
mdrack scan [--changed] [--provider fake]
```

Scan Markdown files under the project root and build/update the
knowledge index.

| Flag | Type | Description |
|---|---|---|
| `--changed` | flag | Accepted but currently **ignored** — the indexer always detects and processes changed, new, and deleted files. |
| `--provider` | `fake` | Embedding provider. When set to `fake`, deterministic random vectors are generated for each chunk. When omitted, **no embeddings** are computed. |

### Success

```json
{
  "ok": true,
  "data": {
    "run_id": "b3a4c1e2-...",
    "files_seen": 15,
    "files_changed": 3,
    "files_deleted": 0,
    "chunks_created": 42
  },
  "meta": { "command": "scan" }
}
```

| Field | Type | Description |
|---|---|---|
| `run_id` | string | UUID of the index run. |
| `files_seen` | integer | Total Markdown files discovered on disk. |
| `files_changed` | integer | Files that were new or content-changed since last scan. |
| `files_deleted` | integer | Files present in the database but missing on disk. |
| `chunks_created` | integer | Total chunks written during this run (includes re-indexed files). |

### Error

```json
{
  "ok": false,
  "error": {
    "message": "Configuration not available",
    "code": "CONFIG_ERROR"
  },
  "meta": { "command": "scan" }
}
```

Additional error codes: `INTERNAL_ERROR`.

### Notes

- The database file written is `<store>/index.db` (default `.mdrack/index.db`).
- Changelist is computed by comparing file hashes in the database against
  current disk content.
- Scan includes `**/*.md` by default and excludes `tests/**`,
  `node_modules/**`, `.git/**`, `.venv/**`.
- When `--provider fake` is used, embeddings are deterministic random
  vectors of `config.embedding.dimensions` (default 768).

---

## 3. `mdrack search`

```
mdrack search <query> [--mode text|semantic|hybrid] [--limit N] [--provider lmstudio|fake]
```

Search indexed chunks by text, semantic similarity, or a hybrid blend.

| Argument/Flag | Type | Description |
|---|---|---|
| `QUERY` | string (required) | Search query string. Supports FTS5 syntax in `text` and `hybrid` modes (prefix with `*`, phrases with `"..."`, boolean `OR`). |
| `--mode` | `text` / `semantic` / `hybrid` | Search mode. Default: `hybrid` (from config). |
| `--limit` | integer | Max results. Default: `20` (from config). |
| `--provider` | `lmstudio` / `fake` | Embedding provider for `semantic` and `hybrid` modes. Default: `lmstudio` (from config). |

### 3a. Text search (`--mode text`)

#### Success

```json
{
  "ok": true,
  "data": {
    "query": "python async",
    "mode": "text",
    "results": [
      {
        "chunk_id": "a1b2c3d4-...",
        "score": 1.0,
        "snippet": "...Python <b>async</b> functions...",
        "file": "docs/guide.md",
        "section_title": "Async IO",
        "heading_path": "Guide > Async IO"
      }
    ],
    "total_count": 12
  },
  "meta": { "command": "search" }
}
```

| Field | Type | Description |
|---|---|---|
| `chunk_id` | string | UUID of the matching chunk. |
| `score` | float | FTS5 bm25-like rank (lower is better). |
| `snippet` | string | Highlighted snippet from FTS5 (`<b>` tags for matches). |
| `file` | string | Relative path of the source file. |
| `section_title` | string or null | Title of the parent section (if any). |
| `heading_path` | string or null | Full heading ancestry (e.g. `"H1 > H2"`). |

### 3b. Semantic search (`--mode semantic`)

#### Success

```json
{
  "ok": true,
  "data": {
    "query": "python async",
    "mode": "semantic",
    "results": [
      {
        "chunk_id": "a1b2c3d4-...",
        "score": 0.87,
        "content_preview": "Python provides async/await...",
        "file": "docs/guide.md",
        "section_title": "Async IO",
        "heading_path": "Guide > Async IO"
      }
    ],
    "total_count": 20
  },
  "meta": { "command": "search" }
}
```

| Field | Type | Description |
|---|---|---|
| `chunk_id` | string | UUID of the matching chunk. |
| `score` | float | Cosine similarity (0–1, higher is more similar). |
| `content_preview` | string | First 200 characters of chunk content. |

### 3c. Hybrid search (`--mode hybrid`)

#### Success

```json
{
  "ok": true,
  "data": {
    "query": "python async",
    "mode": "hybrid",
    "results": [
      {
        "chunk_id": "a1b2c3d4-...",
        "combined_score": 0.032,
        "text_score": 1.0,
        "semantic_score": 0.87,
        "text_rank": 1,
        "semantic_rank": 3,
        "content_preview": "...Python async functions...",
        "file": "docs/guide.md",
        "section_title": "Async IO",
        "heading_path": "Guide > Async IO"
      }
    ],
    "total_count": 15
  },
  "meta": { "command": "search" }
}
```

| Field | Type | Description |
|---|---|---|
| `combined_score` | float | RRF combined score (higher is better). |
| `text_score` | float | Original FTS5 rank (may be `null` if chunk only appears in semantic results). |
| `semantic_score` | float | Original cosine similarity (may be `null`). |
| `text_rank` | integer or null | 1-based rank in the text results list. |
| `semantic_rank` | integer or null | 1-based rank in the semantic results list. |
| `content_preview` | string | First 200 chars of chunk content (from semantic) or FTS5 snippet (from text). |

### Common errors

```json
{
  "ok": false,
  "error": {
    "message": "Database not found at /path/to/.mdrack/knowledge.db. Run 'mdrack scan' first.",
    "code": "STORAGE_ERROR"
  },
  "meta": { "command": "search" }
}
```

Additional error codes: `FTS_ERROR`, `SEARCH_ERROR`, `INTERNAL_ERROR`.

### Notes

- The database read is `<store>/knowledge.db` (default `.mdrack/knowledge.db`).
- Hybrid uses Reciprocal Rank Fusion (RRF) with `k=60` and applies weighting
  from config: default `text_weight=0.4`, `semantic_weight=0.6`.
- Hybrid runs text and semantic searches in parallel, fetching `limit*2`
  candidates from each to improve fusion quality.
- Semantic search loads all vectors into memory and performs linear-scan
  cosine similarity.

---

## 4. `mdrack read chunk`

```
mdrack read chunk <chunk_id> [--context none|neighbors]
```

Retrieve a chunk by its UUID.

| Argument/Flag | Type | Description |
|---|---|---|
| `CHUNK_ID` | string (required) | UUID of the chunk. |
| `--context` | `none` / `neighbors` | When `neighbors`, includes 1 previous and 1 next chunk via the doubly-linked list. Default: `none`. |

### Success (without context)

```json
{
  "ok": true,
  "data": {
    "chunk": {
      "id": "a1b2c3d4-...",
      "file_id": "f1e2d3c4-...",
      "section_id": "s1e2c3t4-...",
      "content": "# Introduction\n\n...",
      "content_type": "text",
      "chunk_index": 0,
      "heading_path": "[\"Introduction\"]",
      "previous_chunk_id": null,
      "next_chunk_id": "b2c3d4e5-...",
      "embedding_text": "...",
      "embedding_text_hash": "abc123..."
    }
  },
  "meta": { "command": "read chunk" }
}
```

### Success (with `--context neighbors`)

```json
{
  "ok": true,
  "data": {
    "chunk": { /* same as above */ },
    "neighbors": [
      { /* previous chunk dict */ },
      { /* next chunk dict */ }
    ]
  },
  "meta": { "command": "read chunk" }
}
```

If no previous or next chunk exists, only the available neighbors are
returned (the neighbours list may have 0, 1, or 2 entries).

### Error

```json
{
  "ok": false,
  "error": {
    "message": "Chunk 'invalid-id' not found",
    "code": "NOT_FOUND"
  },
  "meta": { "command": "read chunk" }
}
```

Additional error codes: `STORAGE_ERROR`.

### Notes

- Database read is `<store>/mdrack.db` (default `.mdrack/mdrack.db`).
- Chunks are linked via `previous_chunk_id` and `next_chunk_id` columns.
- `heading_path` is stored as a JSON-encoded array of heading titles.

---

## 5. `mdrack read section`

```
mdrack read section <section_id>
```

Read a section and all its chunks by section UUID.

### Success

```json
{
  "ok": true,
  "data": {
    "section": {
      "id": "s1e2c3t4-...",
      "file_id": "f1e2d3c4-...",
      "title": "Async IO",
      "heading_path": "[\"Guide\", \"Async IO\"]",
      "level": 2,
      "start_line": 45,
      "end_line": 120,
      "parent_id": "p1a2r3e4-..."
    },
    "chunks": [
      { /* chunk dict */ },
      { /* chunk dict */ }
    ]
  },
  "meta": { "command": "read section" }
}
```

### Error

```json
{
  "ok": false,
  "error": {
    "message": "Section 'invalid-id' not found",
    "code": "NOT_FOUND"
  },
  "meta": { "command": "read section" }
}
```

### Notes

- Database read is `<store>/mdrack.db` (default `.mdrack/mdrack.db`).
- Chunks are ordered by `chunk_index` ascending.
- Sections form a tree via `parent_id`. `heading_path` denormalizes the
  ancestry for fast retrieval.

---

## 6. `mdrack read file`

```
mdrack read file <file_id>
```

Read file metadata and list all sections by file UUID.

### Success

```json
{
  "ok": true,
  "data": {
    "file": {
      "id": "f1e2d3c4-...",
      "relative_path": "docs/guide.md",
      "title": "User Guide",
      "source_hash": "abc123...",
      "indexed_at": "2026-06-17T12:00:00+00:00",
      "status": "active"
    },
    "sections": [
      {
        "id": "s1e2c3t4-...",
        "file_id": "f1e2d3c4-...",
        "title": "Introduction",
        "heading_path": "[\"Introduction\"]",
        "level": 2,
        "start_line": 1,
        "end_line": 44,
        "parent_id": null
      }
    ]
  },
  "meta": { "command": "read file" }
}
```

### Error

```json
{
  "ok": false,
  "error": {
    "message": "File 'invalid-id' not found",
    "code": "NOT_FOUND"
  },
  "meta": { "command": "read file" }
}
```

### Notes

- Database read is `<store>/mdrack.db` (default `.mdrack/mdrack.db`).
- Sections are ordered by `start_line` ascending.

---

## 7. `mdrack files list`

```
mdrack files list [--page N] [--page-size N]
```

Paginated listing of all indexed files.

| Flag | Default | Description |
|---|---|---|
| `--page` | `0` | Page number (0-indexed). Must be non-negative. |
| `--page-size` | `20` | Items per page. Must be positive. |

### Success

```json
{
  "ok": true,
  "data": {
    "files": [
      {
        "id": "f1e2d3c4-...",
        "relative_path": "docs/guide.md",
        "title": "User Guide",
        "source_hash": "abc123...",
        "indexed_at": "2026-06-17T12:00:00+00:00",
        "status": "active"
      }
    ],
    "pagination": {
      "page": 0,
      "page_size": 20,
      "total": 45,
      "has_next": true
    }
  },
  "meta": { "command": "files list" }
}
```

| Field | Type | Description |
|---|---|---|
| `pagination.page` | integer | Current page (0-indexed). |
| `pagination.page_size` | integer | Items per page. |
| `pagination.total` | integer | Total number of indexed files. |
| `pagination.has_next` | boolean | `true` if more pages exist. |

### Error

```json
{
  "ok": false,
  "error": {
    "message": "Page number must be non-negative",
    "code": "VALIDATION_ERROR"
  },
  "meta": { "command": "files list" }
}
```

```json
{
  "ok": false,
  "error": {
    "message": "Database not found at ... .mdrack/knowledge.db. Run 'mdrack init' first.",
    "code": "STORAGE_ERROR"
  },
  "meta": { "command": "files list" }
}
```

### Notes

- Database read is `<store>/knowledge.db` (default `.mdrack/knowledge.db`).
- Files are ordered by `relative_path` ascending.

---

## 8. `mdrack files info`

```
mdrack files info <file_id>
```

Show metadata for a single indexed file.

### Success

```json
{
  "ok": true,
  "data": {
    "file": {
      "id": "f1e2d3c4-...",
      "relative_path": "docs/guide.md",
      "title": "User Guide",
      "source_hash": "abc123...",
      "indexed_at": "2026-06-17T12:00:00+00:00",
      "status": "active"
    }
  },
  "meta": { "command": "files info" }
}
```

### Error

```json
{
  "ok": false,
  "error": {
    "message": "File 'invalid-id' not found",
    "code": "NOT_FOUND"
  },
  "meta": { "command": "files info" }
}
```

### Notes

- Database read is `<store>/knowledge.db` (default `.mdrack/knowledge.db`).

---

## 9. `mdrack sections list`

```
mdrack sections list <file_id>
```

List all sections for a given file.

### Success

```json
{
  "ok": true,
  "data": {
    "sections": [
      {
        "id": "s1e2c3t4-...",
        "file_id": "f1e2d3c4-...",
        "title": "Introduction",
        "heading_path": "[\"Introduction\"]",
        "level": 2,
        "start_line": 1,
        "end_line": 44,
        "parent_id": null
      }
    ]
  },
  "meta": { "command": "sections list" }
}
```

### Error

```json
{
  "ok": false,
  "error": {
    "message": "File 'invalid-id' not found",
    "code": "NOT_FOUND"
  },
  "meta": { "command": "sections list" }
}
```

### Notes

- Database read is `<store>/knowledge.db` (default `.mdrack/knowledge.db`).
- Sections are ordered by `start_line` ascending.
- Verifies the file exists before listing sections.

---

## 10. `mdrack status`

```
mdrack status
```

Show summary statistics of the knowledge store.

### Success (store exists)

```json
{
  "ok": true,
  "data": {
    "files_count": 15,
    "chunks_count": 142,
    "embeddings_count": 142,
    "active_profile": "default",
    "schema_version": "0003"
  },
  "meta": { "command": "status" }
}
```

| Field | Type | Description |
|---|---|---|
| `files_count` | integer | Total files in the store. |
| `chunks_count` | integer | Total chunks across all files. |
| `embeddings_count` | integer | Embeddings for the `"default"` profile. |
| `active_profile` | string | Always `"default"`. |
| `schema_version` | string or null | Maximum applied migration version, or `null` if none. |

### Success (store does not exist)

```json
{
  "ok": true,
  "data": {
    "files_count": 0,
    "chunks_count": 0,
    "embeddings_count": 0,
    "active_profile": null,
    "schema_version": null
  },
  "meta": { "command": "status" }
}
```

If the database file is absent, all counts are zero and `active_profile`
and `schema_version` are `null`.

### Notes

- Database read is `<store>/knowledge.db` (default `.mdrack/knowledge.db`).
- `schema_version` is the maximum integer from the `schema_migrations` table.

---

## 11. `mdrack doctor`

```
mdrack doctor
```

Run diagnostic checks on the knowledge store.

### Success

```json
{
  "ok": true,
  "data": { "status": "not yet implemented" },
  "meta": { "command": "doctor" }
}
```

### Notes

- Returns the `"not yet implemented"` stub unconditionally.
- The `DoctorReport` infrastructure exists in `diagnostics/doctor.py` and
  supports checks for missing FTS rows, missing embeddings, stale embeddings,
  and schema version mismatches, but it is not wired into the CLI yet.

---

## 12. `mdrack rebuild fts`

```
mdrack rebuild fts
```

Rebuild the FTS5 full-text index from the chunks table.

### Success

```json
{
  "ok": true,
  "data": {
    "fts_count": 142,
    "chunk_count": 142
  },
  "meta": { "command": "rebuild fts" }
}
```

| Field | Type | Description |
|---|---|---|
| `fts_count` | integer | Row count in `chunks_fts` after rebuild. |
| `chunk_count` | integer | Row count in `chunks` table. |

### Notes

- Database file: `<store>/index.db` (default `.mdrack/index.db`).
- Automatically applies pending migrations before rebuilding.
- Deletes all existing FTS rows and bulk-inserts every chunk.
- If `fts_count != chunk_count`, some chunks are missing FTS entries
  (data integrity issue).

---

## 13. `mdrack rebuild embeddings`

```
mdrack rebuild embeddings [--provider lmstudio|fake] [--profile <name>]
```

Rebuild all embedding vectors for the active profile.

| Flag | Default | Description |
|---|---|---|
| `--provider` | `lmstudio` (from config) | Embedding provider. |
| `--profile` | `"default"` | Embedding profile name. |

### Success

```json
{
  "ok": true,
  "data": {
    "embedded_count": 142,
    "total_chunks": 150,
    "profile": "default",
    "provider": "lmstudio"
  },
  "meta": { "command": "rebuild embeddings" }
}
```

| Field | Type | Description |
|---|---|---|
| `embedded_count` | integer | Chunks that were embedded in this run. |
| `total_chunks` | integer | Total chunks in the store. |
| `profile` | string | Embedding profile name used. |
| `provider` | string | Provider used for embedding. |

If no chunks have `embedding_text`, `embedded_count` is 0 and no API
calls are made:

```json
{
  "ok": true,
  "data": {
    "embedded_count": 0,
    "total_chunks": 150,
    "profile": "default",
    "provider": "lmstudio"
  },
  "meta": { "command": "rebuild embeddings" }
}
```

### Notes

- Database file: `<store>/index.db` (default `.mdrack/index.db`).
- Automatically applies pending migrations before rebuilding.
- Creates the embedding profile if it does not exist.
- Vectors are upserted into `chunk_embeddings` using the composite key
  `(chunk_id, profile_name)` — existing vectors for the same profile
  are overwritten.
- Embeddings are sent to the provider in a single batch.

---

## 14. `mdrack eval`

```
mdrack eval
```

Run retrieval evaluation queries.

### Success

```json
{
  "ok": true,
  "data": { "status": "not yet implemented" },
  "meta": { "command": "eval" }
}
```

### Notes

- Returns the `"not yet implemented"` stub unconditionally.
- No `--queries` flag or `retrieval` subcommand exists in the current
  implementation.

---

## Error Code Reference

| Code | Typical cause |
|---|---|
| `CONFIG_ERROR` | Configuration missing or failed to load. |
| `STORAGE_ERROR` | Database file not found or inaccessible. |
| `EMBEDDING_ERROR` | LM Studio unavailable or model error during embedding. |
| `NOT_FOUND` | Requested ID does not exist in the store. |
| `FTS_ERROR` | Invalid FTS5 query syntax. |
| `SEARCH_ERROR` | Embedding or vector search failed (returned in data, not as top-level error). |
| `VALIDATION_ERROR` | Invalid argument value (e.g. negative page number). |
| `INTERNAL_ERROR` | Unhandled exception during command execution. |

---

## Database File Summary

Different commands read from different database file names:

| Command | Database file |
|---|---|
| `scan` | `<store>/index.db` |
| `search` | `<store>/knowledge.db` |
| `read chunk` | `<store>/mdrack.db` |
| `read section` | `<store>/mdrack.db` |
| `read file` | `<store>/mdrack.db` |
| `files list` | `<store>/knowledge.db` |
| `files info` | `<store>/knowledge.db` |
| `sections list` | `<store>/knowledge.db` |
| `status` | `<store>/knowledge.db` |
| `rebuild fts` | `<store>/index.db` |
| `rebuild embeddings` | `<store>/index.db` |

The scan/indexer writes to `index.db` while most read commands use
`knowledge.db` or `mdrack.db`.  This is a known discrepancy — in
practice the indexer should write to a single file read by all other
commands.

---

## Configuration Reference

All defaults are defined in the configuration model and can be overridden
via `.mdrack/config.toml` or `--config-file`:

```toml
[paths]
root = "."
store = ".mdrack"

[scan]
include = ["**/*.md"]
exclude = ["tests/**", "node_modules/**", ".git/**", ".venv/**"]

[chunking]
min_chunk_chars = 600
target_chunk_chars = 1200
hard_limit_chars = 2200
overlap_chars = 180

[embedding]
provider = "lmstudio"
model = "nomic-embed-text"
endpoint = "http://localhost:1234/v1"
timeout_secs = 120
dimensions = 768

[search]
default_mode = "hybrid"
text_weight = 0.4
semantic_weight = 0.6
top_k = 20
rrf_k = 60

[profiling]
embedding_profiles = ["default"]
```