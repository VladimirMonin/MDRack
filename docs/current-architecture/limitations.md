# Current limitations

This page is a current-state boundary, not a roadmap promise.

## Retrieval and embeddings

- Semantic retrieval linearly scans JSON-encoded vectors in Python. There is no
  ANN index, vector database, or `sqlite-vec` extension.
- Production embeddings require a reachable LM Studio HTTP endpoint. The Python
  package does not load embedding model weights itself.
- Production reranking is unsupported. Non-null reranker injection and rerank
  requests fail closed; rerank result fields remain null.
- Hybrid retrieval uses unweighted RRF. Configured `text_weight` and
  `semantic_weight` values are currently unused.
- Semantic search does not short-circuit an empty string before invoking the
  provider.

## Parsing and chunking

- `markdown_it` is the default parser; the legacy parser/chunker remains for
  compatibility and A/B baseline use.
- `overlap_chars` is validated and passed to structural configuration but is not
  consumed by `StructuralChunker`. Current structural chunks do not overlap.
- Tables may use a bounded hash marker when one row or header cannot fit. Code
  and Mermaid instead fragment oversized individual lines into exact slices.
- The database stores derived chunk content and provenance, not a complete
  original-document snapshot.

## Images

- Markdown image syntax contributes eligible alt/textual alias only; it creates
  no image resource and never touches the referenced file.
- Direct-image ingestion is explicit. Built-in static extraction requires supplied
  caption/OCR text; live OCR/caption/visual quality is not claimed by offline tests.
- There is no automatic image discovery, remote fetch, perceptual hashing, region
  detection, or binary image storage in SQLite.
- Legacy `0005` asset tables remain in immutable history but have no production
  Markdown indexing owner.

## Public interfaces

- The CLI and engine share retrieval DTOs, but they differ in degradation mapping
  and total available operations.
- Public `read` commands resolve logical identities. Legacy `files list/info` and
  `sections list` still expose or require internal SQLite record identities.
- `MDRackEngine` exposes direct image and resource discovery, but not status,
  doctor, model lifecycle, rebuild, evaluation, or legacy section listing.
- `scan --changed` is accepted but ignored; ordinary scan already performs change
  detection.

## Product scope

MDRack has no GUI, web server, MCP server, cloud embedding provider, specialized
vector database, network asset fetcher, or direct Python model runtime. Adding
one requires an explicit architecture/specification change rather than an
extension inferred from an existing protocol or reserved field.

## Store generations and evidence

- Resource-core search/write requires a verified ready generation. Candidate
  creation/cutover is not an automatic side effect of ordinary legacy commands.
- Retained legacy generations are not automatically deleted; cleanup requires
  separate destructive authorization.
- Linux unit/offline, local SQLite/filesystem, and installed-wheel evidence does
  not prove real-source safety, live external providers, or Windows execution.

## Related current documentation

- [System overview](system-overview.md)
- [Indexing and chunking](indexing-and-chunking.md)
- [SQLite persistence](sqlite-persistence.md)
- [Retrieval](retrieval.md)
- [Assets](assets.md)
- [Public interfaces](public-interfaces.md)
