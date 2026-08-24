# Current limitations

This page is a current-state boundary, not a roadmap promise.

## Retrieval and embeddings

- Semantic retrieval linearly scans canonical binary vectors in Python. The clean-v2
  application catalog uses the `ieee754-f32-le-v1` codec with the builtin exact
  backend; standalone catalogs retain a float64 compatibility default and legacy
  JSON is diagnostic read-only. There is no ANN index, vector database, or
  `sqlite-vec` dependency in the 1.3.0 base distribution.
- The separate `mdrack-sqlite-vec` source package is an experimental compatibility
  probe, not a supported accelerator. Its pinned `sqlite-vec==0.1.9` local probe
  fails the deterministic tie-boundary gate; the corresponding
  [non-promotion packet](../evidence/v1.3.0-sqlite-vec-nonpromotion.json) records
  the fail-closed decision. MDRack provides no sqlite-vec application extra,
  production backend, or partial-write fallback.
- Production embeddings require a reachable LM Studio HTTP endpoint. The Python
  package does not load embedding model weights itself.
- Audio and video retrieval consumes supplied timed transcripts and frame-caption
  text. The CLI now has a narrow direct WAVE path for raw audio that delegates transcription
  to an explicitly supplied local stdin executable; MDRack does not provide an
  STT model, provider, network fallback, or general media decoder. Matroska,
  acoustic similarity, pixel search, and visual similarity remain unsupported.
  Direct raw video is limited to ISO-BMFF `ftyp` input plus an
  explicitly authorized local extractor returning strict timed transcript and
  selected-caption JSON; MDRack does not decode video or provide FFmpeg/STT/VLM
  quality guarantees.
- Unified 1.2 retrieval is text-first: it searches Markdown text, supplied audio/
  video transcripts, frame-caption text, and explicit image caption/OCR text. Its
  `find-similar` command reuses stored textual whole-resource vectors only; it does
  not call a provider and does not accept the frame-only query scope.
- Production reranking is unsupported. Non-null reranker injection and rerank
  requests fail closed; rerank result fields remain null.
- Standard Markdown hybrid retrieval applies configured `text_weight` and
  `semantic_weight`; a zero-weight branch is omitted before provider/storage
  execution. Low-level services without configuration retain equal defaults.
- Semantic search does not short-circuit an empty string before invoking the
  provider.
- The bounded synthetic measurements in
  [v0.3.1 offline evidence](../evidence/v0.3.1-release-gate.md) are observations
  from one Linux host, not a portable latency/RSS support SLA. Larger matrix
  cells remain unrun.
- Provider-free vectors and the accepted real-source media run prove application
  wiring, timing evidence, source integrity, and textual retrieval. They do not
  establish universal semantic quality from a live embedding model.

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
- Direct images require PNG, JPEG, GIF, or WEBP magic bytes and a portable
  relative POSIX source reference. The source-after-read guard protects the
  existing atomic replacement, but does not provide a live filesystem lock.
- There is no automatic image discovery, remote fetch, perceptual hashing, region
  detection, or binary image storage in SQLite.
- Legacy `0005` asset tables remain in immutable history but have no production
  Markdown indexing owner.

## Public interfaces

- The CLI and engine share retrieval DTOs, but they differ in degradation mapping
  and total available operations.
- Public `read` and `files` commands resolve logical identities. Internal SQLite
  row identities are not a public application contract.
- `MDRackEngine` exposes direct image and resource discovery, but not status,
  doctor, model lifecycle, rebuild, evaluation, or legacy section listing.
- `scan --changed` is accepted but ignored; ordinary scan already performs change
  detection.

## Product scope

MDRack has no GUI, web server, MCP server, cloud embedding provider, specialized
vector database, network asset fetcher, or direct Python model runtime. Adding
one requires an explicit architecture/specification change rather than an
extension inferred from an existing protocol or reserved field.

## Store lifecycle and evidence

- Normal startup supports exactly one physical database at
  `<store>/catalog.sqlite3`, verifies the clean-v2 identity, and fails closed on
  another SQLite main file or an obsolete lifecycle artifact. Existing v1,
  app-bridge, and legacy stores are unsupported; MDRack does not read, copy, or
  migrate them automatically.
- First creation is exclusive and failure-cleaned. There is no candidate,
  activation, rollback, retention, or migration mode in the application CLI.
- Linux unit/offline, local SQLite/filesystem, and installed-wheel evidence does
  not prove real-source safety, live external providers, or Windows execution.
  The [one-store acceptance runner](../one-store-acceptance.md) adds a bounded
  synthetic fixture and temporary wheel-target check; it has the same limits.
- A private real-corpus unified-search smoke is a separate explicit data-
  authorization boundary. The 1.2 offline release evidence does not claim that
  boundary, raw-media recognition, or live LM Studio semantic quality.
- ADR-0012 and the app-owned raw-source provenance values define the bounded
  contract for raw-local adapters. R3 adds only CLI WAVE ingestion through an
  opt-in shell-free local command; offline fake-command evidence is orchestration
  evidence, not STT recognition quality. No engine method or schema is added.

## Related current documentation

- [System overview](system-overview.md)
- [Indexing and chunking](indexing-and-chunking.md)
- [SQLite persistence](sqlite-persistence.md)
- [Retrieval](retrieval.md)
- [Assets](assets.md)
- [Public interfaces](public-interfaces.md)

Raw text R1 is limited to strict UTF-8 plain text and Markdown selected outside
the scan root. It performs no provider, model, OCR, STT, VLM, network, or
external asset work, and has no semantic embedding path. The installed CLI
smoke remains provider-free; Windows and private real-source quality are not
claimed.
