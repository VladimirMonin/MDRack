# Offline release verification

This page is the contributor-facing runbook for the v0.4 W5-CI release contract.
The normative contract is [v0.4 W5-CI contract](contracts/v0.4-w5-ci-contract.md);
this page keeps the supported cells, commands, and evidence boundaries visible
without requiring access to live workflow configuration.

## Non-negotiable default

The release path is local and offline. Set `UV_OFFLINE=1` for the workflow and
its subprocesses, install only from the lockfile/cache, and do not contact a
provider, HTTP endpoint, source corpus, external service, or remote runner.
Provider calls, network attempts, online-index fallback, and remote execution are
hard failures. The live evaluator is opt-in and confirmation-guarded; it is not
part of this release path and must not be imported or contacted by omission.

Use the repository root for all commands:

```bash
export UV_OFFLINE=1
uv sync --all-extras --frozen --offline
```

On Windows, use the equivalent environment-variable syntax for the selected
shell. The commands below are the contract commands; a non-zero exit is a
failure, and a partial run must list every skipped command and its reason.

## Supported distribution/install cells

The supported distribution cells are independent package surfaces. Each cell
produces and audits both a wheel and an sdist, for eight artifacts total:

| Cell | Distribution | Contract |
|---|---|---|
| application | `mdrack` | Installs the three local distributions and exposes the CLI and Python API. |
| reusable core | `mdrack-core` | Installs independently and exposes provider/storage-neutral core contracts. |
| media records | `mdrack-media` | Installs with `mdrack-core` and exposes provider-free records and builders. |
| SQLite adapter | `mdrack-sqlite` | Installs with `mdrack-core` and exposes the standalone catalog/search adapter. |

The installed smoke must clear `PYTHONPATH`, run outside the source import path,
verify distribution version and module location, and fail on any import or
command error. Standalone artifacts must not contain the root `mdrack/` package.

## Execution coverage and evidence

The declared execution matrix is Linux (`ubuntu-latest`) and Windows
(`windows-latest`), each with Python 3.11 and 3.12:

| Host | Python | Evidence rule |
|---|---:|---|
| Linux | 3.11 | Mark `passed` only when this exact cell runs and passes. |
| Linux | 3.12 | Mark `passed` only when this exact cell runs and passes. |
| Windows | 3.11 | Mark `passed` only when this exact cell runs and passes. |
| Windows | 3.12 | Mark `passed` only when this exact cell runs and passes. |

The matrix is a coverage declaration, not execution evidence. Every report must
label each cell `passed`, `failed`, `not_run`, or `blocked`; local Linux results
must never be promoted to Windows or Python 3.12 evidence. `fail-fast: false`
is required so selected cells report independently, while any failed cell fails
the workflow.

Evidence must distinguish `unit/offline`, `local components`, `installed
package`, `Windows`, and any separately authorized live boundary. This workflow
claims none of the following: LM Studio/provider behavior, OCR/Whisper/VLM
quality, real-source or real-vault behavior, visual/acoustic quality, or external
runtime behavior.

## Strict gates

Run these gates after frozen offline installation, in order. All are fail-closed:
any non-zero exit, collection error, missing artifact, warning promoted by the
tool, privacy sentinel hit, network attempt, or documentation mismatch fails the
cell. Do not auto-fix a failing gate in CI or weaken its rules.

### 1. Ruff source lint

```bash
uv run ruff check src/ tests/ packages/mdrack-core/src/ packages/mdrack-media/src/ packages/mdrack-sqlite/src/
```

Any Ruff diagnostic fails the cell.

### 2. Mypy for standalone typed packages

```bash
uv run mypy packages/mdrack-core/src/mdrack_core packages/mdrack-sqlite/src/mdrack_sqlite
```

Any mypy error fails the cell. This is not whole-repository typing evidence.

### 3. Unit and integration lane

```bash
uv run pytest -m 'not e2e and not privacy'
```

Collection errors, test failures, xfail misuse, or unexpected deselection fail
the cell. This lane is offline and includes ordinary packaging tests.

### 4. Offline E2E lane

```bash
uv run pytest -m e2e
```

E2E uses only local components, fixtures, and fakes. A provider or network call
is a failure even if assertions pass; E2E must not be described as live or
real-source evidence.

### 5. Privacy lane

```bash
uv run pytest -m privacy
```

The lane fails if supplied query/content/path/root/endpoint/vector,
metadata/facet, exception, or other registered sentinels leak through success,
empty, degradation, failure, or cleanup outputs. Reports must not print raw
failing payloads.

### 6. Dependency, architecture, and compilation boundaries

```bash
uv run python scripts/check_no_forbidden_deps.py
uv run python scripts/check_core_boundaries.py
uv run python scripts/check_sqlite_boundaries.py
uv run python scripts/check_media_boundaries.py
uv run python -m compileall -q scripts src packages/mdrack-core/src packages/mdrack-media/src packages/mdrack-sqlite/src
```

Any forbidden import, reverse package edge, boundary breach, or compilation
error fails the cell. These checks do not authorize architectural changes made
only to make a gate green.

### 7. Offline build and installed smoke

```bash
uv run python scripts/offline_release_matrix.py \
  --output-dir "${TMPDIR:-/tmp}/mdrack-release-artifacts" \
  --candidate-packet docs/evidence/v0.4-release-packet.json \
  --smoke \
  --expected-manifest docs/evidence/w5-offline-release-matrix.json
```

The harness first materializes only the packet's committed base plus its
content-addressed `candidate_snapshot.build_inputs`; publication outputs are not
copied into that candidate. It then builds and audits all four distributions as wheel and sdist,
verify metadata and package isolation, run isolated smoke cells, and record zero
network attempts. A build error, hash mismatch, install error, source-tree
import, missing artifact, or non-zero smoke command fails. The output directory
must remain outside the source checkout; it is disposable evidence and must not
be committed.

### 8. Documentation and whitespace

```bash
test -s docs/evidence/w5-offline-release-matrix.md
test -s docs/evidence/w5-offline-release-matrix.json
uv run python scripts/check_release_docs.py
git diff --check
```

Missing/empty evidence, invalid privacy-safe manifest, missing required
terminology, or whitespace errors fail the cell. Reports must preserve explicit
non-claims for unexecuted matrix cells and stronger evidence boundaries.

## Local baseline versus W5 lanes

For full local repository acceptance, also run the baseline gates from
`AGENTS.md`:

```bash
uv run pytest
uv run ruff check src/ tests/
uv run ruff check packages/mdrack-core/src/ packages/mdrack-sqlite/src/
uv run mypy packages/mdrack-core/src/mdrack_core packages/mdrack-sqlite/src/mdrack_sqlite
uv run python scripts/check_no_forbidden_deps.py
uv run python scripts/check_core_boundaries.py
uv run python scripts/check_sqlite_boundaries.py
git diff --check
```

The W5 lanes above are the reproducible release contract and explicitly separate
E2E and privacy from the ordinary test lane. A green local Linux run is evidence
only for that host and Python version.
