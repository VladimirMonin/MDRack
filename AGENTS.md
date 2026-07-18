# AGENTS.md — MDRack agent router

## Project

MDRack is a local Python 3.11+ CLI and embedded library for indexing Markdown,
storing structural metadata and search indexes in SQLite, creating embeddings
through an LM Studio HTTP endpoint, and returning stable JSON retrieval results.

- Repository/workdir: repository root of the current checkout
- Kanban board: `mdrack`
- Package manager: `uv`
- Persistent database: SQLite only
- Public entry points: Click CLI and `MDRackEngine`

## Start here

1. Read this router.
2. Inspect `git status --short`; preserve unrelated changes.
3. Load only instructions matching the files and operation below.
4. Treat current code and migrations as authoritative over prose and historical plans.
5. Make the smallest scoped change and verify it with real commands.

## Setup and verification

```bash
uv sync --all-extras
uv run pytest
uv run ruff check src/ tests/
uv run ruff check packages/mdrack-core/src/ packages/mdrack-sqlite/src/
uv run mypy packages/mdrack-core/src/mdrack_core packages/mdrack-sqlite/src/mdrack_sqlite
uv run python scripts/check_no_forbidden_deps.py
uv run python scripts/check_core_boundaries.py
uv run python scripts/check_sqlite_boundaries.py
git diff --check
```

Use `uv run` for Python commands. Do not use the system `pip` or create an
untracked package-manager workflow.

## Instruction routing

| Trigger or scope | Load |
|---|---|
| Architecture, module boundaries, public surfaces, dependency direction | `instructions/ARCH.system.instructions.md` |
| SQLite schema, migrations, repositories, FTS, vectors, assets, identity | `instructions/DATA.sqlite.instructions.md` |
| Tests, fixtures, quality gates, evidence claims | `instructions/TEST.quality-gates.instructions.md` |
| Current architecture docs, diagrams, links, historical/current labeling | `instructions/DOCS.architecture.instructions.md` |
| Create or edit `AGENTS.md` or `instructions/` | `instructions/DOCS.instruction-style-guide.instructions.md` |
| Investigate, plan, estimate, or decompose without implementation | `instructions/WORKFLOW.plan-orchestration.instructions.md` |
| Implement, fix, refactor, or edit project files | `instructions/WORKFLOW.build-orchestration.instructions.md` |
| Commit, merge, switch branch, resolve conflicts, push, or alter history | `instructions/WORKFLOW.git-safety.instructions.md` |
| Turn requirements into an implementation-ready specification | `instructions/WORKFLOW.requirements-to-spec.instructions.md` |
| Add, change, or audit logging and diagnostics | `instructions/OBS.logging.instructions.md` |
| Explicit visual/image inspection or MCP-assisted visual evidence | `instructions/TOOLING.mcp-instrument.instructions.md` |

More-specific instructions override broader workflow guidance. If an instruction
contradicts verified code, stop, follow the code for the immediate task, and repair
the stale instruction in the same documentation slice when allowed.

## Product invariants

- Application services normally depend on domain types and ports; adapters implement ports.
  The current bounded exception is `IndexingService`, which imports and constructs
  `MarkdownItParser` as its default when no parser is injected. Do not broaden this
  concrete dependency; changing it requires a scoped architecture task.
- Click must not leak into `MDRackEngine` or application/domain code.
- SQLite is the only persistent database. Do not add a vector database.
- `packages/mdrack-sqlite/` is the single generic resource catalog/search adapter
  owner; app compatibility paths delegate/re-export and package code never imports `mdrack`.
- Embeddings use LM Studio over HTTP. Do not add `torch`, `transformers`,
  `sentence-transformers`, or direct model loading.
- Indexing must not modify source Markdown or fetch external assets.
- Public retrieval uses logical identities and portable source locators. Do not
  expose new SQLite record IDs as public contracts.
- Production reranking remains unsupported unless an approved architecture change
  updates code, tests, contracts, ADRs, and instructions together.
- Do not add a GUI, web server, or MCP server to MDRack.
- Keep PEP 8, type hints, and justified `# noqa` usage.
- Production code uses `logging`; stdout is reserved for documented CLI output.
- Never commit credentials, user content, absolute private paths, databases,
  generated indexes, logs, caches, or vault material.

## Source-of-truth hierarchy

1. Runtime behavior and public tests.
2. SQL migrations `src/mdrack/storage/sqlite/migrations/0000...` onward.
3. Domain types, ports, application services, adapters, CLI, and public API.
4. Current architecture and contract documentation.
5. Historical plans, which explain intent but not current behavior.

Current contract references include `docs/cli-contracts.md` and
`docs/decisions/0001-reranking-deferred.md`. Files under `docs/plans/` and legacy
root plans are historical unless explicitly marked active for the current task.

## Change discipline

- Respect the active task's editable/read-only boundaries.
- Do not add features outside the approved scope or dependencies without a recorded reason.
- Update schema documentation and migration notes with every schema change.
- Update routing whenever an instruction is added, renamed, or removed.
- Update current documentation when architecture, public contracts, limitations,
  or verification commands change.
- Do not claim live, external, or full-suite evidence that was not actually run.

## Git boundary

Read-only Git inspection is allowed. Commits, pushes, branch changes, merges,
rebases, resets, staging, and history edits require explicit task/user scope and
`WORKFLOW.git-safety.instructions.md`. Never initialize, commit, or push merely
because the repository exists; this repository has an `origin`, but remote state
must be checked live before any remote claim.
