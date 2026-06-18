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

## Windows EXE

For a reproducible Windows build, see `docs/windows-exe-build.md`.

## License

MIT
