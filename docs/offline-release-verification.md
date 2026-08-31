# Offline release verification

This page is the contributor-facing runbook for the MDRack 1.3 offline release
contract. The historical [v0.4 W5-CI contract](contracts/v0.4-w5-ci-contract.md)
is retained for audit history and is not a current release gate.

## Non-negotiable default

The release verification commands are local and offline after the environment
has been provisioned. Set `UV_OFFLINE=1` for verification commands and their
subprocesses, and do not contact a provider, HTTP endpoint, source corpus, or
package index inside that boundary. Provider calls, online-index fallback, and
application network access are hard failures. The GitHub workflow is hosted CI:
checkout, tool setup, dependency provisioning from the committed lockfile,
matrix scheduling, and evidence upload are remote infrastructure, not local
offline execution evidence. The live evaluator is opt-in and
confirmation-guarded; it is not part of this release path.

The workflow runs on `workflow_dispatch` and `pull_request`. Its `ubuntu-latest`
and `windows-latest` / Python 3.11 and 3.12 cells execute independently with
`fail-fast: false`. A fresh hosted runner may populate its dependency cache from
the package index during the lock-frozen provisioning step; the workflow then
sets `UV_OFFLINE=1` before every quality, build, smoke, and evidence gate. The
workflow pins `uv==0.11.15`, the version used to
validate the committed `uv.lock`; changing uv requires an explicit lock refresh
and the same offline matrix rather than inheriting the setup action's latest uv.

The hosted artifact smoke may provision the exact constrained runtime versions
from the package index because a fresh runner has no warm wheel cache. The
installed program checks, artifact audit, and evidence validation run after
that provisioning step. Linux replays the full test lanes; Windows runs the
package/release contract and all eight installed artifact cells. The broader
Windows behavioral suite remains outside this release claim.

Use the repository root for all commands:

```bash
uv lock --check
uv sync --all-extras --frozen
export UV_OFFLINE=1
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
The application metadata pins `mdrack-core==1.0.0rc1`,
`mdrack-media==1.0.0rc1`, and `mdrack-sqlite==1.0.0rc2`; the generated
`install_graph` must include all three root edges. This local smoke is not an
index-download installation check.

## Licensing and third-party boundaries

The four Python distributions use the canonical MDRack MIT license. Each exact
wheel and sdist must declare MIT metadata and contain the complete canonical
`LICENSE`; the archive audit is the evidence, not a metadata label alone. See
[licensing and commercial use](licensing.md) for the project-owned policy.

The locked third-party runtime graph is documented in
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md). These dependencies are
resolver-only for the four Python artifacts, including Windows-only `colorama`;
their upstream licenses are not relicensed by MDRack. A self-contained bundle
has a different obligation: before release it needs an exact bundle manifest
and the applicable upstream license/notice texts. This runbook does not treat a
wheel/sdist result as evidence for a PyInstaller EXE.

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

Package-index and publication cells are also separate from this workflow. Until
they run, report TestPyPI upload/hash/index-install, PyPI upload/hash/index-install,
Git tag, and GitHub Release as `not_run` rather than inferring them from an
offline build or an installed local wheel.

## One-store acceptance evidence

The canonical fixed-catalog acceptance runner is separate from the W5 historical
packet. It uses the versioned `one_store_v1` synthetic fixture, preserves fixture
source hashes, keeps only bounded `latest/` evidence, validates a temporary
installed-wheel target, and scans its summary/log/manifest for privacy sentinels.

```bash
evidence_root="$(mktemp -d /tmp/mdrack-one-store-evidence.XXXXXX)"
uv run python scripts/run_one_store_acceptance.py --evidence-root "$evidence_root"
```

The result is local/offline Linux evidence only. It does not contact a provider
or prove real source, Windows, package publication, or live external behavior.
See [one-store acceptance evidence](one-store-acceptance.md) for the exact
artifact layout, lifecycle-event schema, and development-only skip flag.

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
  --smoke
uv run python scripts/check_v13_release_packet.py \
  --artifacts-dir "${TMPDIR:-/tmp}/mdrack-release-artifacts"
```

The harness builds and audits all four distributions as wheel and sdist, verifies
metadata and package isolation, runs isolated smoke cells, and enforces the
offline controls `UV_OFFLINE=1` plus the installed-smoke socket block. It does
not provide process-wide network-attempt telemetry. A build error, hash
mismatch, install error, source-tree
import, missing artifact, or non-zero smoke command fails. The output directory
must remain outside the source checkout; it is disposable evidence and must not
be committed.

Run the packet validator only against the exact clean candidate it describes.
The packet is excluded from its own source manifest, but every other tracked
file is included. A dirty documentation edit, even when `git diff --check`
passes, is not authenticated as the prior candidate and must be followed by a
fresh two-build packet/review cycle before an external release stage.

### 8. Documentation and whitespace

```bash
uv run python scripts/check_v13_release_packet.py
git diff --check
```

Missing/empty evidence, invalid packet metadata, missing required terminology, or
whitespace errors fail the cell. Reports must preserve explicit
non-claims for unexecuted matrix cells and stronger evidence boundaries.

## External package-index verification: not part of this runbook command

After the local candidate, the four hosted matrix cells, and explicit release
authority are all available, use the sequence in the
[1.3 release notes](release-1.3.md#external-publication-sequence-not-run):
TestPyPI first, then PyPI, with `mdrack-core`, `mdrack-media`,
`mdrack-sqlite`, and `mdrack` uploaded in that order. Verify every returned
index hash, then test a normal and `--no-binary mdrack` installation in separate
fresh environments outside the checkout with `PYTHONPATH=`. Do not tag or create
a GitHub Release before the PyPI checks pass; stop on any duplicate, partial
upload, resolver/import failure, or hash mismatch.

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
