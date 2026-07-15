## MDRack Remediation Plan

> **Status: Superseded.** Historical remediation record; do not use as the
> current v0.2 execution plan. See
> [`mdrack-v0.2-retrieval-modernization-plan.md`](mdrack-v0.2-retrieval-modernization-plan.md).

Date: 2026-06-17

### Objective

Close the gaps found during real CLI validation so MDRack is usable with LM Studio by default, consistent across `--root` and config-driven workflows, honest in retrieval eval reporting, and covered by stronger end-to-end checks.

### Scope

In scope:
- LM Studio default integration and observability
- Root/store/config path consistency across CLI commands
- Retrieval eval correctness and `doctor` CLI wiring
- Regression tests and real-workflow verification

Out of scope:
- New product features beyond the current MVP
- Schema redesign beyond what is required for the fixes
- New external services or dependencies

### Workstreams

#### 1. LM Studio Usable By Default

Goal: make configured LM Studio flows work without manual endpoint workarounds and make request lifecycle visible in logs.

Planned changes:
- Normalize LM Studio endpoint handling so both base URLs and `/v1` URLs work.
- Align default config values for endpoint, model, and dimensions with actual provider behavior.
- Allow `scan` to use the configured embedding provider, including `lmstudio`.
- Prevent silent semantic failure in hybrid search and eval paths.
- Add safe request lifecycle logging for LM Studio calls.
- Persist correct embedding profile metadata.

Primary files:
- `src/mdrack/embeddings/lmstudio.py`
- `src/mdrack/config/models.py`
- `src/mdrack/cli/commands/scan.py`
- `src/mdrack/cli/commands/search.py`
- `src/mdrack/cli/commands/rebuild.py`
- `src/mdrack/search/semantic.py`
- `src/mdrack/search/hybrid.py`
- related tests

Verification:
- unit tests for endpoint normalization and error handling
- CLI search/rebuild flows against LM Studio-compatible configuration

#### 2. Root / Store / Config Consistency

Goal: every CLI command must resolve the same project root, config file, store directory, and database path.

Planned changes:
- Resolve default config path from the selected `--root`.
- Resolve relative `paths.store` against the selected root.
- Stop reloading config inside subcommands that already receive ctx config.
- Reuse one shared helper for store/database resolution.
- Fix indexer store resolution so `scan` writes where the rest of the CLI reads.

Primary files:
- `src/mdrack/cli/__init__.py`
- `src/mdrack/cli/commands/files.py`
- `src/mdrack/cli/commands/read.py`
- `src/mdrack/cli/commands/sections.py`
- `src/mdrack/cli/commands/search.py`
- `src/mdrack/cli/commands/rebuild.py`
- `src/mdrack/cli/commands/eval.py`
- `src/mdrack/indexing/indexer.py`
- `src/mdrack/config/loader.py`
- related tests

Verification:
- CLI tests from an external working directory using `--root`
- config-file override tests
- real sandbox smoke test with `scan`, `status`, `files`, `read`, `search`

#### 3. Eval Correctness And Doctor CLI

Goal: eval metrics must not report false success, and `doctor` must expose the already-implemented diagnostics engine.

Planned changes:
- Validate eval query `expected` clauses more strictly.
- Treat zero matched gold targets as invalid or failed, not perfect.
- Propagate semantic/hybrid search failures into eval results.
- Align runtime behavior with query metric schema or simplify the schema.
- Replace the `doctor` stub with the real diagnostics command.

Primary files:
- `src/mdrack/eval/queries.py`
- `src/mdrack/eval/metrics.py`
- `src/mdrack/eval/retrieval.py`
- `src/mdrack/cli/commands/eval.py`
- `src/mdrack/cli/__init__.py`
- `src/mdrack/diagnostics/doctor.py`
- related tests

Verification:
- unit tests for invalid/empty expected targets
- eval CLI tests with failure cases
- doctor CLI tests with seeded inconsistencies

#### 4. Quality Gates And Real Workflow Coverage

Goal: move confidence from green unit tests to trustworthy CLI and retrieval behavior.

Planned changes:
- Add CLI end-to-end tests for `init`, `scan`, `status`, `files`, `read`, `search`, `rebuild`, `eval`, `doctor`.
- Add regression tests for LM Studio config defaults and hybrid failure reporting.
- Extend real-workflow checks to mixed fixture corpora and root/config edge cases.
- Update docs where implementation contracts changed.

Primary files:
- `tests/cli/*`
- `tests/e2e/*`
- `tests/unit/*`
- `docs/cli-contracts.md`

Verification:
- `uv run pytest --tb=short -q`
- `uv run ruff check src/ tests/`
- `uv run python scripts/check_no_forbidden_deps.py`
- manual CLI smoke tests on fixture sandboxes

### Execution Order

1. Fix root/store/config consistency first.
2. Fix LM Studio default integration and logging.
3. Fix eval semantics and wire `doctor`.
4. Add regression coverage and update contracts/docs.
5. Re-run full verification and real CLI smoke tests.

### Done Criteria

The remediation is complete when:
- `mdrack scan`, `search`, `rebuild`, and `eval` behave consistently under `--root`.
- LM Studio works with default-style configuration and emits visible safe logs.
- eval no longer reports perfect metrics for empty gold sets.
- `mdrack doctor` returns real diagnostics output.
- full test, lint, and forbidden dependency checks pass.
