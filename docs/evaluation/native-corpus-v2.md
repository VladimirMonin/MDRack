# Native corpus adapter and public experiment runner (input-side v2)

This is a **new input contract**, not an extension of the closed quality-input/report
v2 grammar or a v1 corpus migration. Existing evaluator arithmetic, target matching,
report versions, licenses and public DTOs are unchanged. Python validation is
normative; the new JSON Schemas provide structural checks only.

## Honest evidence boundary

`mdrack.eval.native_retained.import_retained_pack(root, manifest_ref)` reads a
caller-pinned retained acquisition manifest and verifies its local artifacts. It
never acquires a URL or restores pruned transport. For the retained corpus-02 pack:

* four XM3600 images: **media BY-2.0**, separate EN/RU annotation **BY-4.0**;
* two selected **ESC-10 BY-3.0** clips, confirmed against retained original rows;
  this is not a license claim for the whole ESC-50 collection;
* one Durian authored-CGI video: **BY-3.0**, not natural-camera footage;
* three FLEURS records are **text leads only**, not speech samples;
* captions, acoustic class IDs, source descriptions and video duration do not
  become relevance grades, ASR, temporal-event truth or cross-language judgments.

The adapter returns **draft/acquisition**, zero query cases/grades, no fixed
projection/enrichment, `local_reviewed=false`, `publishable=false`. Actual credits,
source/attribution chains and original annotations stay in the external retained
pack, with a digest-pinned attribution-ledger reference. These references are not
publication clearance. Public access does not assert reviewed/no-PII status.
This pack cannot run a quality experiment. Real quality remains open.

## Input documents

Schemas: [native corpus](../../tests/evaluation/schemas/native-corpus-v2.schema.json)
and [experiment](../../tests/evaluation/schemas/native-experiment-v2.schema.json).

A bundle has `contract=mdrack.native-corpus`, `schema_version=2`, state
`draft|frozen`, evidence `acquisition|synthetic|real_reviewed`, and resources,
source-group nodes and queries. Optional `provenance_ref`/`selection_ref` pin the acquisition manifest
and selection recipe; the retained importer supplies both. Resource IDs and source revisions are explicit;
artifacts and attribution ledgers are relative, bounded, byte-counted and SHA-256
pinned. No absolute/parent-traversal path, symlink escape, unknown license or
publication without local review is accepted. New-license support is limited to
this new adapter; the v1 license contract is not widened.

`local_reviewed` is an explicit caller declaration that local rights/privacy and
attribution conditions were reviewed; it is never inferred from a downloadable
file. `publishable` is separate. A local experiment does not require permission
to redistribute the media. Do not publish an index or prepared artifact merely
because a payload-free quality report is safe to share.

Group nodes identify source parents, descriptions, translations, crops, clips,
annotations and generated query material. Edges must preserve group and partition;
all occurrences of one group stay in one partition; cycles and known duplicate
media content crossing groups are rejected. Resource nodes bind their actual
media SHA. The caller must declare semantic relationships that bytes alone cannot
discover. Grouping is not inferred from translations or caption similarity.

Queries declare split, slice, source group, typed input, **explicit nonempty
eligible resource IDs**, target, identity/temporal matching, cutoffs, complete or
pooled judgments, temporal policy and adjudication/origin. Text queries cannot be
copies of the fixed candidate text. Native cross-source queries exclude their own
source group. Optional `task=instance|localization` explicitly distinguishes
same-parent instance/localization from cross-source similarity; localization
requires unit/temporal targets. Frozen real-reviewed queries also need an `intent`
and a digest-matching `source_node` in their declared group. The retained importer
creates none of these judgments. Human/adjudication declarations are trusted input,
not automatically authenticated by software.

Fixed candidate artifacts (`projection` and `enrichment`) carry kind, exact text
hash and origin. `kind=asr` contains an existing normalized `TranscriptArtifact`
JSON document, **not a class name or untimed string**. The existing transcript
materializer owns passage grouping and coordinates; the adapter never invents a
whole-duration event. Plain descriptions and captions remain text assistance.
Agent drafts are not ready projections or adjudicated gold.

## A–D and optional E

Use `mdrack.eval.native_runner.run_native_experiment` (or the async variant), with
explicit already-prepared providers A and B. There is no default model, SDK,
network client or implicit model construction.

* **A/B**: identical frozen text/normalized timed-passage projections, grouping,
  queries, eligible resources, K and budgets, with their supplied text spaces.
  Typed document encoding and complete public-manifest import are real operations.
* **C**: native image/audio/video ingestion into the public engine and real SQLite.
  `video_kind=video|frames` is declared, not a silent fallback. Documents use their
  fixed text projection. The existing native policy and its numeric budgets/hash
  are sealed in the plan before indexing.
* **D**: on **the same C store**, publish the fixed OCR/ASR/caption/description
  lexical artifact through public prepared enrichment, then query again.
* **E** (optional): encode the actual D materialized text units with B, republish
  dense prepared enrichment on that store and query again.

All searches call `MDRackEngine.search_media/search_native_media`, not an adapter
ranker. Resource-specific source namespaces constrain each branch **before**
candidate truncation. Native audio/video queries use the public API's vector
weight 1; non-default native-query vector weights are explicitly blocked, not
silently ignored. There is no lexical branch without text. The adapter does not accept arbitrary unit-ID scopes and
then pretend post-limit filtering is a scoped search. Resource/revision scope sent
to quality-v2 is the original closed projection of the selected snapshot.

A/B, B/C, C/D and D/E comparisons use corresponding query ordinals and actual
results at the query's largest K. Reports retain per-K nDCG/Recall/MRR, coverage,
negative returned/empty counts, temporal errors and nullable misses from the
unchanged evaluator. Paired deltas include quality and negative false positives.
Unsupported routes, failed preparation or incomplete judgments retain **blocked
cells**, never zero-quality substitutes or quietly removed denominators.
At the Python runner boundary, declared `EmbeddingError` failures use the safe
`embedding_port_failure` reason and independent scenarios continue. This is not a
catch-all: direct programming failures and cancellation propagate, subject to the
existing public API's provider-error normalization.

## Current real-provider qualification and matrix boundary

The [short-text A functional result](text-provider-functional-2026-10-24.md) qualifies
short DOCUMENT/QUERY inputs through the existing LM Studio producer/legacy adapter,
including the real script's no-argument factory path. This is not real-corpus quality,
long-context support or installed support for a caller-private factory module. Native
image product qualification is separate and does not add native text-document roles.

Current `RunConfig.parse` admits only A/B/C/D or A/B/C/D/E, not a C/D-only run.
The supplied B provider is shared by prepared-text B and native C/D/E: `_projection`
requires DOCUMENT TextInput, and optional dense E also needs that role. The qualified
image/text-query native provider honestly refuses it before encoding. Null policy
returns `BLOCKED_POLICY_MISSING`; any incomplete stage returns `BLOCKED_COVERAGE`.
Independent gold/policy cannot alone close this required B-role/matrix constraint.
Its source-contract disposition remains open; do not fabricate capabilities, drop
blocked denominators or treat functional A as whole-matrix fitness acceptance.

## Caller policy, costs and privacy

Configuration freezes scenario matrix, split, candidate budget, lexical/vector
weights, A/B mode, video route and optional caller fitness policy before encoding.
The policy names critical slices, maximum quality regression, minimum paired
nDCG gain, search time, index bytes, provider-call and negative-return bounds, and
minimum independent source groups. Missing policy blocks a fitness conclusion,
not collection of mechanics observations. Absent critical slices, insufficient
groups or incomplete scenarios also block it. These are **caller bounds**, not
invented universal acceptance thresholds or statistical/model acceptance.

Inputs are independently snapshotted; callbacks cannot rewrite sealed policy or
gold. Plan and report bind corpus/query/gold/groups, configuration, prepared
spaces/capabilities, native policy and adapter/evaluator module bytes. The
`adapter_evaluator_ref` covers the runner, corpus adapter, quality_v2 and quality_io
modules; it is **not** a claim to hash every SDK dependency. Complete source/wheel
and actual imported-module closure must be retained with each qualified run.

Counts and timing are observed: executed/planned cells, indexed resource/rep/unit/
vector counts, live index bytes including SQLite sidecars, preparation/search time,
and attempted provider calls/input bytes/time. Provider counters are cumulative
per supplied instance (B is shared across B/C/D/E), not additive per-stage bills.
Currency price is unknown/null. Independent groups, not repeated query count, are
the uncertainty unit; no confidence interval is fabricated. Synthetic results
remain `SYNTHETIC_MECHANICS_ONLY`; `semantic_quality_claim` is always false.

Clean reports contain ordinals/digests/counts, not captions, transcripts, credit
names, profile URLs or raw local paths. The sealed plan hashes labels and prepared
space metadata. Indexes and intermediate prepared artifacts are **input-side local
work products** and may contain text/space metadata: do not publish them as clean
reports. CLI errors are payload-free and preserve an existing output on refusal.

## Offline CLI

Run with an environment containing the four ordinary project packages and its
locked runtime dependencies, from a checkout or using a transported identical
script and non-editable wheels:

```sh
python scripts/run_native_evaluation.py prepare-retained \
  --pack-root EXTERNAL_PACK --manifest-ref sha256:PINNED_MANIFEST_SHA --output draft.json
python scripts/run_native_evaluation.py inspect --bundle draft.json --media-root EXTERNAL_PACK
python scripts/run_native_evaluation.py run --bundle reviewed.json --config experiment.json \
  --media-root EXTERNAL_MEDIA --output-root NEW_OWNED_WORK --output report.json \
  --provider-a host_providers:PreparedA --provider-b host_providers:PreparedB
```

Factories are explicit host-supplied no-argument callables; draft readiness and
configuration are checked **before** factory import. The host controls factory
side effects; this runner never authorizes network/model acquisition. The outer host
must own preflight, an exact intended loaded-instance lease and a fresh authorized hard
request budget; runner Meter counters are measurements, not that guard. It must also
close providers and unload only its owned instance in `finally`: the current script
has no provider-close finally. Do not reuse a closed proof ledger or use an unavailable
instance identifier as an artifact-key/JIT/fallback selection. Exit 0 means
successful prepare/inspect or bounds-satisfied mechanics, 2 means a written report
with blocked/exceeded fitness, and 1 means refusal/error. None means independent
technical ACCEPT or real-model quality PASS.

The separate synthetic tests generate their own PNG/PCM bytes and fully declared
grades, exercise real FTS/vector/SQLite and public ingestion/search/enrichment,
and include negative, partition, privacy, policy and temporal controls. Scripted
providers test software mechanics, not semantic or speech capability.
