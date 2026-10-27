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
| `src/mdrack/application/` | Canonical indexing, retrieval, read, and fixed-catalog composition. |
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
- SQLite/schema/catalog → `DATA.sqlite.instructions.md`;
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
uv run ruff check packages/mdrack-core/src/ packages/mdrack-media/src/ packages/mdrack-sqlite/src/
uv run mypy src/mdrack packages/mdrack-core/src/mdrack_core packages/mdrack-media/src/mdrack_media packages/mdrack-sqlite/src/mdrack_sqlite scripts/run_native_evaluation.py --no-incremental --show-error-codes
uv run python scripts/check_no_forbidden_deps.py
uv run python scripts/check_core_boundaries.py
uv run python scripts/check_media_boundaries.py
uv run python scripts/check_sqlite_boundaries.py
uv run python scripts/check_documentation.py --check
git diff --check
```

Use focused tests while iterating, then run the gates required by the changed
slice. For the fixed SQLite acceptance slice, run the
[one-store acceptance runner](one-store-acceptance.md) with an evidence directory
outside the checkout; it covers the synthetic fixture and a temporary wheel target.
A green fake/offline test does not prove live LM Studio, Windows, or a real private
corpus. Report exact commands and honest non-claims.

### Mixed-folder regression boundary

For `folder add` / `MDRackEngine.import_folders`, the focused local-component set is:

```bash
uv run python -m pytest -q tests/cli/test_cli_folder.py tests/unit/test_folder_import.py tests/integration/test_folder_import.py tests/integration/test_folder_storage_alias_safety.py
```

These checks use explicit synthetic sources/providers and real local SQLite/filesystem.
They cover literal TXT/spans, document/image routing and original locators, two-root
identity/isolation, exclusions/link policy, honest partial outcomes, privacy and
fresh-process zero-encode/no-duplicate replay. The storage-owner regression exercises
direct and real Windows junction aliases for store/cache through engine and registered
CLI, including absent final leaves, no source-tree writes/no encoding, plus permitted
disjoint layouts. Alias cells may skip when Windows junction creation is unavailable;
a run with those skips does not qualify that Windows boundary. Do not replace normal
build isolation or run a native model merely to test this orchestration. Full
reconciliation/unified search, dedicated installed release and personal-corpus
quality/SLO remain separate. Current contracts are
[folder indexing](current-architecture/indexing-and-chunking.md#selected-mixed-folder-indexing)
and [CLI outcomes](cli-contracts.md#selected-mixed-folders).

When v1 evaluation inputs share a directory with the four versioned v2 schemas, use:

```bash
uv run python scripts/check_v11_input_freeze.py --check
```

This adapter verifies the original 66 inputs and invokes the unchanged generator on an
owned temporary original-input view. It admits only the named v2 additions, writes no
source inputs and does not refresh judgments or results. See the
[evaluation contracts](evaluation/README.md#current-tree-historical-v1-check).

Offline packaging keeps normal PEP 517 isolation. Provision the pinned build backend
and runtime wheels into an approved local wheelhouse before the offline run; an absent
backend is a setup failure, not evidence that isolation should be disabled. Verify all
four installed distribution origins and exercise the registered console launcher when
claiming installed CLI support. Source `-I -B -S`/callable checks are not that launcher:
a normal Windows PE starts Python normally and needs its own pre-import/no-user-site/
no-`.pth`/safe-path qualification. Neither boundary proves another OS or live inference.

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

For documentation changes, verify relative links and heading anchors, use the bounded
maintenance route below, and run the affected public-document consumers:

```bash
uv run pytest tests/unit/test_release_publication.py tests/unit/test_v13_release_contract.py
git diff --check
```

The exact `scripts/check_v13_release_packet.py` validator belongs to the historical
1.3 release candidate, not ordinary documentation delivery of later source bytes.
Keep that packet strict and unchanged; a new release candidate needs its own explicit
qualification. `scripts/check_v13_sqlite_vec_nonpromotion.py` separately checks the
portable frozen experiment packet when that contract changes. The older
`scripts/check_release_docs.py` validates a historical v0.4 packet and is not a current
release gate.

## Bounded documentation maintenance

[Proposed native-media programme](proposals/native-media/README.md) preserves the full
01 → 02 → 04 → 03 design bundle, separate from current architecture. Source baseline
and original verification fields are historical; editorial dates do not renew evidence.
The [DOCS instruction](../instructions/DOCS.architecture.instructions.md) owns metadata
and knowledge lifecycle; [Foundation provenance](provenance/project-foundation-kit.md)
records selective adaptation and full applicable MIT notices.

```bash
uv run --no-sync --offline python scripts/check_documentation.py --update-index
uv run --no-sync --offline python scripts/check_documentation.py --check
uv run --no-sync --offline pytest tests/unit/test_documentation.py tests/unit/test_verification_wrappers.py
```

Use pinned uv 0.11.15 and an owned environment/cache/tmp root; provision locked
requirements separately before offline checks. Do not manually repair .pth/PYTHONPATH
or refresh lock to conceal setup failures. An external UV_PROJECT_ENVIRONMENT leaves
an inherited environment untouched. The checker defaults to its script-relative root;
`--project ROOT` supports a synthetic project from another cwd. A mode is required.
Exit 0 means structural success, 1 document errors, 2 input/usage failure.

The fixed scope file explicitly selects authoring/native Markdown. Local target
existence is checked inside root; only selected bodies/anchors are read, otherwise
not_checked. External URLs are counted, never requested. Supported: simple inline/image
and full/collapsed reference links, ATX headings, explicit HTML IDs; fences/code spans
are ignored as syntax. Nested destinations/brackets, shortcut references, Setext/full
GFM, renderer-specific anchors and Mermaid rendering are outside the guarantee.
Mermaid rendering waiver: no renderer is installed/invoked by this checker.

Authors update an existing knowledge owner for a semantic delta (or justify no impact),
then update/check navigation and repeat to confirm no-op. Only the marked bundle README
block is generated: four rows from validated title/kind/status and selected paths. It
never writes bodies, status or dates. Invalid inputs/markers prevent writes; no-op
preserves bytes/mtime. Automatic architecture truth or independent acceptance is not
claimed. Existing manual rules and stronger test/release obligations remain.

Development wrappers keep lint/type/test/privacy/docs/package checks and skip only the
historical exact release packet. CI pull requests use this boundary; manual dispatch
retains strict identity. See [runbook](offline-release-verification.md#development-versus-strict-release).

## Candidate identity and publication boundary

Release evidence is point-in-time evidence. The committed MDRack 1.3 base
candidate checkpoint is
`a796a1ab55bbf18ae8c62618502b2b0dda929431`; the packet binds it to the
sorted SHA-256 manifest
`c257c816df7bf3f65fefb709f987fcb5472915bda6f04aa436d9762e62e72868` over
626 tracked paths, excluding only the packet itself to avoid self-reference.
Do not use a branch name, a later dirty checkout, or a packet-only edit as a
substitute for that identity. Any other tracked-byte change requires a fresh
two-build comparison, packet refresh, and independent review before another
commit can be called a release candidate.

The base set is `mdrack==1.3.0`, `mdrack-core==1.0.0rc1`,
`mdrack-media==1.0.0rc1`, and `mdrack-sqlite==1.0.0rc2`. Its local release
matrix builds wheel and sdist artifacts and performs installed-package smoke;
it does not prove an index install. The later external publication sequence is
core, media, SQLite, then application. It needs separate authority for the
hosted Linux/Windows matrix, TestPyPI, PyPI, Git tag, and GitHub Release. No
Git tag, package upload, deployment, Windows run, or live-provider evidence is
implied by the candidate checkpoint; see [1.3 release notes](release-1.3.md).
