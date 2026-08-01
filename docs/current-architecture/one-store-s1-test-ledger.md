# One-store S1 test-surface ledger

This is the current S1 classification for the inherited dirty test deletion
set. It does not mark an absent test as passing and does not claim that later
public-surface work is complete.

## S1 proof currently retained

`tests/e2e/test_one_store_contract.py` is the S1 topology oracle. It covers:

- fresh CLI `init` and `MDRackEngine.scan` create only
  `<store>/catalog.sqlite3`, and a subsequent `MDRackEngine.search_text` reads
  from that same catalog;
- a missing-catalog engine read fails without creating a database;
- a fresh Python process reopens that same catalog;
- a failed first catalog creation leaves no SQLite file/sidecars and preserves
  an unrelated root `.hermes` marker;
- fresh `integrity_check` and `foreign_key_check` pass through the package
  verifier;
- `files` and `read` use only the fixed catalog after a normal scan, including
  core-derived chunk neighbors;
- clean initialisation preserves source bytes and the JSON-init privacy envelope;
- normal registered CLI commands publish neither candidate lifecycle nor a
  `--catalog` bypass; and
- a production-source scan rejects active-generation imports, an unregistered
  explicit-catalog CLI module, legacy-store literals outside the rejection guard,
  and candidate-rebuild composition.

This is unit/offline and local SQLite/filesystem evidence only. It does not
prove installed packages, a provider, a real corpus, Windows, or raw-media
similarity.

## Deliberately removed: contradictory lifecycle tests

These files asserted the removed active-pointer/candidate contract and cannot
remain as passing product tests:

| Removed paths | Reason | S1 replacement / later owner |
| --- | --- | --- |
| `tests/cli/test_cli_storage.py`, `tests/cli/test_cli_storage_analyzer.py` | candidate rebuild, verification, activation, or pointer analysis | S1 source/topology oracle; any retained diagnostic behavior is S2 |
| `tests/integration/test_generation_runtime.py`, `tests/unit/test_store_generations.py` | runtime generations and active pointer are prohibited | S1 source/topology oracle |
| `tests/integration/test_fresh_compact_reindex.py`, `tests/integration/test_fresh_compact_explicit_sources.py` | inactive candidate creation and activation are prohibited | first normal scan is S1; explicit-source surface is S2 |

## Removed but not accepted as S1 proof

The following inherited files were removed with the old composition, but their
non-lifecycle behavior has **not** been accepted merely because the old tests
are gone. Their one-store replacement belongs to the listed stage:

| Removed paths | Outstanding behavior | Owning stage |
| --- | --- | --- |
| `tests/cli/test_cli_doctor.py`, `tests/cli/test_cli_status.py` | current status/doctor envelopes and catalog diagnostics | S2 |
| `tests/cli/test_cli_images.py`, `tests/cli/test_cli_metadata.py`, `tests/cli/test_cli_resources.py`, `tests/e2e/test_metadata_workflow.py` | retained CLI/engine parity for image, metadata, and resource operations | S2 |
| `tests/e2e/test_v12_unified_public_workflow.py`, `tests/integration/test_s6_core_app_integration.py` | unified public workflow and compatibility projections | S2; textual-space semantics in S3 |
| `tests/e2e/v1_1/test_offline_application_stack.py`, `tests/evaluation/v1_1/offline_runner.py`, `tests/evaluation/v1_1/test_offline_runner.py` | offline application/evaluation runner | S2, then S4 evidence |
| `tests/privacy/v1_1/test_q1_runtime_privacy.py` | broad runtime privacy matrix beyond init output | S4 |
| `tests/cli/test_cli_eval.py`, `tests/cli/test_cli_files.py`, `tests/cli/test_cli_read.py`, `tests/cli/test_cli_sections.py` | direct `knowledge.db` setup or withdrawn normal commands | S2 port/replacement |
| `tests/unit/test_s8_failure_matrix.py` | retired command/diagnostic error branches | S2/S4 replacement matrix |
| `tests/e2e/test_one_store_contract.py::test_selected_resource_similarity_requires_all_16_textual_cells` | cross-kind shared-space parity | S3 |

No additional test file is deleted by the S1 residual repair. Existing retained
tests continue to run as evidence; failures from old direct-DB helpers or
unported public surfaces must be reported as failures, not converted to green
by deletion. The old `sections` and `eval` CLI implementations are deliberately
removed from production rather than left registered against a different schema.
