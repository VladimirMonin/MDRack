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
  "data": {
    "status": "initialized",
    "store_path": "C:/vault/.mdrack",
    "db_path": "C:/vault/.mdrack/knowledge.db",
    "schema_version": "0006"
  },
  "meta": { "command": "init" }
}
```

### Notes

- Creates the knowledge store directory and applies migrations to
  `<store>/knowledge.db`.
- `init` is idempotent and safe to run before `scan`.
- Migration history is linear and fail-closed. A database containing an unknown
  future version is not modified by an older MDRack build.

---

## 2. `mdrack scan`

```
mdrack scan [--changed] [--provider lmstudio|fake]
```

Scan Markdown files under the project root and build/update the
knowledge index.

| Flag | Type | Description |
|---|---|---|
| `--changed` | flag | Accepted but currently **ignored** — the indexer always detects and processes changed, new, and deleted files. |
| `--provider` | `lmstudio` / `fake` | Embedding provider. When omitted, the configured provider from `[embedding].provider` is used. |

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

- The database file written is `<store>/knowledge.db` (default `.mdrack/knowledge.db`).
- Changelist is computed by comparing file hashes in the database against
  current disk content.
- Scan includes `**/*.md` by default and excludes `tests/**`,
  `node_modules/**`, `.git/**`, `.venv/**`.
- When `--provider fake` is used, embeddings are deterministic random
  vectors of `config.embedding.dimensions`.
- When `--provider lmstudio` is used, embeddings are computed during scan
  and persisted under the active profile.

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
        "logical_id": "chunk_logical_01",
        "chunk_id": "chunk_logical_01",
        "score": -1.375,
        "text_score": -1.375,
        "semantic_score": null,
        "text_rank": 1,
        "semantic_rank": null,
        "rrf_rank": null,
        "rrf_score": null,
        "rerank_rank": null,
        "rerank_score": null,
        "content_preview": "...Python <b>async</b> functions...",
        "snippet": "...Python <b>async</b> functions...",
        "file": "docs/guide.md",
        "section_title": "Async IO",
        "heading_path": ["Guide", "Async IO"],
        "source_locator": {
          "root_id": "default",
          "relative_path": "docs/guide.md",
          "start_line": 20,
          "end_line": 31,
          "start_offset": 340,
          "end_offset": 612,
          "heading_path": ["Guide", "Async IO"],
          "block_kind": "paragraph",
          "chunk_kind": "text",
          "block_logical_id": "block_logical_01",
          "chunk_logical_id": "chunk_logical_01"
        }
      }
    ],
    "total_count": 1,
    "degraded": false,
    "degraded_reason": null
  },
  "meta": { "command": "search" }
}
```

For text results, `score` and `text_score` are the same FTS5 bm25-like
candidate score (lower is better), and `text_rank` is its 1-based position.
Semantic, RRF, and rerank fields are `null`.

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
        "logical_id": "chunk_logical_01",
        "chunk_id": "chunk_logical_01",
        "score": 0.87,
        "text_score": null,
        "semantic_score": 0.87,
        "text_rank": null,
        "semantic_rank": 1,
        "rrf_rank": null,
        "rrf_score": null,
        "rerank_rank": null,
        "rerank_score": null,
        "content_preview": "Python provides async/await...",
        "snippet": "Python provides async/await...",
        "file": "docs/guide.md",
        "section_title": "Async IO",
        "heading_path": ["Guide", "Async IO"],
        "source_locator": {
          "root_id": "default",
          "relative_path": "docs/guide.md",
          "start_line": 20,
          "end_line": 31,
          "start_offset": 340,
          "end_offset": 612,
          "heading_path": ["Guide", "Async IO"],
          "block_kind": "paragraph",
          "chunk_kind": "text",
          "block_logical_id": "block_logical_01",
          "chunk_logical_id": "chunk_logical_01"
        }
      }
    ],
    "total_count": 1,
    "degraded": false,
    "degraded_reason": null
  },
  "meta": { "command": "search" }
}
```

For semantic results, `score` and `semantic_score` are the same cosine
similarity (higher is more similar), and `semantic_rank` is its 1-based
position. Text, RRF, and rerank fields are `null`.

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
        "logical_id": "chunk_logical_01",
        "chunk_id": "chunk_logical_01",
        "score": 0.032266458495966696,
        "text_score": -1.375,
        "semantic_score": 0.87,
        "text_rank": 1,
        "semantic_rank": 3,
        "rrf_rank": 1,
        "rrf_score": 0.032266458495966696,
        "rerank_rank": null,
        "rerank_score": null,
        "content_preview": "...Python async functions...",
        "snippet": "...Python async functions...",
        "file": "docs/guide.md",
        "section_title": "Async IO",
        "heading_path": ["Guide", "Async IO"],
        "source_locator": {
          "root_id": "default",
          "relative_path": "docs/guide.md",
          "start_line": 20,
          "end_line": 31,
          "start_offset": 340,
          "end_offset": 612,
          "heading_path": ["Guide", "Async IO"],
          "block_kind": "paragraph",
          "chunk_kind": "text",
          "block_logical_id": "block_logical_01",
          "chunk_logical_id": "chunk_logical_01"
        }
      }
    ],
    "total_count": 1,
    "degraded": false,
    "degraded_reason": null
  },
  "meta": { "command": "search" }
}
```

For hybrid results, `score` and `rrf_score` are the same RRF score (higher is
better), and `rrf_rank` is the 1-based fused position. `text_score`,
`semantic_score`, `text_rank`, and `semantic_rank` preserve the component
candidate scores and positions; each may be `null` when the item is absent from
that branch. Production v0.2 performs no reranking, so `rerank_rank` and
`rerank_score` are always `null`.

### 3d. Result field contract (all modes)

| Field | Type | Description |
|---|---|---|
| `logical_id` | string | Stable public chunk identity. It is not the SQLite record UUID. |
| `chunk_id` | string | Compatibility alias equal to `logical_id`. |
| `score` | float | Canonical mode score: text candidate score, semantic similarity, or hybrid RRF score. |
| `text_score` | float or null | Text candidate score when present. |
| `semantic_score` | float or null | Semantic candidate score when present. |
| `text_rank` | integer or null | 1-based position in text candidates. |
| `semantic_rank` | integer or null | 1-based position in semantic candidates. |
| `rrf_rank` | integer or null | 1-based fused rank; populated only for hybrid results. |
| `rrf_score` | float or null | Reciprocal Rank Fusion score; populated only for hybrid results and equal to `score`. |
| `rerank_rank` | null | Reserved for a future reranker; always `null` in production v0.2. |
| `rerank_score` | null | Reserved for a future reranker; always `null` in production v0.2. |
| `content_preview` | string | Candidate preview; highlighted FTS5 snippet for text-origin candidates, otherwise up to 200 content characters. |
| `snippet` | string | Compatibility alias equal to `content_preview`. |
| `file` | string | Compatibility projection of `source_locator.relative_path`. |
| `section_title` | string or null | Parent section title when available. |
| `heading_path` | array of strings | Full heading ancestry; equal to `source_locator.heading_path`. |
| `source_locator` | object | Complete portable locator: root-relative path, heading path, line/offset span, block/chunk kinds, and public block/chunk logical IDs. |

### Common errors

```json
{
  "ok": false,
  "error": {
    "message": "Database not found. Run 'mdrack scan' first.",
    "code": "STORAGE_ERROR"
  },
  "meta": { "command": "search" }
}
```

Additional error codes: `FTS_ERROR`, `EMBEDDING_ERROR`, `INTERNAL_ERROR`.

### Notes

- The database read is `<store>/knowledge.db` (default `.mdrack/knowledge.db`).
- Hybrid uses unweighted Reciprocal Rank Fusion (RRF) with `k=60`.
- Hybrid fetches `limit*2` candidates from each branch before fusion.
- Semantic search loads all vectors into memory and performs linear-scan
  cosine similarity.

---

## 4. `mdrack read chunk`

```
mdrack read chunk <chunk_id> [--context none|neighbors]
```

Retrieve a chunk by its stable public logical ID. SQLite record UUIDs are internal.

| Argument/Flag | Type | Description |
|---|---|---|
| `CHUNK_ID` | string (required) | Public chunk logical ID. |
| `--context` | `none` / `neighbors` | When `neighbors`, includes 1 previous and 1 next chunk via the doubly-linked list. Default: `none`. |

### Success (without context)

```json
{
  "ok": true,
  "data": {
    "chunk": {
      "id": "chunk_logical_01",
      "logical_id": "chunk_logical_01",
      "content": "# Introduction\n\n...",
      "content_type": "text",
      "chunk_index": 0,
      "heading_path": ["Introduction"],
      "embedding_text_hash": "abc123...",
      "source_locator": {
        "root_id": "default",
        "relative_path": "docs/guide.md",
        "start_line": 1,
        "end_line": 12,
        "start_offset": 0,
        "end_offset": 240,
        "heading_path": ["Introduction"],
        "block_kind": "paragraph",
        "chunk_kind": "text",
        "block_logical_id": "block_logical_01",
        "chunk_logical_id": "chunk_logical_01"
      }
    }
  },
  "meta": { "command": "read chunk" }
}
```

### Success (with `--context neighbors`)

The success envelope has the same `chunk` object and additionally contains a
`neighbors` array. Every neighbor uses the exact same public chunk schema shown
above. Internal file, section, chunk, and linked-list record UUIDs are omitted.

If no previous or next chunk exists, only the available neighbors are
returned (the neighbours list may have 0, 1, or 2 entries).

### Error

```json
{
  "ok": false,
  "error": {
    "message": "Chunk not found",
    "code": "NOT_FOUND"
  },
  "meta": { "command": "read chunk" }
}
```

Additional error codes: `STORAGE_ERROR`.

### Notes

- Database read is `<store>/knowledge.db` (default `.mdrack/knowledge.db`).
- `id` is a compatibility alias equal to the stable public `logical_id`.
- `heading_path` and `source_locator.heading_path` are arrays of strings.
- `source_locator.start_offset` and `end_offset` may both be `null` for rows
  migrated from schema 0005; all newly indexed chunks include integer offsets.

---

## 5. `mdrack read section`

```
mdrack read section <section_id>
```

Read a section and all its chunks by stable public section logical ID.

### Success

```json
{
  "ok": true,
  "data": {
    "section": {
      "id": "section_logical_01",
      "logical_id": "section_logical_01",
      "title": "Async IO",
      "heading_path": "[\"Guide\", \"Async IO\"]",
      "level": 2,
      "start_line": 45,
      "end_line": 120
    },
    "chunks": [
      {
        "id": "chunk_logical_01",
        "logical_id": "chunk_logical_01",
        "content": "Python async content",
        "content_type": "text",
        "chunk_index": 0,
        "heading_path": ["Guide", "Async IO"],
        "embedding_text_hash": "abc123...",
        "source_locator": {
          "root_id": "default",
          "relative_path": "docs/guide.md",
          "start_line": 45,
          "end_line": 60,
          "start_offset": 900,
          "end_offset": 1250,
          "heading_path": ["Guide", "Async IO"],
          "block_kind": "paragraph",
          "chunk_kind": "text",
          "block_logical_id": "block_logical_01",
          "chunk_logical_id": "chunk_logical_01"
        }
      }
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
    "message": "Section not found",
    "code": "NOT_FOUND"
  },
  "meta": { "command": "read section" }
}
```

### Notes

- Database read is `<store>/knowledge.db` (default `.mdrack/knowledge.db`).
- Chunks are ordered by `chunk_index` ascending.
- The public section `id` is a compatibility alias equal to `logical_id`.
- Internal `file_id` and `parent_id` record UUIDs are omitted.
- Section `heading_path` is the stored JSON string or `null`; chunk heading
  paths are decoded arrays.

---

## 6. `mdrack read file`

```
mdrack read file <file_id>
```

Read file metadata and list all sections by stable public file logical ID.

### Success

```json
{
  "ok": true,
  "data": {
    "file": {
      "id": "file_logical_01",
      "logical_id": "file_logical_01",
      "root_id": "default",
      "relative_path": "docs/guide.md",
      "title": "User Guide",
      "source_hash": "abc123...",
      "indexed_at": "2026-06-17T12:00:00+00:00",
      "status": "active",
      "parser_name": "markdown-it-py",
      "parser_version": "1",
      "chunk_strategy_name": "structural",
      "chunk_strategy_version": "1"
    },
    "sections": [
      {
        "id": "section_logical_01",
        "logical_id": "section_logical_01",
        "title": "Introduction",
        "heading_path": "[\"Introduction\"]",
        "level": 2,
        "start_line": 1,
        "end_line": 44
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
    "message": "File not found",
    "code": "NOT_FOUND"
  },
  "meta": { "command": "read file" }
}
```

### Notes

- Database read is `<store>/knowledge.db` (default `.mdrack/knowledge.db`).
- Sections are ordered by `start_line` ascending.
- Public file and section `id` values are compatibility aliases equal to their
  `logical_id`; internal SQLite record UUIDs and `index_run_id` are omitted.

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
    "profile_model": "text-embedding-qwen3-embedding-4b",
    "profile_dimensions": 2560,
    "profile_endpoint": "http://localhost:1234/v1",
    "configured_model": "text-embedding-qwen3-embedding-4b",
    "configured_dimensions": 2560,
    "configured_endpoint": "http://localhost:1234/v1",
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
| `profile_model` | string or null | Model recorded in `embedding_profiles` for the active profile. |
| `profile_dimensions` | integer or null | Stored vector dimension for the active profile. |
| `profile_endpoint` | string or null | Stored LM Studio endpoint for the active profile. |
| `configured_model` | string or null | Current model from the resolved MDRack config. |
| `configured_dimensions` | integer or null | Current dimension from the resolved MDRack config. |
| `configured_endpoint` | string or null | Current LM Studio endpoint from the resolved MDRack config. |
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
  "data": {
    "ok": true,
    "summary": {
      "total": 3,
      "errors": 0,
      "warnings": 0,
      "info": 3
    },
    "findings": [
      {
        "severity": "info",
        "code": "FTS_OK",
        "message": "All chunks have FTS entries",
        "details": {
          "fts_count": 0,
          "chunk_count": 0
        }
      }
    ]
  },
  "meta": { "command": "doctor" }
}
```

### Notes

- The outer envelope `ok` indicates CLI execution success.
- The inner `data.ok` indicates store health.
- `summary` contains stable severity counts.
- `findings` contains the real diagnostics output from `diagnostics/doctor.py`.
- Doctor now reports profile metadata presence and config/profile mismatch explicitly.

---

## 12. `mdrack model ...`

```
mdrack model <subcommand>
```

Manage LM Studio model lifecycle operations through MDRack.

### 12a. `mdrack model list`

```
mdrack model list
```

Returns models visible to the LM Studio native API.

Example success payload:

```json
{
  "ok": true,
  "data": {
    "models": [
      {
        "key": "text-embedding-qwen3-embedding-4b",
        "state": null,
        "loaded": false,
        "display_name": "Qwen3 Embedding 4B",
        "model_type": "embedding",
        "publisher": "Qwen",
        "selected_variant": null,
        "variants": [],
        "instance_ids": []
      }
    ]
  },
  "meta": { "command": "model list" }
}
```

### 12b. `mdrack model loaded`

```
mdrack model loaded
```

Returns the currently loaded LM Studio model instances that MDRack can see.

Example success payload:

```json
{
  "ok": true,
  "data": {
    "models": [
      {
        "key": "text-embedding-qwen3-embedding-0.6b",
        "instance_id": "text-embedding-qwen3-embedding-0.6b",
        "state": null
      }
    ]
  },
  "meta": { "command": "model loaded" }
}
```

### 12c. `mdrack model download`

```
mdrack model download <model>
```

Requests a model download through LM Studio. When the requested name is an alias
of a visible model, MDRack resolves it to the LM Studio key before sending the
request.

### 12d. `mdrack model download-status`

```
mdrack model download-status
```

Returns LM Studio download state for active download jobs.

### 12e. `mdrack model load`

```
mdrack model load <model>
```

Loads an embedding model into LM Studio. If the target model is already loaded,
the command returns:

```json
{
  "ok": true,
  "data": {
    "key": "text-embedding-qwen3-embedding-0.6b",
    "state": "already_loaded",
    "instance_id": "text-embedding-qwen3-embedding-0.6b"
  },
  "meta": { "command": "model load" }
}
```

### 12f. `mdrack model unload`

```
mdrack model unload <instance_id>
```

Unloads a specific LM Studio model instance by instance id.

### 12g. `mdrack model switch`

```
mdrack model switch <model> [--download] [--load/--no-load] [--dimensions N] [--rebuild embeddings|full|none] [--yes]
```

Switches the active embedding model for the selected project root.

Behavior summary:
- resolves human-friendly names to LM Studio model keys when possible;
- optionally downloads the target model;
- loads the target model unless `--no-load` is used;
- probes the real vector dimension if `--dimensions` is omitted;
- persists the updated config only after rebuild succeeds;
- unloads the previously active model instance after a successful switch when possible;
- rebuilds vectors for the whole active profile by default.

Example success payload:

```json
{
  "ok": true,
  "data": {
    "old_model": "text-embedding-qwen3-embedding-0.6b",
    "requested_model": "Qwen/Qwen3-Embedding-4B-GGUF",
    "new_model": "text-embedding-qwen3-embedding-4b",
    "old_dimensions": 1024,
    "new_dimensions": 2560,
    "config_path": "C:/tmp/project/.mdrack/config.toml",
    "rebuild": {
      "embedded_count": 3,
      "total_chunks": 3,
      "profile": "default",
      "performed": true,
      "mode": "embeddings"
    },
    "download": [],
    "load": {
      "key": "text-embedding-qwen3-embedding-4b",
      "state": "loaded",
      "instance_id": "text-embedding-qwen3-embedding-4b"
    },
    "unload_previous": {
      "attempted": true,
      "model": "text-embedding-qwen3-embedding-0.6b",
      "status": "unloaded",
      "results": [
        {
          "instance_id": "text-embedding-qwen3-embedding-0.6b",
          "status": "unloaded"
        }
      ]
    }
  },
  "meta": { "command": "model switch" }
}
```

Notes:
- `--rebuild none` is blocked unless `--yes` is provided.
- When the target model is already loaded, `load.state` becomes `"already_loaded"`.
- `new_model` may differ from `requested_model` because MDRack stores the resolved
  LM Studio key after a successful switch.
- `unload_previous.reason` may be `"same_model"` or `"previous_model_not_loaded"`
  when there is nothing to unload.

---

## 13. `mdrack rebuild fts`

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

- Database file: `<store>/knowledge.db` (default `.mdrack/knowledge.db`).
- Automatically applies pending migrations before rebuilding.
- Deletes all existing FTS rows and bulk-inserts every chunk.
- If `fts_count != chunk_count`, some chunks are missing FTS entries
  (data integrity issue).

---

## 14. `mdrack rebuild embeddings`

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

- Database file: `<store>/knowledge.db` (default `.mdrack/knowledge.db`).
- Automatically applies pending migrations before rebuilding.
- Creates the embedding profile if it does not exist.
- Vectors are upserted into `chunk_embeddings` using the composite key
  `(chunk_id, profile_name)` — existing vectors for the same profile
  are overwritten.
- Embeddings are sent to the provider in a single batch.

---

## 15. `mdrack eval retrieval`

```
mdrack eval retrieval --queries <path> [--k N] [--provider lmstudio|fake]
```

Run retrieval evaluation queries against the indexed store.

### Success

```json
{
  "ok": true,
  "data": {
    "queries_path": "tests/retrieval_eval/queries.yaml",
    "k": 5,
    "results": [
      {
        "query_id": "Q001",
        "query": "how can an agent read neighboring chunks",
        "mode": "hybrid",
        "k": 5,
        "recall_at_k": 1.0,
        "mrr": 1.0,
        "precision_at_k": 0.5,
        "retrieved_count": 2,
        "expected_count": 1,
        "conditions_met": true,
        "error": null
      }
    ],
    "summary": {
      "queries_total": 1,
      "queries_successful": 1,
      "queries_failed": 0,
      "queries_with_zero_gold": 0,
      "avg_recall_at_k": 1.0,
      "avg_mrr": 1.0,
      "avg_precision_at_k": 0.5
    }
  },
  "meta": { "command": "eval retrieval" }
}
```

### Notes

- Query loading validates supported `expected` and `metrics` clauses.
- Per-query `metrics.recall_at` overrides the top-level `--k` for that query.
- Failed semantic or hybrid queries surface an `error` field in per-query output.

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

All commands read and write the same database file:

| Command | Database file |
|---|---|
| `scan` | `<store>/knowledge.db` |
| `search` | `<store>/knowledge.db` |
| `model switch` | `<store>/knowledge.db` when rebuild is enabled |
| `read chunk` | `<store>/knowledge.db` |
| `read section` | `<store>/knowledge.db` |
| `read file` | `<store>/knowledge.db` |
| `files list` | `<store>/knowledge.db` |
| `files info` | `<store>/knowledge.db` |
| `sections list` | `<store>/knowledge.db` |
| `status` | `<store>/knowledge.db` |
| `rebuild fts` | `<store>/knowledge.db` |
| `rebuild embeddings` | `<store>/knowledge.db` |
| `eval retrieval` | `<store>/knowledge.db` |
| `doctor` | `<store>/knowledge.db` |

Relative store paths are resolved against the selected `--root`.

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
model = "qwen3-embedding-0.6b"
endpoint = "http://localhost:1234/v1"
timeout_secs = 120
dimensions = 1024

[search]
default_mode = "hybrid"
text_weight = 0.4
semantic_weight = 0.6
top_k = 20
rrf_k = 60

[profiling]
embedding_profiles = ["default"]
```

After a successful `mdrack model switch`, MDRack may persist the resolved LM Studio
model key (for example `text-embedding-qwen3-embedding-0.6b`) rather than the
human-friendly alias that was requested.
