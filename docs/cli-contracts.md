# MDRack CLI Contracts

## Scope

This document is the current S2 contract for the normal local MDRack CLI. All
normal operations use exactly one SQLite catalog:

```text
<store>/catalog.sqlite3
```

`<store>` is the configured `paths.store` relative to `--root` unless it is an
absolute path. There is no normal-command option for selecting a different
catalog.

The first writable operation (`init` or `scan`) creates the fixed catalog. A
read-only operation does not create a catalog. If a pre-S1 store artifact is
present, normal startup fails safely; it neither opens, transfers, nor rewrites
that artifact.

The maintainer-only [one-store acceptance runner](one-store-acceptance.md) is a
Python script, not a Click command or an alternate catalog lifecycle. It creates
only temporary fixture stores and writes bounded evidence outside the checkout.

## JSON envelope

Successful commands write one JSON value to stdout:

```json
{
  "ok": true,
  "data": {},
  "meta": {"command": "command name"}
}
```

Command failures write one JSON value with `ok: false`, an error `message`, a
stable `code`, and the command name in `meta`. The message never includes an
absolute local path or a caller-provided logical identifier.

## Catalog lifecycle

### `mdrack init`

Creates or opens the fixed catalog without indexing source files.

```json
{
  "ok": true,
  "data": {
    "status": "initialized",
    "catalog_path": "catalog.sqlite3",
    "schema_version": "0004"
  },
  "meta": {"command": "init"}
}
```

The catalog creation is fail-closed. A failed first creation leaves no usable
SQLite database or journal sidecar at the catalog path. Existing non-database
files under the root are outside the catalog lifecycle and are not removed.

### `mdrack scan [--changed] [--provider lmstudio|fake]`

Creates the fixed catalog if needed, then indexes Markdown under `--root`. The
command never edits Markdown or fetches linked assets. Its response reports run
counts (`files_seen`, `files_indexed`, `files_deleted`, `chunks_created`, and
`errors_count`) and a success or failure status.

### Embedded API

`MDRackEngine.scan()` has the same fixed-catalog creation rule. Engine reads
(`search_text`, `get_file`, and `get_chunk`) fail safely when the catalog is
absent and do not create one.

## Current command surface

All commands below share the catalog rule above; none accepts a normal
application catalog-path override.

| Command | Contract |
| --- | --- |
| `init` | Create/open the fixed catalog. |
| `scan` | Index Markdown into the fixed catalog. |
| `search` | Text, semantic, or hybrid retrieval from the fixed catalog. |
| `read chunk` | Read one text chunk by public logical ID. `--context neighbors` returns adjacent text chunks derived from the same core representation. |
| `read file` | Read one document resource by public logical ID. |
| `read outline` | Read canonical document headings by file logical ID; heading identities are derived logical IDs, not SQLite or legacy section IDs. |
| `resource import\|export\|inspect\|delete` | Perform one prepared-resource lifecycle operation through the configured catalog; no `--catalog` override exists. |
| `eval retrieval` | Evaluate text, semantic, or hybrid retrieval against the configured catalog; output contains safe ordinal metrics only. |
| `files list` | List document resources in ascending relative-path order with `--page` and `--page-size`. |
| `files info` | Return one document resource by public logical ID. |
| `status`, `doctor` | Report fixed-catalog state and diagnostics. |
| `rebuild fts`, `rebuild embeddings` | Rebuild fixed-catalog derived search data. |
| `model`, `cache` | Manage configured local model/cache behavior without changing catalog selection. |
| `image`, `ingest`, `metadata`, `resources`, `similar`, `find-similar`, `facets` | Operate typed resources in the same fixed catalog. |
| `benchmark`, `storage-analyze` | Read privacy-safe diagnostics from the same fixed catalog. |

## 3f. Unified text search

`mdrack search QUERY --scope all|notes|audio|video|frames|images` uses the
fixed catalog. `--mode text|semantic|hybrid` selects a lexical, vector, or
fused branch; every result uses portable logical identities and source
locators. Current v0.3 preserves the legacy-compatible RRF-only behavior for
hybrid fusion. The command has no alternate-catalog option.

## 3g. Unified provider-free resource similarity

`mdrack find-similar RESOURCE_ID --scope all|notes|audio|video|images` compares
an existing whole-resource vector in the same fixed catalog. It is
provider-free: it does not create embeddings, load a model, or open another
catalog. The embedded equivalent is `MDRackEngine.find_similar_resource`.

### Document identity and file records

`files list`, `files info`, and `read file` return document records whose `id`
and `logical_id` are the same portable document resource ID. Records contain
only the public projection:

```json
{
  "id": "doc_<stable-id>",
  "logical_id": "doc_<stable-id>",
  "root_id": "default",
  "relative_path": "notes/example.md",
  "title": "Example",
  "source_hash": "<sha256-hex>",
  "indexed_at": "<ISO-8601 timestamp>",
  "status": "active",
  "parser_name": "markdown_it",
  "parser_version": "<parser version>",
  "chunk_strategy_name": "structural_blocks",
  "chunk_strategy_version": "2"
}
```

`files list` wraps the records as:

```json
{
  "files": [],
  "pagination": {
    "page": 0,
    "page_size": 20,
    "total": 0,
    "has_next": false
  }
}
```

`files info` and `read file` wrap one record as `{ "file": { ... } }`. A
missing record returns `NOT_FOUND` and a missing catalog returns `STORAGE_ERROR`.

### Chunk reads

`read chunk <logical-id>` returns `{ "chunk": { ... } }`. The public chunk
record contains its logical ID, text content, unit kind, ordinal,
`heading_path`, and a portable `source_locator`. It never exposes an SQLite
row identifier. With `--context neighbors`, the response additionally contains
`neighbors`, ordered from preceding to following ordinal within that text
representation.

A missing chunk returns `NOT_FOUND`; a missing catalog returns `STORAGE_ERROR`.

### Document outlines

`read outline <file-logical-id>` returns `{ "file_logical_id": ..., "headings": [...] }`.
Each heading carries a deterministic `heading_...` logical ID, its `heading_path`,
and optional source line bounds. It does not expose legacy section IDs or SQLite
record IDs. A missing file returns `NOT_FOUND`.

### Resource lifecycle and retrieval evaluation

`resource import`, `resource export`, `resource inspect`, and `resource delete`
operate only on the catalog selected by `--root` and configured `paths.store`.
They do not accept `--catalog`. Inspection exposes only aggregate counts, kinds,
and fingerprints. `eval retrieval --queries PATH` evaluates cases against that
same catalog; its result serializes only ordinal cases, aggregate metrics, and
counts—never query text, source text, file paths, or database IDs.

## Withdrawn command contracts

The following inherited product surfaces are not registered in S2 and therefore
have no normal-operation contract:

- `storage ...`
- `sections ...`
- `read section`

They are not compatibility aliases and must not be used as an alternate catalog
lifecycle. The normal CLI also has no `--catalog` option.

## Database topology

For a successfully initialized normal store, the only SQLite database file is
`<store>/catalog.sqlite3`. SQLite may use transient lock/WAL sidecars while the
catalog is open; those sidecars are not alternate stores. Normal operations do
not create a legacy database, generation directory, or active-pointer file.

## Explicit text ingestion

`mdrack --root ROOT ingest text SOURCE_PATH --source-ref PORTABLE_REF
--media-type text/plain|text/markdown` captures one strict UTF-8 source outside
the scan root and atomically replaces its deterministic `raw_text` resource in
the existing catalog. The command is provider-free and emits only logical IDs,
counts, and `persisted`; source paths, content, digests, and exceptions are not
returned. Search with `--scope all --mode text` exposes portable
`raw_local_source_span` evidence.

## Explicit image ingestion

`mdrack --root ROOT image ingest SOURCE_PATH --resource-id ID
--source-namespace NAMESPACE --source-ref PORTABLE_REF [--caption TEXT]
[--ocr TEXT] [--provider fake|lmstudio]` uses the existing direct-image
surface. `PORTABLE_REF` must be a normalized relative POSIX reference; it is
not a filesystem path. The source must have PNG, JPEG, GIF, or WEBP magic bytes.
The success envelope remains `image ingest` with the existing result keys.
Failures use the payload-free `IMAGE_INGEST_ERROR` envelope.
