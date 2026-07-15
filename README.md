# MDRack

Local command-line Markdown knowledge rack for AI agents.

MDRack indexes Markdown files, splits them into structural chunks, stores
metadata and search indexes in SQLite, creates embeddings through LM Studio,
and lets agents search, inspect, and retrieve document context via stable
JSON commands.

## Quick start

```bash
uv sync
uv run mdrack --help
```

Run the complete offline verification suite with `scripts/verify.sh` on Linux or
`scripts/verify.ps1` on Windows. Migration, reindex, model-change and rollback
procedures are documented in `docs/recovery.md`.

## Image assets

The structural parser indexes Markdown images, Obsidian embeds and HTML `img`
references as a portable asset graph. Search uses only alt text and adjacent
document text. MDRack performs no OCR, vision inference, visual embedding,
network fetch or source-asset mutation.

## Known limitations

### Reranking is disabled in v0.2

`Qwen3-Reranker-0.6B-Q8_0-GGUF` can be discovered and loaded by LM Studio,
but LM Studio does not expose a documented reranking API. MDRack therefore
uses FTS and semantic candidates fused with deterministic RRF without a
production reranking stage.

The reranker protocol, DTOs, nullable result fields, and deterministic test
adapter remain for future integration. They do not represent production
reranking. In normal v0.2 results, `rerank_rank` and `rerank_score` are `null`.
See [ADR-0001](docs/decisions/0001-reranking-deferred.md).

## Windows EXE

For a reproducible Windows build, see `docs/windows-exe-build.md`.

## License

MIT
