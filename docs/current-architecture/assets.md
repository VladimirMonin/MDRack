# Images: Markdown projection and direct ingestion

MDRack 0.3 deliberately separates Markdown image syntax from explicit image
ingestion. Neither path fetches remote data or mutates source files.

## Markdown image policy

The markdown-it adapter recognizes Markdown images, Obsidian embeds, and HTML
`img` syntax only to project eligible human-readable text into normal prose:

- a non-empty Markdown/HTML alt value is kept once;
- a non-empty textual Obsidian alias is kept once;
- bare, empty, or numeric aliases contribute no text;
- target paths, `src`, titles, and dimensions contribute no text or metadata;
- surrounding prose remains in its original order and is not duplicated.

The scanner does not resolve, stat, open, hash, MIME-probe, or diagnose the
referenced file. Production Markdown indexing creates no asset graph,
`image_reference` chunk, or image resource. Legacy `0005` tables remain only
because historical migrations are immutable.

## Explicit direct-image ingestion

`mdrack image ingest` and the corresponding `MDRackEngine` methods operate only
on a caller-selected local file. The application reads the file to establish its
media type, byte size, and content hash, obtains caption/OCR text from explicit
arguments or an injected extractor, prepares ready vectors outside core, and
atomically replaces one typed image resource in a ready resource-store generation.

The source bytes remain outside SQLite. Each bounded caption/OCR representation
owns one `whole_resource` search unit by default. Text and visual vectors use
different explicit embedding spaces and are never compared across spaces.

Image text, semantic, and hybrid search apply `resource_kind=image` before the
candidate limit. Provider failure degrades safely. Delete is idempotent and
removes only the derived resource graph, never the source file.

## Duplicate and similarity discovery

- Exact duplicates compare persisted byte `content_hash` values and return other
  logical resource IDs in stable order.
- Similarity starts from an existing whole-resource unit/vector in an explicit
  space. By default, every unit from the query resource is excluded before the
  result limit.
- Facet and typed scope filters are applied in the adapter before top-k.

## Explicit non-capabilities

MDRack does not claim live OCR/caption quality, automatic image discovery from
Markdown, perceptual near-duplicate hashing, image regions, remote fetch, binary
storage in SQLite, or source mutation. Deterministic fake and local SQLite tests
do not prove a live visual provider.

## Primary source anchors

- Markdown text projection: `src/mdrack/adapters/markdown_it/parser.py`
- Explicit image pipeline: `src/mdrack/ingestion/images.py`
- CLI/API: `src/mdrack/cli/commands/images.py`, `src/mdrack/public_api/engine.py`
- Resource persistence: `src/mdrack/adapters/sqlite/resource_store.py`
- Resource schema: `src/mdrack/storage/sqlite/migrations/0007_resource_core.sql`
- Discovery: `src/mdrack/application/resources.py`