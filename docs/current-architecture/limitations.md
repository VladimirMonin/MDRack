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
- Default production embeddings require a reachable LM Studio text HTTP endpoint.
  Explicit trusted outer host/CLI composition may instead select a caller-owned bounded
  native process via prepared-provider injection; see [ADR-0018](../decisions/0018-caller-owned-native-encoder.md).
  Core/application/default providers and Python do not load model weights/vendor DLLs.
  Separate [short-text A functional qualification](../evaluation/text-provider-functional-2026-10-24.md)
  covers plain DOCUMENT/existing prefixed QUERY and actual no-argument caller factories
  at context 2048, not an 8 MiB/long-context guarantee or multilingual semantic quality.
  Server-returned unit norms are observed, not adapter normalization. Selected CUDA /
  GPU-max/residency do not establish HTTP text-forward/kernel device; that remains unknown.
  Caller-private factory glue is not installed product support. The existing runner's
  native B/E text-document and required full-matrix constraint remain open.
- Legacy raw audio/video textual paths still consume supplied transcripts/frame captions
  or explicit local STT/extractor output. The separate 1.4.0rc3
  [native temporal component](../contracts/native-temporal-v1.md) performs bounded
  actual CPU PNG-frame/PCM-audio/visual-MP4 preparation, with independently declared
  image/audio/direct-video typed capabilities and vector-only units. FFmpeg/ffprobe
  are explicit existing local tools, not an STT/VLM/model or general format guarantee.
  V1 supports WAVE/ISO-BMFF with matching tracks, zero stream origins and bounded
  integer-ms coordinates; unsupported formats/timelines/ranges fail closed.
- Production selected LM Studio embeddings remain textual. In the scoped loaded-model
  qualification, LM Studio 0.4.26+4 `/v1/embeddings` returned text200/finite768 but refused
  typed PNG content400 at its string-only input guard before embedding. Runtime2.63.0
  is selection metadata, not an observed GPU-kernel identity. Model Vision metadata
  does not establish API pixel admission; this is not a negative claim about the model,
  generic prepared-provider capabilities or all future LM Studio implementations.
  See the [input qualification](../contracts/native-image-v1.md#lm-studio-input-qualification).
  The prior [external-host canary](../evaluation/native-image-cuda-canary-2026-10-23.md)
  and later [normal product qualification](../evaluation/native-image-product-2026-10-23.md)
  are distinct: the latter actually exercises engine/catalog/source CLI/installed PE,
  public original opens/reopen and zero-encode same-resource lexical enrichment with
  an explicitly selected external provider. It does not repair the HTTP wrapper.
  CPU PNG decoding/L2, sampled rather than all-operator CUDA proof, separate full-space
  identity and unqualified text-document/image-query/similarity remain explicit.
  Only bounded fixed-size RGB PNG and text-query-for-image roles are qualified; no audio /
  video inference, independent semantic quality or portable latency/SLO follows. Fake
  synthetic providers still prove orchestration only. Real-corpus/held-out/public A–D /
  quality-cost/W6 gates remain separate.
- Local capture, source guards, CPU decoding and child cleanup remain app-owned.
  `mdrack_core` and `mdrack_media` never open sources or run processes. Pure media
  builders now accept native ready observations separately from genuine textual
  artifacts; shared Core validation and SQLite persistence/publication stay unchanged.
- Unified 1.2 retrieval is text-first: it searches Markdown text, supplied audio/
  video transcripts, frame-caption text, and explicit image caption/OCR text. Its
  `find-similar` command reuses stored textual whole-resource vectors only; it does
  not call a provider and does not accept the frame-only query scope.
- Production reranking is unsupported. Non-null reranker injection and rerank
  requests fail closed; rerank result fields remain null.
- Standard Markdown hybrid retrieval applies configured `text_weight` and
  `semantic_weight`; a zero-weight branch is omitted before provider/storage
  execution. Low-level services without configuration retain equal defaults.
- Semantic-only search does not short-circuit an empty string before invoking the
  provider. Ordinary application hybrid search instead rejects empty/whitespace
  queries before provider preparation or reader entry.
- The bounded synthetic measurements in
  [v0.3.1 offline evidence](../evidence/v0.3.1-release-gate.md) are observations
  from one Linux host, not a portable latency/RSS support SLA. Larger matrix
  cells remain unrun.
- Provider-free vectors and the accepted real-source media run prove application
  wiring, timing evidence, source integrity, and textual retrieval. They do not
  establish universal semantic quality from a live embedding model.

## Search response consistency

- The [operation-owned search snapshot](README.md#operation-owned-search-snapshots)
  covers one ordinary application response. A later independent read, metadata
  inspection, export or enrich request may observe a newer commit; there is no
  shared filesystem snapshot or publication/CAS token in the search response.
  The separate conditional-publication API does not extend a search transaction
  across later enrich work or the external filesystem.
- Standalone services and injected legacy storage without the optional app
  read-session factory retain their legacy consistency boundary. A catalog context
  manager alone owns lifetime, not a multi-call search snapshot.
- Query-provider/HTTP preparation is outside the owned transaction. Existing-vector
  similarity reads its basis, candidates and hydration together without a provider.
  A future consumer needing a basis before HTTP must capture immutable values,
  close, perform HTTP and revalidate in a new session, not keep a borrowed reader.
- Each ordinary uninjected operation opens and fully verifies a readonly catalog.
  Verification preflight and the subsequent search use separate snapshots. The
  [current synthetic scaling measurements](../evaluation/catalog-search-scaling-2026-10-23.md)
  separate verified open from already-open search and default repeated queries. On
  the measured Windows source boundary at 8192 units/vectors, median verified open
  was 6748.172 ms and default repeated selective lexical search 5547.531 ms; already-open
  injected selective search 0.997 ms is a different read-session composition. No applicable
  SLO, portable latency guarantee or concurrent-write safety of that injection was
  established. Do not weaken verification or snapshot policy to adopt a warm timing.
- Deterministic published-catalog interleavings were checked on Windows/CPython
  3.14.2 with a writer-ready barrier. A retained Windows SHM/topology bootstrap
  `PermissionError` observation is not claimed fixed; arbitrary simultaneous
  bootstrap/open topology remains outside that evidence.

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
  activation, automatic rollback, retention or automatic migration mode. Explicit
  offline `rebuild upgrade` supports only exact completed clean v2/0004 to current
  v2/0005, with all consumers stopped/closed and verified backup outside store.
  It never adopts v1/app-bridge/unknown/damaged/incomplete catalogs; see
  [ADR-0014](../decisions/0014-conditional-publication-and-v2-upgrade.md).
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
