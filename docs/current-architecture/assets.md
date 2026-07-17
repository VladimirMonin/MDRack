# Assets

MDRack indexes image references as local provenance. It does not inspect image
semantics or turn images into embeddings.

## Supported references

The markdown-it adapter recognizes:

- Markdown images such as `![alt](path)`;
- Obsidian embeds such as `![[path|alias]]`;
- HTML `img` references.

Standalone references inside paragraphs are separated into exact
`IMAGE_REFERENCE` source blocks. Each block retains syntax, raw reference,
heading path, alt text, surrounding text when present, and line/offset span.

## Safe local resolution

`build_asset_graph` URL-decodes local paths, normalizes separators, and resolves
them beneath the configured root. It rejects:

- URI schemes and network-relative targets as `external_reference`;
- absolute paths, drive-like targets, empty targets, and traversal escapes as
  `unsafe_reference`;
- valid local paths that do not exist as `missing`.

For an existing file, MDRack records SHA-256, MIME guess, byte size, and PNG/GIF
dimensions when readable. No network fetch or source mutation occurs.

## Searchable text versus provenance

An image reference contributes searchable content only from deduplicated:

1. explicit alt text;
2. immediate adjacent non-image text from the parsed document.

If searchable text exists, the structural chunker creates exactly one bounded
`image_reference` chunk for the source block. If no searchable text exists, the
asset reference is persisted without a retrieval chunk. Ambiguous mapping of one
image block to more than one retrieval chunk raises an error instead of choosing
silently.

Raw reference text, resolved path, hashes, dimensions, and resolution status are
provenance. They are not visual descriptions.

## Persistence

- `assets` owns root-relative asset identity and optional file metadata.
- `asset_references` links one source occurrence to a file, block, optional
  retrieval chunk, raw syntax, exact source span, searchable text, and resolution
  status.
- `asset_descriptions` reserves `(asset_id, description_kind)` rows, but current
  production code has no reader or writer for descriptions.

Storage ports and the SQLite adapter can list assets/references for a file, but
there is no public asset CLI command and no `MDRackEngine` asset method today.

## Explicit non-capabilities

Asset indexing does not perform OCR, vision inference, captioning, visual
embeddings, remote download, or modification of Markdown or asset files.

## Primary source anchors

- Parsing and adjacent text: `src/mdrack/adapters/markdown_it/parser.py`
- Safe resolution and metadata: `src/mdrack/application/assets.py`
- One bounded searchable chunk: `src/mdrack/application/chunking.py`
- Domain records: `src/mdrack/domain/assets.py`
- Schema: `src/mdrack/storage/sqlite/migrations/0005_assets.sql`
- Persistence: `src/mdrack/adapters/sqlite/index_storage.py`
