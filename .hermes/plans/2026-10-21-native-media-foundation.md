# Native media / Foundation — active execution entry

Full programme and obligations: [canonical 03](../../docs/proposals/native-media/03-mdrack-implementation-and-migration-plan.md).
Read [bundle](../../docs/proposals/native-media/README.md) in order 01 → 02 → 04 → 03.
This entry tracks execution, not another roadmap or evidence registry.

Current software acceptance and the next bounded execution are recorded in
[the latest status](#current-reviewed-caption-pilot-and-required-remainder). Earlier per-family OPEN /
FAIL statements below retain their original acceptance boundary; they do not reopen
findings explicitly closed by a later qualified result.

## Historical family scopes and dependencies

- W1-A: scoped documentation/tooling/setup independently ACCEPTED, including R3 BOM
  normalization; R1/R2 remain closed. No media/runtime/evaluator/CAS implementation
  is claimed here. Global full/expanded-installed/release gates remain OPEN.
- W1-B1: grade>0 arithmetic source/static slice independently ACCEPTED; no new
  repaired-runtime build/installed or model-quality PASS is inferred.
- W1-B2: complete source evaluator family (schema 2 / semantics 2.0.0) independently
  ACCEPTED; B2-R1 CLOSED. Versioned target/cutoff, resource duplicate rejection,
  temporal correctness, deterministic one-to-one matching, nullable misses and
  separate negative cases are within the accepted source scope. W5 real-corpus,
  public production-runner and model-quality obligations remain OPEN; source
  acceptance does not imply live-pixels, full-suite or installed PASS.
- W1-C: whole-response read-session/Q09 source/local-component family independently
  ACCEPTED on Windows/CPython 3.14.2, including metadata two-lexical-branch and
  non-embedding discovery consumers, consistent verify/open, eager DTOs and owned
  reader cleanup/nonsharing. C01 blank-hybrid validation now precedes provider and
  reader entry. Nine transferred type-boundary repairs plus dev-only PyYAML stubs
  are qualified: strict nine-path app import closure and core/SQLite 30-file gates
  are GREEN, not whole-src/release static acceptance. Fresh current v2; no CAS or
  native-model acceptance. See [current snapshot scope](../../docs/current-architecture/README.md#operation-owned-search-snapshots).
  Historical C01/strict15 FAIL evidence is retained; full/installed/V/W2–W6 and
  real-model quality/release obligations remain open in their existing boundaries.
- W1-D: D26 technical/source slice independently ACCEPTED; four knowledge updates
  completed. Useful core/app/LM Studio serialized bytes, privacy and sink-I/O isolation
  are confirmed on Windows/CPython 3.14.2 source with fake/MockTransport and real
  SQLite/files/CLI; see [O03 and graph-only O06](../../docs/proposals/native-media/04-mdrack-acceptance-quality-and-observability.md#принятый-срез-w1-d-o03-и-graph-only-o06).
  W3 token/repeat oracle, new build/installed logging runtime and full/release/model
  gates remain OPEN; the whole O01–O06/native/enrichment programme is not closed.
- Global gates: full repository and expanded installed remain OPEN (retained FAIL);
  new repaired-runtime build/installed proof remains OPEN. Exact current release
  qualification/publication is NOT_RUN, not waived. Cache10 repair is independently
  ACCEPTED and released in the Windows source/local-component scope. Its effective
  SAFER-medium/Administrators-deny-only proof is not ordinary interactive/UAC-filtered
  or installed/full-app proof. Portable-fixture and nonpromotion remainders remain
  OPEN/NOT_RUN; cache acceptance closes none of the retained full/installed/new-build
  gates.
- V: W3-linked app 1.4.0rc2 versions/exports/contracts/ADR/fixtures/lock INTEGRATED
  and independently ACCEPTED in the source/offline W3 scope with pinned uv0.11.15.
  Core/media 1.0.0rc2 and SQLite distribution 1.0.0rc3 (API 1.0.0rc2) are coherent;
  all31 third-party lock nodes are unchanged. Fresh installed-synthetic Windows/
  CPython3.14.2 qualification covers this slice, not full release/OS/global gates.
  Earlier W2 acceptance and its historical package identities remain retained.
- W2: source/offline provider/planner/source-resolution/native-image family independently
  ACCEPTED on Windows/CPython3.14.2, including actual Root and installed-synthetic
  local-component checks. W2-R1 is CLOSED: original O01 route/stage/error branch
  accounting and O04 two-size serialized event/byte bound are independently verified.
  Typed CLI/engine routes preserve the accepted C-owned read sessions, authorized
  source snapshots and fresh v2 without CAS. Exact cumulative39 technical bytes,
  preimages/readback and the later plan-only factual status overlay are retained
  separately; this is not whole-roadmap or release acceptance. Live pixels transport
  remains separately BLOCKED until a concrete supported LM Studio HTTP contract is
  established. Available embedding-model/textual-route evidence and synthetic
  installed/native orchestration are not native-pixels or real-quality proof.
  No live calls were added. Retained full-suite failures and W3–W6 obligations below
  are not closed by this source/offline acceptance.
- W3: whole assigned M03-03 + coherent V independently ACCEPTED on Windows/
  CPython3.14.2 source/offline, actual Root and fresh installed local/synthetic bytes.
  W3-R1 CLOSED: clean-old-v2 graph/FTS refusal, backup/recheck/target verification
  and exact0004-to0005 preservation are independently confirmed. Neutral publication,
  shared core validation, atomic graph/state/token/CAS/all writers, stale no-write,
  withdrawal/deletion/recreate and token/repeat remain within this accepted scope.
  Useful recovery after real BEGIN, including unconditional delete failure, preserves
  old data/state/token, closes the transaction and permits next publication/deletion.
  Affected strict/import closure and installed public engine/registered launcher
  checks are qualified; inherited expanded CLI strict failures are not full green.
  M04-S02 purpose-aware app merge and full enrichment stay W4; W5/W6/native/model/
  quality/full-release and the earlier global remainders are not closed.
- W4: FIRST_READY_OCR_FAMILY_ACCEPTED on Windows/CPython3.14.2 source/local
  components and non-editable installed packages; W4-R1 CLOSED. Public engine and
  registered CLI publish caller-owned ready OCR into real FTS without model/OCR
  calls, preserving native/independent graphs and vector bytes through repeat,
  correction, bounded CAS, stale-result refusal, reopen and normal image rescan.
  The additive export/import snapshots are synchronized and closed negative controls
  remain strict. The connected W4/M03-06 prepared enrichment/reindex/durable-custody
  component is now also independently ACCEPTED in actual Root/local and fresh
  non-editable installed Windows/CPython3.14.2 boundaries; W4-R2 CLOSED. Ready
  OCR/text/transcript/frame/video projectors, all four intents, dependency/manual
  correction, preserving producers, changed-source invalidation, withdrawal/reentry,
  import/delete/tombstones, bounded real-connection CAS and actual host retention/
  cache deletion/reopen are qualified. Affected11 strict is GREEN; all103 inherited
  whole-static diagnostics remain exact and are not waived. Core publication docs/
  inventory are synchronized without neutral code changes. This prepared component
  does not close native builders, live inference, real corpus/quality-cost, global
  full/expanded-installed or release obligations. Caller custody alone still does
  not claim host durability; the separate archive supplies the qualified retention.
- W5: original M03-07 native temporal/media component independently ACCEPTED on
  Windows/CPython3.14.2 actual Root and fresh noneditable installed packages;
  W5-R1 CLOSED. App1.4.0rc3/media1.0.0rc3 pure ready-vector builders, bounded CPU
  frames/audio/visual-only direct video, async engine/registered CLI, current-role/input
  candidate resolution and effective pre-limit filtering, shared W4 CAS/lifecycle,
  nullable graph, repeat and caller-removal/reopen are qualified. Affected31 strict is
  GREEN; exact103 inherited whole-static diagnostics remain RED, new0/missing0.
  This is source/local and installed-synthetic acceptance, not live inference, whole
  W5/W6 or release acceptance. See [native contract](../../docs/contracts/native-temporal-v1.md).
  Original real M03-04 obligations remain: versioned RU/EN/cross-language corpus,
  held-out source groups and actual model A–D/optional E/paired quality-cost runs.
- M03-04 software: the complete [native input-side corpus/provenance/query adapter
  and common-public runner](../../docs/evaluation/native-corpus-v2.md) is independently
  ACCEPTED on Windows/CPython3.14.2 actual Root/source-local and fresh noneditable
  installed boundaries; M03-04-R1 CLOSED. Frozen inputs/policy/profile/gold,
  pre-limit eligibility, real engine/SQLite/FTS, paired C–D–E enrichment with native
  vector preservation, safe declared port failures and planned/blocked denominators
  are qualified with scripted providers. This is software mechanics, not real-model
  quality. The retained 73-artifact/seven-media acquisition stays draft, with zero
  query grades/speech and reviewed=false/publishable=false. Whole-static103 remains
  inherited FAIL/new0; original M03-04/W5 real-data/model-cost, global/full/expanded-
  installed/live/W6/operational/release obligations remain OPEN.
- W6: all declared release-scope families → exact candidate, installed CLI/engine,
  actual OS/Python cells, migration/reopen, G01/R01–R04/T01–T02 and three separate
  contract/runtime/quality-cost verdicts. Image release includes late enrichment;
  reduced release scope is a separate decision. Scoped repository publication is
  separate from package/tag/release publication; no full-release approval is implied.

## Roles and maintained owners

Hermes coordinates assignments/handoffs. Coder implements and performs author checks;
one different Sol/max Reviewer independently evaluates the frozen candidate. Repairs
return to the same author/reviewer; no additional owner pipeline or self-acceptance.
One integration owner controls shared contracts/versioning; reusable core owns
validation/grouping/RRF, SQL owns persistence/lifecycle, app owns policy/source I/O,
media owns pure builders and evaluation owner owns metric/corpus truth.

Knowledge stays with [DOCS](../../instructions/DOCS.architecture.instructions.md),
[TEST](../../instructions/TEST.quality-gates.instructions.md),
[OBS](../../instructions/OBS.logging.instructions.md),
[build workflow](../../instructions/WORKFLOW.build-orchestration.instructions.md),
[current architecture](../../docs/current-architecture/README.md), package API/ADR/CLI
contracts and [release runbook](../../docs/offline-release-verification.md).
Programme completion is not inferred from completion of W1-A.

## 2026-10-23 bounded successor status (historical pre-final cutoff)

Finite14/all103 strict repair is independently ACCEPTED: actual Root strict0/213,
Ruff0; source and fresh installed affected190 PASS/1 POSIX SKIP overlap, not380 unique
cases;233 exact payloads and7 contract groups. Earlier exact103 RED entries remain
historical observations, not current static debt.

Suite K1–K7 author owning checks:77 PASS, including actual isolated offline four-wheel
consumers and unchanged historical66 input checks. This is not independent acceptance
of that successor or a full-suite/release claim. Historical full raw1/2371 PASS/18 FAIL/
6 SKIP remains unchanged. Current named semantic skill input yielded actual4 PASS/
2 semantic FAIL; these two read-only content contracts remain OPEN, not read refusals
or skips. Final full gates, coherent-candidate independent review and explicit scoped
Git authority are still mandatory. Model/native-pixel/full-corpus quality-cost,
programme/W6/operational/release obligations are not inferred closed.

## Current acceptance and next execution

This delivered repair checkpoint and its original next-order list are retained below.
The [later result qualification](#current-result-qualification) governs the current
remaining work; the completed OCR checks are not reopened by the historical list.

The connected PUBLIC30 repair is independently ACCEPTED, including the final exact
one-fixture taxonomy correction. Host-only artifact reads stay registered but are not
catalog-backed; actual engine/public CLI-callable access without a catalog and the
true catalog sibling are qualified. Fresh final affected selection:28 PASS, no skips /
deselection. Retained pre-correction independent full:2412 selected /2406 PASS /
6 historical SKIP /0 FAIL. Original18, owning77/new17, strict0/213, Ruff/boundaries,
four-wheel233 payloads and actual Windows PE controls retain their stated boundaries.
Current-skill six and the committed-input six are different inputs; the whole user-dirty
skill is excluded from this scoped publication. Earlier full18 and skill2 FAIL observations
are historical, not current blockers. W3/W4 and native/corpus software acceptance remain
closed; full pytest does not establish native-model or programme fitness.

Necessary documentation/instruction and verified-retrospective integration follows this
software PASS. Scoped commit/ordinary branch delivery is separate from tag/package /
release approval; the actual commit and GitHub readback belong to delivery receipts.

The next execution order is finite and does not reopen accepted W3/W4:

1. **Old OCR correctness, next task:** change an existing OCR text under the same
   revision while a dependent vector exists; verify dependency invalidation/replacement
   and actual subsequent search/read state. Separately apply an automatic result over a
   manual edit and verify the declared conflict/precedence behavior and retained text /
   vector state. These are bounded current-boundary checks, not another architecture audit.
2. **One genuine image route:** explicitly supported provider/API/runtime, image without
   OCR → actual image vector → text query → the correct openable image. Then add text
   enrichment to that same material and verify additional lexical retrieval without
   losing the native projection. Production embeddings remain LM Studio HTTP; selecting
   another route requires coherent authorized architecture/code/tests/instruction changes
   first. No caption proxy for pixels, inherited budget or implied private-media permission.
3. **Quality and scale through existing owners:** existing runner with independent
   materials, similar distractors, RU/EN/negative queries and predeclared criteria;
   separately measure catalog-open and search latency over increasing chunk counts.
   Keep original source-group/held-out, quality-cost and observability obligations.

These follow-ons are not gates for this repair. No inference/acquisition allowance is
renewed; real model/media work requires its own concrete authorized inputs/runtime and
budget. W6/operational/release and broader quality remain separate from scoped delivery.

## Current result qualification

This API-refusal/catalog-measurement checkpoint precedes the
[later external-host CUDA qualification](#current-cuda-canary-and-product-remainder).
Its HTTP-specific prerequisite is not a claim that no other native execution is possible.

Source `37cb7d39f03c152c64ee9100560ee2fe19c7be75` includes the separately accepted old-OCR
producer/manual/material consistency fixes. Their 2411-pass/six-historical-skip source
qualification remains retained; this result review changed no product source/tests and
ran no new full suite. W2/W3/W4/W5/corpus/fixture/static acceptance stays within its
existing scope.

Two subsequent result objects are independently accepted, without merging their claims:

- **Negative LM Studio API qualification:** the loaded existing model returned text200 /
  finite768, but LM Studio0.4.26+4 `/v1/embeddings` refused typed PNG input at its string-
  only wrapper guard before embedding. Runtime2.63.0 is selection metadata. Six later
  discriminator cells are NOT_RUN; model Vision and generic prepared-provider capability
  are not disproved. See [input qualification](../../docs/contracts/native-image-v1.md#lm-studio-input-qualification).
- **Catalog-open/search measurements only:** four predeclared synthetic768d tiers through
  normal public stores/engine, separate verified-open/default and already-open injection,
  312 searches and code-recomputed distributions. The
  [sanitized scaling report](../../docs/evaluation/catalog-search-scaling-2026-10-23.md)
  preserves the large default/open costs and method limits. No applicable latency SLO,
  performance-regression verdict, concurrent injection-safety or model-quality PASS follows.

The genuine image-vector → text-query → correct openable-image route remains OPEN.
The next unresolved prerequisite is a concrete supported LM Studio typed image-embedding
input plus compatible text-query vector output through its public API/worker/backend.
Reloading a model, a Vision label, caption/base64-as-text or speculative endpoint retries
are not substitutes. No alternative runtime or blind update is implied; production
embeddings remain LM Studio HTTP and prior operation budgets are not renewed.

After that prerequisite is actually qualified, retain same-material lexical enrichment
and the existing runner's independent materials/similar distractors/RU/EN/negative cases /
predeclared quality-cost criteria. The separate catalog scaling request now has usable
measurements; choosing an applicable workload SLO for a future performance decision is
still distinct. These result acceptances do not reopen accepted software or close native /
semantic fitness, W6/operational/release.

## Current CUDA canary and product remainder

This is the canary checkpoint; [later normal product acceptance](#current-native-product-and-fitness-remainder)
supersedes its unexecuted integration queue without rewriting the canary cutoff.

On the documentation baseline `4623abf4c86c647d3f096703c6d4b31ad2da8484`, the
[external-host CUDA canary](../../docs/evaluation/native-image-cuda-canary-2026-10-23.md)
is independently ACCEPTED within its synthetic scope. Exact frozen vendor ABI/host /
runtime/weight bindings and a fresh seven-cell run produced six learned768 vectors,
32 vision +48 backbone sampled post-evaluation CUDA outputs, unchanged numeric criteria
and both hash/decode-verified original opens. WIC CPU decode and host L2 remain CPU;
this is not all-operator GPU, general ABI, legacy-space or real-quality acceptance.

The HTTP wrapper limitation and production LM Studio HTTP default are unchanged.
The canary did not traverse `PreparedEmbeddingProvider`, normal engine/catalog/public
read or installed CLI. Existing W3/W4/W5/M03–04/OCR/full2411+6 and separate catalog
MEASUREMENTS_ONLY remain retained, not reopened or rerun by this result.

The next actual product slice is bounded host-owned encoder → explicit prepared-provider
injection → normal `MDRackEngine` image ingest/semantic text-query search/public read /
hash-original-open/close-reopen. Preserve exact recipe/space identity, honest supported
roles/byte bounds, source authority, failure/cancellation cleanup and no hidden CPU or
legacy-vector substitution. Do not put the model loader in Core/application/default
providers. The current CLI exposes no external-host provider option; actual explicit
composition and installed-launcher parity must be proved, not inferred from the canary.

Then verify lexical enrichment on that same resource without native re-encoding, and
existing-runner independently judged RU/EN/similar-distractor/negative/held-out quality
and cost/latency criteria. No new server/runtime/model or blind update follows. Completed
live budgets are closed; this result does not itself grant further execution. Whole
native-media/fitness/W6/operational/release remains OPEN.

## Current native product and fitness remainder

This product checkpoint retains its original next-work cutoff. The functional-A
checkpoint below also retains its cutoff; [the latest data/remainder status](#current-reviewed-caption-pilot-and-required-remainder) is current.

The exact source8 on baseline `d5476653a4da80ab4082b47ce47a0e4ffc90c973` is independently
ACCEPTED as a bounded normal image product slice; see [product result](../../docs/evaluation/native-image-product-2026-10-23.md)
and [ADR-0018](../../docs/decisions/0018-caller-owned-native-encoder.md). The explicit trusted
outer host/CLI injects a caller-owned prepared provider backed by a selected bounded process.
Default LM Studio text HTTP/legacy identity stays unchanged; Core/application/default
providers do not load weights/vendor DLLs. This is the approved additive boundary.

Fresh independent proof: three normal catalogs; embedded/source CLI/actual installed PE /
installed SDK reopen;17 learned native calls,184 sampled CUDA outputs, correct public read /
locator-to-allowlisted-original hash/decode opens, exact DTO/f32/full-space parity. SAME
resource manual lexical enrichment encoded0 and retained native projection; later semantic
query counted anew. Current full2428 /2422 PASS /0 FAIL /six historical SKIP includes all
six real-current-skill consumers without edits/waivers/substitution. Four isolated builds /
236 exact installed payloads, strict216/Ruff/boundaries and prior software scopes are
qualified; foreign whole skill/reference/private evidence remain excluded from delivery.

Next concrete artifact: independently judged approved native-corpus-v2 materials with
RU/EN/near distractors/negative/held-out/source groups and rights/allowlist/provenance,
plus frozen experiment/policy/cutoffs and predeclared quality/cost/latency criteria.
Use the existing runner and explicit caller factory; separately qualify textual A.
Only supported native/lexical routes may execute; prepared-text B/dense-text E stay
blocked pending text-document qualification, not caption/CPU fallback or invented labels.
Catalog scaling remains MEASUREMENTS_ONLY, not an applicable SLO. Completed budgets stay
closed without renewal. Whole independent native fitness/W6/operational/release is OPEN.

## Current functional A and matrix remainder

This functional checkpoint preserves its original data-pending cutoff; see the
[later reviewed-pilot status](#current-reviewed-caption-pilot-and-required-remainder).

On baseline `46824c9be98b70521ce585294a3d185f2c315ef3`, the existing short-text
LMStudioProvider/LegacyTextEmbeddingAdapter and actual caller no-argument factories
are independently functionally accepted; see [result](../../docs/evaluation/text-provider-functional-2026-10-24.md).
Fresh reusable-facade proof used 6 real requests, including two direct wire controls
and a real query from a second factory; full profile/space/caps, plain DOCUMENT /
existing QUERY prefix, finite 768 and observed server norms agree. Context 2048 only;
no 8 MiB/long-context or multilingual semantic-quality claim. Text-kernel device remains
UNKNOWN despite selected CUDA/GPU-max/residency. Default identity/native product/full
2422 PASS + six historical SKIP remain unchanged; no native forwards/full replay here.

The remaining source-contract artifact is an independent disposition of the required
B-role/matrix: current RunConfig requires A/B/C/D or A/B/C/D/E, while native B/E
text-document remains unsupported. Null policy/incomplete coverage blocks fitness;
independent gold/policy alone cannot close that mismatch. Do not redesign the matrix,
fabricate roles or weaken assertions under this functional acceptance.

Independent approved RU/EN/near-distractor/negative/held-out/source-group corpus /
judgments/projections and frozen experiment/policy/cutoffs/quality-cost-latency criteria
remain separate inputs. No data adjudication or quality trial was performed here.
Future execution uses the existing runner and explicit caller factories only under its
resolved contract and a fresh concrete scope; outer preflight/hard ledger/close/exact
own unload are mandatory because the script has no provider-close finally. Completed
budgets stay closed, catalog scaling MEASUREMENTS_ONLY, whole fitness/W6/release OPEN.

## Current reviewed caption pilot and required remainder

On baseline `754cb37cf2df00c2bbfb590ed476ae97d1b80cdf`, independent DATA review accepted
[a private source-caption pilot and limited pre-score policy](../../docs/evaluation/native-corpus-review-2026-10-24.md), not model fitness. Actual22 submitted RGB224 views/parents/rights/lineage and shared gold were checked;
11 dev +11 held-out source groups,44 human texts/52 cases. Three proposed grades changed;
534 scoped known rows reuse480 unique text/target decisions, four unknowns stay pooled.
Both own frozen bundles passed current validators/require_evaluable and actual public
source `inspect` (raw0/evaluabletrue/publicationfalse); two full RunConfig parses passed.
Target model/GPU/LM Studio/lifecycle/factory/GET/ranking/scoring/install operations were0.

Strict query-language intent has one dev pair and no held-out pair. Paired assistance
variants are not matched-intent query-language coverage. Negative6/7-member subcatalogs
are locally reviewed gold, not full-corpus absence or semantic refusal fitness; ceiling10
is structural only. A minimal retained-human-caption selection correction is proposed,
not applied or deemed sufficient bilingual coverage. The policy's per-query regression /
zero-gain hypothesis and inclusive timing/cost bounds are experimental, not user SLAs.

Next source artifact remains the separately owned required B-role/matrix disposition;
this DATA review did not read or duplicate it. Approved data cannot add native text
DOCUMENT roles or admit C/D-only. Thereafter use a concrete frozen-input/profile/config
plan and separately authorized finite executor budget, outer preflight/close/exact own
unload. Do not replay unchanged native/full/build/installed/A proof, reopen their closed
ledgers or replace required missing bilingual/negative/text slices with pilot counts.
Functional A/normal native product stay accepted; catalog MEASUREMENTS_ONLY and whole
semantic/cost/latency fitness/SLO/W6/operational/release remain OPEN.
