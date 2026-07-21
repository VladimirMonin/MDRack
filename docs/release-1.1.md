# MDRack 1.1 local release

MDRack 1.1 is the local release of the standalone application. The application
distribution is `mdrack==1.1.0`; the preserved standalone foundation contracts
remain `mdrack-core==1.0.0rc1`, `mdrack-media==1.0.0rc1`, and
`mdrack-sqlite==1.0.0rc1`.

## What works

- Markdown indexing preserves bounded JSON-compatible source metadata and supports
  configured title, alias, lexical, and typed-facet projections.
- Text, semantic, and hybrid retrieval are available through the CLI and
  `MDRackEngine`, with deterministic resource grouping and source locators.
- Whisper JSON, VTT, SRT, and generic timed JSON can be read as supplied
  transcripts; audio and video results carry time evidence.
- Video composition combines transcript passages and frame-caption text without
  storing media binaries or modifying source files.
- Explicit image ingestion and textual image retrieval remain available.
- Prepared-resource manifests, immutable artifact caching, rebuild/rollback
  procedures, and installed-package workflows are available.

## Evidence accepted for this local release

- A closed real-source run imported and retrieved articles, one audio transcript,
  one video transcript with frame-caption text, and 20 direct images. It verified
  23 source hashes unchanged and produced valid temporal evidence. This run was
  accepted as existing evidence and was not repeated during release preparation.
- The provider-free Q1 application run executed 170 frozen cases twice on fresh
  disposable SQLite catalogs with identical logical output, zero Python network
  attempts, zero observed external network syscalls, zero privacy violations, and
  cleanup of both catalogs.
- The release-preparation focused suite passed 88 tests, including the Q1 E2E,
  evaluation/privacy checks, version contract, and CLI version check.
- Eight local artifacts were built: wheel and sdist for `mdrack`, `mdrack-core`,
  `mdrack-media`, and `mdrack-sqlite`. A fresh isolated Python 3.11 environment
  installed all four wheels offline; the installed smoke passed 18 compatibility
  modules, 97 symbols, 23 public API exports, SQLite, audio retrieval, CLI JSON,
  and three legacy retrieval modes with zero network attempts.

## One local performance observation

A single disposable SQLite measurement used 10,000 synthetic vector units with
384 dimensions, candidate limit 100, one warm-up, and five measured repetitions
on Linux/Python 3.11. Median vector-search wall time was 2770.05 ms, p95 was
2833.08 ms, median peak RSS was 51,812 KiB, and the database occupied 24,485,888
bytes. This is a local observation, not a portable latency or memory SLA. SQLite
remains the only persistent backend and vector search remains a linear Python
scan.

## Honest limitations

- MDRack consumes supplied transcripts and frame-caption text. It does not
  transcribe raw audio, decode media, or provide acoustic similarity.
- There is no pixel, visual-embedding, or perceptual image search. Image and frame
  retrieval is text-first.
- Provider-free vectors and the accepted real-source run do not prove universal
  semantic quality from a live embedding model.
- Production embeddings require an explicitly configured reachable LM Studio HTTP
  endpoint. No live provider call was made for this release preparation.
- Production reranking, ANN/vector extensions, Windows execution, and Python 3.12
  execution are not claimed here.
- No tag or package-index publication is part of this local release.

The detailed current limits remain in
[current limitations](current-architecture/limitations.md). Q1 synthetic
measurements and their boundary are recorded in
[quality observations](evaluation/v1.1-quality-observations.md) and
[offline evaluation evidence](evidence/v1.1-offline-evaluation.md).
