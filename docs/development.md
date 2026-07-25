# Developing MDRack safely

This guide is the short entry point for coding agents and human maintainers. The
checked-out code, migrations, tests, and routed project instructions are current
truth; documents under `docs/plans/` are historical unless a task explicitly
activates one.

## Repository map

| Path | Responsibility |
|---|---|
| `src/mdrack/domain/` | App domain values and invariants. |
| `src/mdrack/ports/` | Storage, parser, embedding, and related contracts. |
| `src/mdrack/application/` | Canonical indexing, retrieval, read, and generation orchestration. |
| `src/mdrack/adapters/` | Markdown, SQLite compatibility, and LM Studio adapters. |
| `src/mdrack/storage/sqlite/` | App-owned migrations and legacy persistence. |
| `src/mdrack/cli/` | Click composition and JSON presentation. |
| `src/mdrack/public_api/` | Click-free `MDRackEngine`. |
| `packages/mdrack-core/` | Provider/storage-neutral stdlib-only core. |
| `packages/mdrack-sqlite/` | Generic SQLite catalog/search adapter. |
| `packages/mdrack-media/` | Provider-free media records/contracts. |
| `tests/` | Unit, integration, CLI, packaging, privacy, and offline E2E evidence. |
| `docs/current-architecture/` | Maintainer-facing current architecture. |

For the dependency diagram and precise ownership, use the
[current system overview](current-architecture/system-overview.md).

## Read instructions before editing

Start with [`AGENTS.md`](../AGENTS.md). It routes work to focused files in
[`instructions/`](../instructions/):

- architecture/public surfaces → `ARCH.system.instructions.md`;
- SQLite/schema/generations → `DATA.sqlite.instructions.md`;
- tests and evidence → `TEST.quality-gates.instructions.md`;
- current docs and links → `DOCS.architecture.instructions.md`;
- logging/diagnostics/privacy → `OBS.logging.instructions.md`;
- implementation and Git operations → the matching `WORKFLOW.*` instructions.

More-specific project instructions override generic workflow advice. Treat SQL
migrations and executed behavior as stronger evidence than old prose.

## Setup and quality gates

Use the project-managed environment only:

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

Use focused tests while iterating, then run the gates required by the changed
slice. A green fake/offline test does not prove live LM Studio, an installed wheel,
Windows, or a real private corpus. Report exact commands and honest non-claims.

## Change discipline

1. Inspect `git status --short` and preserve unrelated work.
2. Trace the public entry point through application service, port, and adapter;
   check sibling CLI and engine paths before editing.
3. Make the smallest coherent change in the canonical owner. Do not add a vector
   database, direct model runtime, web/GUI/MCP server, or remote asset fetcher.
4. Add a regression for changed behavior and synchronize current contracts/docs in
   the same slice.
5. Review the full diff for scope, generated files, private data, paths, endpoints,
   source content, vectors, and secrets.
6. Stage explicit intended paths only. Commit/push only with explicit authority;
   never clean, reset, rebase, amend, force-push, or rewrite history by default.

SQLite is the only persistent database. LM Studio HTTP is the production embedding
boundary. Markdown scanning never mutates source or opens referenced images.
Public retrieval should expose logical IDs and portable locators, not new SQLite
row IDs. See [current limitations](current-architecture/limitations.md) before
widening scope.

## Documentation and release checks

For documentation changes, verify every relative link and heading anchor and run:

```bash
uv run python scripts/check_release_docs.py
uv run pytest tests/unit/test_release_publication.py tests/unit/test_v13_release_contract.py
uv run python scripts/check_v13_release_packet.py
uv run python scripts/check_v13_sqlite_vec_nonpromotion.py
git diff --check
```

Release evidence is point-in-time evidence. Do not silently rewrite historical
claims to look current. The MDRack 1.3 source-preparation commit was pushed to Git,
but no Git tag, PyPI upload, deployment, Windows run, or live-provider evidence is
implied by that push; see [1.3 release notes](release-1.3.md).
