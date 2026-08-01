# MDRack single-clean-v2: one startup, one catalog

- Plan version: `one-store-v1`, created 2026-07-31
- Stage: S0 — contract freeze, canonical fixture, RED acceptance
- Status: accepted owner decision; no production rewrite is performed by this stage.
- Scope owner: `t_c29f7bdc` owns this document, `tests/fixtures/one_store_v1/`, and focused acceptance tests only.

## 1. Product decision and boundaries

A normal MDRack application startup has exactly one persistent SQLite database:

```text
<store>/catalog.sqlite3
```

The first writable application operation (`init`, `scan`, or `MDRackEngine.scan`) creates or opens that path under one writer lock. It verifies the fresh `mdrack_sqlite_catalog_v2` identity before use. Every read and write opens that same path. A read-only operation with no catalog fails safely and creates no database.

Normal application behavior must not create, read, select, retain, or advertise:

- `knowledge.db`;
- `active-generation.json`;
- `generations/`, candidate IDs, activation, rollback, retention, or predecessor adoption;
- a normal `--catalog` bypass for search, resource, image, transcript, video, model, benchmark, or evaluation commands;
- a split legacy reader separate from the resource catalog.

Old user-data migration, backfill, raw visual/pixel embeddings, waveform/acoustic embeddings, decoded-frame/motion embeddings, provider calls, packaging, real corpus runs, Git staging/commit/push, and database deletion are out of scope for S0.

The target remains feasible with the present `mdrack-core` and `mdrack-sqlite` packages. No replacement database, schema migration, or core rewrite is approved.

## 2. Source anchors frozen by this plan

The RED contract is based on the current source, not historical prose:

- `src/mdrack/cli/__init__.py:163-210` publishes `<store>/knowledge.db` and `init` creates legacy migrations.
- `src/mdrack/application/compatibility.py` resolves application storage through active-generation metadata and falls back to the legacy path.
- `src/mdrack/application/generation_manager.py` and `src/mdrack/adapters/sqlite/generation_runtime.py` own candidate/activation verification.
- `src/mdrack/cli/commands/storage.py:42-70` exposes candidate rebuild semantics.
- `src/mdrack/cli/commands/{resource,transcript,video}.py` retain explicit-catalog paths; `images.py` relies on active-generation composition.
- `src/mdrack/application/resources.py` resolves selected-resource similarity through one exact whole-resource textual space. Provider-neutral media batches must be re-keyed at the application boundary to that same app-owned identity; video frame units remain separate textual evidence.
- `packages/mdrack-sqlite/src/mdrack_sqlite/catalog.py` already supplies the fresh v2 catalog create/open substrate to retain.

## 3. Required public-surface ledger

The following ledger defines behavior rather than preserving legacy implementation details.

| Status | Surface |
| --- | --- |
| Keep and route to canonical catalog | `scan` add/update/delete; `status`; `doctor`; text/semantic/hybrid `search`; metadata/facets; `resources` duplicate/similarity/search; `find-similar`; image ingest/search/delete; FTS rebuild; storage analysis. |
| Port to canonical catalog | `init`; first scan; `MDRackEngine.scan`; files list/info; read file/chunk/neighbors; outline/heading replacement for raw section IDs; singular resource import/export/inspect/delete; retrieval evaluation; default benchmark; model switch/rebuild; transcript/video ingestion; CLI/engine parity for each retained outcome. |
| Remove or move out of normal application flow | candidate rebuild/verify/activate; rollback/retention/adoption; ordinary app `--catalog`; raw legacy section-ID contract; legacy fallback and any retained-predecessor runtime. |

A separate standalone package expert tool may retain a direct catalog path only if it is explicitly outside MDRack’s normal application contract. That boundary is not an S0 implementation decision.

## 4. One textual embedding-space contract

All text-derived resources use one app-owned immutable `EmbeddingSpaceRecord`, constructed from the complete active `EmbeddingProfile`:

```text
space_id = embedding_space_id(profile.name, profile.fingerprint, vector_value_policy)
```

The profile fingerprint includes the query instruction actually passed to the provider plus serialization/template version. Markdown and image paths construct this record directly. Provider-neutral audio/video batches normalize their transport fingerprint and are re-keyed to the same app-owned record before indexing; this changes neither public resource IDs nor source locators.

Each selectable resource owns exactly one textual whole-resource projection:

- document: aggregate retrieval-text chunks;
- image: aggregate committed caption and OCR text;
- audio: aggregate timed transcript text;
- video: aggregate transcript text and all available frame-caption text.

Frame units remain independently searchable but are not independently selectable resources. The acceptance matrix contains all 16 source/target cells for `document`, `image`, `audio`, and `video_with_frame_text`; each cell requires a non-degraded textual result in the one canonical space. This is a text-derived capability only; it does not claim raw-media similarity.

## 5. Canonical fixture: `tests/fixtures/one_store_v1/`

All fixture content is synthetic, CC0, and reviewed as containing no PII. It contains only reproducible source and prepared derived text; routine tests must never call OCR, STT, image decoding, frame extraction, a network service, or an LM Studio provider.

| Path | Contract |
| --- | --- |
| `manifest.json` | fixture schema/version/provenance, payload SHA-256 index, expected one-file topology, public ledger, 16-cell matrix, duplicate and privacy policy. The manifest deliberately does not hash itself. |
| `markdown/note.md`, `markdown/post-init.md` | deterministic Markdown sources. |
| `images/image.png`, `images/image-copy.png` | identical deterministic image bytes for duplicate detection. |
| `prepared/image-caption-ocr.json` | committed caption/OCR values to pass to direct image ingestion. |
| `transcripts/audio.whisper.json` | timed prepared audio transcript. |
| `video/video-resource.json` | valid prepared video manifest with timed transcript and two frame-caption observations. |
| `queries.json` | public search scopes/modes, duplicate expectation, provider-degradation expectation, and the required 4×4 textual-similarity matrix. |
| `privacy-sentinels.json` | values that must not occur in JSON stdout, stderr, diagnostics, or logs. |

The fixture verifier hashes every payload declared by `manifest.json`, validates the duplicate image bytes, validates matrix uniqueness/completeness, and validates that no source/derived filename lies outside the declared manifest. Test scenarios copy fixture sources into their own `tmp_path` root and compare source hashes before and after each operation.

## 6. S0 RED acceptance nodes

Focused tests live in `tests/e2e/test_one_store_contract.py`. The expected current result is a contract failure, never a fixture/setup failure.

| Test node | Required GREEN behavior | Expected current RED reason |
| --- | --- | --- |
| `test_fixture_manifest_is_complete_and_byte_frozen` | fixture is internally reproducible | PASS now; fixture precondition, not a product claim. |
| `test_first_init_creates_only_canonical_catalog` | first `init` leaves exactly `.mdrack/catalog.sqlite3` and no forbidden topology artifact | legacy `init` creates `.mdrack/knowledge.db`. |
| `test_public_surface_ledger_has_no_normal_candidate_or_catalog_bypass` | retained commands exist while candidate lifecycle and normal explicit catalog paths are absent | `storage rebuild-fresh` / `storage activate` and direct catalog options remain exposed. |
| `test_fresh_process_reopens_the_same_catalog` | a second Python process sees the first process’s same catalog and no extra SQLite topology | current bootstrap never creates `catalog.sqlite3`. |
| `test_selected_resource_similarity_requires_all_16_textual_cells` | matrix is 16/16, each source/target pair is non-degraded and uses the one textual space | prior media fingerprint/space IDs partitioned the matrix; app-boundary re-keying is now the focused repair under review. |
| `test_source_bytes_remain_unchanged_through_prepared_media_ingests` | copied Markdown/media fixture input hashes are unchanged after prepared image, transcript, and video ingestion without a provider | focused retained invariant. |
| `test_json_outputs_logs_diagnostics_and_evidence_hide_privacy_sentinels` | JSON stdout/stderr, logs, safe diagnostics, and evidence serialization contain no root/path/content/provider/vector/metadata sentinel | focused retained privacy contract. |

The matrix node is intentionally a bounded RED contract: it must use the shared fixture inputs and a deterministic fake provider, never a live provider. Slice B replaces its temporary current-architecture bootstrap helper with the normal one-store startup; Slice D supplies the 16/16 behavior. No test is marked `xfail`, because the RED output is evidence required before product mutations.

## 7. Existing dirty repair: hunk catalogue

The shared checkout is already dirty. S0 neither stages nor commits it, and no existing hunk is accepted wholesale. Later implementation must rebase each retained idea onto the one-file contract and retain only independently reviewed behavior.

| Existing path / hunk family | S0 classification | Rationale and next owner |
| --- | --- | --- |
| `docs/cli-contracts.md` | supersede | documents `legacy_v0_2` and pointer fallback; Slice E rewrites current contract after implementation. |
| `docs/current-architecture/system-overview.md` | supersede | calls a retained legacy generation preservation-only; topology contradicts one normal catalog. |
| `docs/evidence/v0.4-release-packet.json` | leave untouched / supersede later | stale release digest must not be regenerated in S0. |
| `docs/recovery.md` | supersede | first adoption preserves `knowledge.db` and candidate metadata, prohibited by target contract. |
| `src/mdrack/adapters/sqlite/generation_runtime.py` | supersede from application runtime | current changes are candidate verifier semantics; possible catalog verification primitives need independent extraction only. |
| `src/mdrack/application/compatibility.py` | split: candidate ideas only | direct core document projection and core retrieval pieces may inform Slice C; resolver/fallback/generation wiring must be replaced. |
| `src/mdrack/application/generation_manager.py` | supersede | first-adoption and active-generation logic remains forbidden. |
| `src/mdrack/cli/__init__.py` | supersede | status/doctor v2 data ideas may be reused, but context and init still name `knowledge.db`. |
| `src/mdrack/diagnostics/doctor.py`, `src/mdrack/diagnostics/integrity.py` | candidate for keep after rebase | v2 integrity/count helpers are useful only after they open fixed `catalog.sqlite3` without generation metadata. |
| `tests/cli/test_cli_doctor.py`, `test_cli_status.py` | supersede as current-contract tests | assert active pointer/candidate behavior; retain only nonlegacy result-shape/privacy assertions after rewrite. |
| `tests/cli/test_cli_storage.py` | supersede | tests first adoption and installed candidate activation, opposite topology. |
| `tests/integration/test_generation_active_reopen.py` | supersede | validates candidate and pointer behavior, opposite topology. |
| `tests/e2e/test_v13_recovery_workflow.py` | supersede/rewrite later | demonstrates legacy-init → candidate activation; useful synthetic media construction only. |
| `tests/cli/test_cli_images.py`, `test_cli_metadata.py`, `test_cli_resource_manifest.py`, `test_cli_resources.py`, `tests/e2e/test_transcript_workflow.py` | keep unrelated wheel-install repair only after independent review | `--no-deps` / `.pth` adjustments do not establish one-store behavior; preserve as separate packaging concern. |
| `tests/unit/test_s8_privacy_contracts.py` | candidate for keep | `contract_kind` assertion is tied to old topology, but generic privacy checks remain useful after contract update. |
| `.hermes/**` | untouched | unrelated private execution metadata. |

## 8. Dependency-ordered implementation and review

1. **S0 (this card):** freeze contract, fixture, byte manifest, RED topology/public/space/privacy tests.
2. **S1 / Slice B:** implement a fixed-path `CanonicalStore` factory; first init/scan/engine bootstrap; read-only absence; cleanup after interrupted first creation. Review topology and failure atomicity.
3. **S2 / Slice C:** port every ledger surface, remove legacy/candidate runtime composition and normal catalog bypasses; replace raw sections with a v2 outline contract. Review public CLI/engine parity.
4. **S3 / Slice D:** implement the canonical app-owned text space, effective query instruction, and whole-video transcript-plus-frame textual projection. Make the 16/16 matrix green. Review embedding identity and ranking semantics.
5. **S4 / Slice E:** correlated privacy-safe observability, installed-package runner, current docs, and evidence packet. Run final independent acceptance.

Only one writer may edit the shared checkout at a time. A material review rejection receives one bounded repair and a fresh review. A full source-tree test run, wheel build/install, live LM Studio call, real-corpus run, commit, and push remain distinct later gates.

## 9. Evidence boundaries and completion requirements

S0 evidence is `unit/offline`: real fixture bytes plus local Click/import behavior and a deterministic fake-provider contract only. It cannot prove installed artifacts, LM Studio, a real corpus, Windows, or raw-media capability.

S0 completes only after:

1. manifest SHA-256 verification and fixture parser validation pass;
2. the focused test module has the documented precondition passes and documented RED failures on the current revision;
3. the nearest existing fixture/helper tests pass unchanged;
4. a privacy scan confirms fixture sentinels are not copied into S0 output/logging assertions;
5. `git diff --check` passes and the diff is limited to the S0 plan, fixture, and focused acceptance code.
