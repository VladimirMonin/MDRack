# MDRack

Local Python 3.11+ command-line and embedded Markdown knowledge rack for AI
agents.

MDRack indexes Markdown into structural retrieval chunks, stores metadata,
FTS5 indexes, JSON-encoded vectors, and asset provenance in SQLite, obtains
embeddings through an LM Studio HTTP endpoint, and returns stable JSON results
with portable source locators.

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
2. Structural chunker v2 applies separate prose, Python, code, table, Mermaid,
   and image-reference policies.
3. A per-file SQLite transaction replaces sections, chunks, FTS rows, optional
   vectors, assets, and references atomically.
4. Retrieval uses FTS5 text ranking, LM Studio query embeddings with a Python
   cosine scan, or deterministic application-level RRF fusion.
5. CLI and embedded search return the same logical identities, score/rank
   fields, heading arrays, and `SourceLocator` shape.

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
- [Recovery procedures](docs/recovery.md)
- [ADR-0001: reranking deferred](docs/decisions/0001-reranking-deferred.md)

Files under `docs/plans/` and the legacy architecture/design documents are
historical unless explicitly marked as an active plan. They are not the current
product contract.

## Image assets

The structural parser records Markdown images, Obsidian embeds, and HTML `img`
references as a portable local asset graph. Search uses only alt text and
immediate adjacent document text. MDRack performs no OCR, vision inference,
visual embedding, network fetch, or source/asset mutation.

## Known limitations

- SQLite is the only persistent database. Semantic search linearly scans
  JSON-encoded vectors in Python; there is no ANN/vector extension.
- Structural `overlap_chars` is currently not consumed, so structural chunks do
  not overlap.
- Production reranking is disabled. `rerank_rank` and `rerank_score` remain
  `null`; non-null reranker injection fails closed.
- Legacy `files` and `sections` inspection commands still expose internal record
  IDs, unlike public logical-ID read and retrieval contracts.

See the complete [limitations ledger](docs/current-architecture/limitations.md).

## Verification and recovery

Run the complete offline verification suite with `scripts/verify.sh` on Linux or
`scripts/verify.ps1` on Windows. Migration, reindex, model-change, and rollback
procedures are documented in [recovery](docs/recovery.md).

For a reproducible Windows executable build, see
[Windows EXE build](docs/windows-exe-build.md).

## License

MIT
