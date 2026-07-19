# MDRack

MDRack 0.3 is a local Python 3.11+ command-line and embedded retrieval rack for
Markdown documents and explicitly supplied images.

The application depends on standalone `mdrack-core` and `mdrack-sqlite`
distributions. The first is the stdlib-only provider/persistence-neutral kernel;
the second is the stdlib-plus-core generic resource catalog/search adapter. The
`mdrack` distribution owns Markdown/image ingestion, app migration generations,
LM Studio integration, Click JSON commands, and `MDRackEngine`; it vendors neither
package.

## Quick start

```bash
uv sync
uv run mdrack --help
mkdir -p ./notes
uv run mdrack --root ./notes init
uv run mdrack --root ./notes scan
uv run mdrack --root ./notes search "architecture" --mode text
```

The CLI also provides read, files, sections, status, doctor, rebuild, eval, and
LM Studio model-management commands. Host applications can use
`MDRackEngine` without importing Click.

## How it works

1. The default markdown-it adapter parses UTF-8 Markdown into source blocks
   with H1–H6 heading paths and exact line/character provenance.
2. Markdown image syntax contributes only safe textual alt/alias prose; paths and
   referenced files are never inspected or indexed as assets.
3. The app projects documents into typed core resources and writes a complete
   graph atomically to a ready SQLite store generation.
4. Core retrieval accepts ready lexical/vector branches, applies scope filters
   before limits, groups resource evidence, and performs deterministic weighted RRF.
5. Explicit image ingestion stores derived caption/OCR text and ready vectors,
   never source bytes; duplicate and whole-resource similarity use logical IDs.

## Documentation

- [Current architecture index](docs/current-architecture/README.md)
- [System overview](docs/current-architecture/system-overview.md)
- [Indexing and structural chunking](docs/current-architecture/indexing-and-chunking.md)
- [SQLite persistence and current schema](docs/current-architecture/sqlite-persistence.md)
- [Text, semantic, and hybrid retrieval](docs/current-architecture/retrieval.md)
- [Asset handling](docs/current-architecture/assets.md)
- [CLI and embedded interfaces](docs/current-architecture/public-interfaces.md)
- [Current limitations](docs/current-architecture/limitations.md)
- [CLI contracts](docs/cli-contracts.md)
- [v0.3 compatibility registry](docs/compatibility/v0.3-compatibility-registry.md)
- [Recovery procedures](docs/recovery.md)
- [ADR-0001: reranking deferred](docs/decisions/0001-reranking-deferred.md)
- [ADR-0002: provider/storage-neutral core](docs/decisions/0002-provider-storage-neutral-core.md)
- [ADR-0004: SQLite operating envelope](docs/decisions/0004-sqlite-operating-envelope.md)
- [v0.3 release evidence](docs/evidence/v0.3-release-gate.md)

Files under `docs/plans/` and the legacy architecture/design documents are
historical unless explicitly marked as an active plan. They are not the current
product contract.

## Images

Markdown image syntax never starts image ingestion. It preserves eligible alt or
textual alias once as ordinary prose and discards target/path/title/dimensions.
`mdrack image ingest` is a separate explicit local-file operation. Caption/OCR
text is caller-supplied or produced by an injected extractor; live LM Studio use
requires an explicit provider choice. Source bytes remain outside SQLite and are
never modified.

## Known limitations

- SQLite is the only persistent database. Semantic search linearly scans
  JSON-encoded vectors in Python; there is no ANN/vector extension.
- Structural `overlap_chars` is currently not consumed, so structural chunks do
  not overlap.
- Production reranking is disabled. `rerank_rank` and `rerank_score` remain
  `null`; non-null reranker injection fails closed.
- Legacy `files` and `sections` inspection commands still expose internal record
  IDs; new resource/image/search contracts expose logical IDs only.
- The resource-core schema lives in candidate store generations. Only a verified
  `ready` generation may serve resource search/write; cleanup is never automatic.

See the complete [limitations ledger](docs/current-architecture/limitations.md).

## Verification and recovery

Run the complete offline verification suite with `scripts/verify.sh` on Linux or
`scripts/verify.ps1` on Windows. Release acceptance additionally builds wheel and
sdist and runs `scripts/check_installed_package.py` from an isolated installed
wheel outside the source tree. Migration, generation cutover, rollback, and
retention procedures are documented in [recovery](docs/recovery.md).

For a reproducible Windows executable build, see
[Windows EXE build](docs/windows-exe-build.md).

## License

MIT
