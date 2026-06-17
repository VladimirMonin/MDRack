## MDRack Model Management Plan 3

Date: 2026-06-17

### Objective

Remove routine manual LM Studio model handling from the normal MDRack workflow.
The user should be able to download, load, unload, inspect, switch, and rebuild
embedding models from the CLI without opening LM Studio for everyday operation.

### Problem Statement

Current MDRack behavior is incomplete for real LM Studio usage:
- the code can request embeddings through `/v1/embeddings`, but it cannot manage
  model lifecycle through LM Studio's native control API;
- the active embedding model is configured statically in TOML;
- switching the configured model is unsafe because incremental `scan` only updates
  changed files, which can leave old and new vectors mixed in one embedding profile;
- diagnostics do not fully explain model drift or profile mismatch after a model
  switch.

This creates a poor user workflow and a correctness risk for semantic search.

### Scope

In scope:
- LM Studio model management through HTTP API
- CLI commands for model listing, download, load, unload, and switch
- safe persistence of the active embedding model in project config
- full embedding rebuild on model switch by default
- diagnostics and status improvements for active model visibility
- end-to-end tests with a virtual store and model-switch scenarios
- live validation against two real LM Studio embedding models:
  - `Qwen/Qwen3-Embedding-4B-GGUF`
  - `Qwen/Qwen3-Embedding-0.6B-GGUF`

Out of scope:
- GUI or tray integration
- background model orchestration daemon
- multi-user or remote LM Studio coordination
- Python-side model execution
- multi-profile model routing as a user-facing feature in this phase

### Product Rules

1. The normal user workflow must not require opening LM Studio to switch the active
   embedding model.
2. The application must use LM Studio HTTP APIs directly rather than shelling out to
   the `lms` command.
3. A model switch must not silently leave mixed embeddings in the active profile.
4. The active project config must not be updated to a new model until the new model is
   validated and the required rebuild succeeds.
5. The default recovery path after model switch is a full embedding rebuild, not a
   partial incremental update.
6. Logs must remain privacy-safe: no raw private paths, secrets, prompts, or raw URLs.

### User-Facing Command Plan

Add a new top-level CLI group:

```text
mdrack model ...
```

Initial command set:
- `mdrack model list`
- `mdrack model loaded`
- `mdrack model download <model>`
- `mdrack model download-status`
- `mdrack model load <model>`
- `mdrack model unload <instance_id>`
- `mdrack model switch <model>`

Planned switch flags:
- `--download` to download the model first if needed
- `--load` to force loading before validation
- `--rebuild embeddings|full|none`
- `--yes` to skip confirmation for destructive rebuild actions

Planned default behavior for `mdrack model switch <model>`:
1. Inspect LM Studio for model availability.
2. Download the model if requested and required.
3. Load the model if requested or needed for validation.
4. Run a small embedding probe against the target model.
5. Detect actual embedding dimension from the probe response.
6. Verify the current store state and active profile metadata.
7. Persist new config only after successful validation.
8. Run a full embedding rebuild by default.
9. Return structured JSON with the old model, new model, dimensions, rebuild mode,
   and final status.

### Technical Design

#### 1. LM Studio Control Client

Extend the existing LM Studio integration beyond `/v1/embeddings`.

Required native LM Studio API support:
- `GET /api/v1/models`
- `POST /api/v1/models/download`
- `GET /api/v1/models/download/status`
- `POST /api/v1/models/load`
- `POST /api/v1/models/unload`

Implementation direction:
- keep the embedding provider focused on embedding requests;
- add a sibling control client or a small management layer for model lifecycle calls;
- reuse the same endpoint normalization and privacy-safe logging principles.

Expected responsibilities:
- list downloaded / visible models;
- inspect model state (`loaded`, `not-loaded`, or equivalent LM Studio state);
- start downloads and poll download state;
- load embedding models into memory;
- unload model instances from memory;
- surface structured, sanitized errors to CLI commands.

#### 2. Provider Factory Centralization

Current provider construction is duplicated across CLI commands.

Plan:
- extract one shared provider factory for embedding commands;
- make `scan`, `search`, `rebuild`, and `eval` read the same resolved active model;
- make model switch reuse the same path so runtime behavior stays consistent.

#### 3. Safe Active Model Persistence

The active embedding model currently lives in TOML config.

Plan:
- add a safe config writer for `.mdrack/config.toml`;
- update `embedding.model`, `embedding.dimensions`, and related fields only after
  successful switch validation;
- write changes atomically to avoid partial config corruption.

Reason for choosing config persistence in this phase:
- it keeps the runtime model source-of-truth in one place;
- the current command stack already loads model settings from config;
- it avoids introducing a second persistent authority for the active model.

#### 4. Safe Rebuild Policy

Default policy on model switch:
- rebuild embeddings for the full active profile;
- do not rely on incremental `scan` after a model switch.

Rationale:
- file-hash-based change detection does not detect embedding model drift;
- partial re-embedding under the same profile can create mixed vectors;
- rebuilding embeddings is sufficient when parsing/chunking logic is unchanged.

Planned rebuild modes:
- `embeddings`: full embedding rebuild for all chunks in the active profile
- `full`: complete rescan + rebuild for cases where parsing or chunking also changed
- `none`: allow only as an explicit escape hatch; must emit a strong warning and mark
  the store as requiring rebuild

#### 5. Diagnostics And Status Hardening

`status` and `doctor` must become model-aware.

Planned additions:
- show active config model and configured dimensions;
- show active profile metadata from `embedding_profiles`;
- warn when config and stored profile metadata disagree;
- warn when a model switch was requested without a successful rebuild;
- make it easier to spot a mixed or stale embedding state.

### Execution Phases

#### Phase 1. Planning Artifact And Control Surface

Goal:
- add this document and lock the command surface before coding.

Deliverables:
- `docs/model-management-plan-3.md`
- separate commit with planning only

#### Phase 2. LM Studio Control Layer

Goal:
- implement HTTP client support for model list, download, download status, load,
  unload, and loaded-model inspection.

Primary files:
- `src/mdrack/embeddings/lmstudio.py` or a new sibling module
- related unit tests

Verification:
- unit tests with mocked HTTP client responses

#### Phase 3. CLI Model Commands

Goal:
- expose model management from MDRack CLI.

Primary files:
- `src/mdrack/cli/__init__.py`
- new `src/mdrack/cli/commands/model.py`
- shared output helpers if needed

Verification:
- CLI tests for JSON output and error paths

#### Phase 4. Config Persistence And Provider Unification

Goal:
- centralize provider creation and add safe config writing.

Primary files:
- `src/mdrack/config/loader.py`
- `src/mdrack/config/models.py`
- `src/mdrack/cli/commands/scan.py`
- `src/mdrack/cli/commands/search.py`
- `src/mdrack/cli/commands/rebuild.py`
- `src/mdrack/cli/commands/eval.py`
- new shared provider/config helper module if needed

Verification:
- unit tests for config write path
- CLI tests proving all embedding-aware commands use the same model settings

#### Phase 5. Safe Model Switch Workflow

Goal:
- implement `mdrack model switch` with validation, config update, and rebuild flow.

Primary files:
- new `src/mdrack/cli/commands/model.py`
- `src/mdrack/cli/commands/rebuild.py`
- `src/mdrack/indexing/indexer.py`
- diagnostics helpers if a rebuild-required marker is introduced

Verification:
- integration tests with a temporary store
- failure tests proving the old config survives failed switch attempts

#### Phase 6. Diagnostics And Status Improvements

Goal:
- make drift and mismatch visible.

Primary files:
- `src/mdrack/diagnostics/doctor.py`
- `src/mdrack/diagnostics/integrity.py`
- `src/mdrack/cli/__init__.py`
- related tests

Verification:
- seeded database tests for mismatch cases

#### Phase 7. End-To-End Coverage

Goal:
- add real workflow coverage around init, scan, switch, rebuild, and search.

Test requirements:
- virtual project root and virtual knowledge store
- indexed markdown fixture corpus
- model switch scenario from `0.6B` to `4B`
- switch back from `4B` to `0.6B`
- explicit verification that embeddings were fully rebuilt
- explicit verification that search still works after each switch

Planned test layers:
- unit tests for control API behavior
- CLI tests for command contracts
- integration tests for database/profile behavior
- e2e tests for end-user workflow in a temporary store

#### Phase 8. Live Validation Against Real Models

Goal:
- validate the implementation against the user's actual LM Studio environment.

Live experiment matrix:
- list models and inspect state
- load `Qwen/Qwen3-Embedding-0.6B-GGUF`
- run embedding probe and record actual dimension
- init temporary store, scan fixtures, run semantic search
- switch to `Qwen/Qwen3-Embedding-4B-GGUF`
- rebuild full embeddings, rerun semantic search
- switch back to `Qwen/Qwen3-Embedding-0.6B-GGUF`
- unload one or both models and verify CLI responses

Data to capture in report:
- whether each model was already downloaded
- whether explicit load was required
- actual dimension reported by each model
- time and behavior of rebuild operations
- any LM Studio state quirks or API inconsistencies

### Test Plan

#### Unit Tests

Add coverage for:
- list models success and failure
- download request success and failure
- download status parsing
- load request success and failure
- unload request success and failure
- dimension probe for target model
- config write and rollback behavior

#### CLI Tests

Add coverage for:
- `model list`
- `model loaded`
- `model download`
- `model download-status`
- `model load`
- `model unload`
- `model switch`
- JSON success and error envelopes
- refusal or warning behavior for unsafe no-rebuild cases

#### Integration Tests

Add coverage for:
- embedding profile metadata updates on switch
- full rebuild replacing all vectors in active profile
- failure before config persistence
- status and doctor mismatch reporting

#### End-To-End Tests

Add a temporary-root workflow that does all of the following:
1. `init`
2. `scan`
3. semantic search with model A
4. `model switch` to model B
5. semantic search with model B
6. `model switch` back to model A
7. semantic search again

The e2e suite must use a virtual store and simulated model control responses by
default, while still mirroring the real model identifiers:
- `Qwen/Qwen3-Embedding-4B-GGUF`
- `Qwen/Qwen3-Embedding-0.6B-GGUF`

### Risks

1. LM Studio control API response shape may differ slightly by version.
2. Real model dimensions may not match assumptions and must be detected rather than
   trusted blindly.
3. Switching config before rebuild success can leave the project in a broken state.
4. Allowing `--rebuild none` without a visible warning can create silent retrieval
   corruption.
5. Some LM Studio versions may expose loaded instances differently from visible model
   listings.

### Mitigations

- keep response parsing defensive and covered by tests;
- probe the target model before persistence;
- persist config only after validation and successful rebuild;
- emit strong diagnostics for mismatch states;
- run live validation on both target models before closing the work.

### Done Criteria

This plan is complete when all of the following are true:
- users can manage LM Studio embedding models from `mdrack model` commands;
- users can switch the active embedding model without opening LM Studio;
- model switch does not silently mix old and new vectors in one active profile;
- `status` and `doctor` expose active model and mismatch information clearly;
- e2e tests cover virtual-store model-switch flows using both target model names;
- live experiments succeed against `Qwen/Qwen3-Embedding-4B-GGUF` and
  `Qwen/Qwen3-Embedding-0.6B-GGUF` or clearly document any blocking LM Studio issue.
