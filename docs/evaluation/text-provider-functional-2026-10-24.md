# Short-text A producer and caller-factory qualification — 2026-10-24

**Independently accepted functional boundary only.** This is not corpus quality,
multilingual retrieval fitness, latency SLO, full-matrix or release acceptance.
Source baseline: `46824c9be98b70521ce585294a3d185f2c315ef3`. The previously accepted
[native image product](native-image-product-2026-10-23.md) and software gates remain
unchanged; neither the full suite nor native CUDA forwards were replayed here.
This report retains that HTTP-A cutoff. The later
[opt-in native text/image result](native-text-image-product-2026-10-24.md) separately
closes the named native B text-DOCUMENT functional blocker with fresh installed/CUDA
proof; it does not expand this A result, identify its HTTP kernel device or run a
real-corpus A–D/optional-E experiment.

## Producer, roles and identity

The existing `LMStudioProvider` and `LegacyTextEmbeddingAdapter` were exercised with
the already-installed EmbeddingGemma2 / gemma-embedding2 Q8_0 model, selected CUDA12
AVX2 runtime 2.63.0 and an explicitly loaded caller-owned instance with actual
context **2048**. The pinned backbone/projector pair totals **1,290,750,752 bytes**.
No model/runtime installation, new server, default-provider or legacy-space change.

DOCUMENT sends the plain text in `input: [text]`; QUERY uses the existing exact
`Represent the query for retrieval: ` prefix. No adapter normalization, truncation
or dimensions/MRL request was added. Approximately unit norms are **observed server
outputs**, not adapter L2 normalization. The declared local **8 MiB** input bound
is not a qualified server/token/long-context limit: only short artificial controls
were tested. No semantic relevance labels or corpus judgments were created.

The full profile, canonical space and typed capabilities were checked, not merely
768 dimensions. The textual profile fingerprint is
`1343b07782cf5f35475d0c794e80846ad11ea5bf4566c0f55d0cb6f17b54ed89`.
It differs from the accepted native image space; equal dimensions do not permit
mixed-space vector reuse or qualify native text-document/image-query/similarity roles.

## Actual factory and request proof

The original functional evidence contains eight real requests and was independently
checked. A separate fresh reproduction used a byte-identical reusable caller facade
with its own instance selection and the same complete profile/space/capabilities:

| Fresh boundary | Actual HTTP requests | Observation |
|---|---:|---|
| First real no-argument factory | 3 | Short EN DOCUMENT/QUERY and RU DOCUMENT through the existing producer/adapter |
| Direct exact-wire controls | 2 | Plain DOCUMENT and the existing prefixed QUERY |
| Second real no-argument factory | 1 | A fresh provider actually encoded the repeated QUERY |
| **Total** | **6** | Finite nonzero **768**-component vectors; no caller cache or ready-vector replay |

Both constructions used the actual `scripts/run_native_evaluation.py::_factory`,
not a replacement factory dispatcher. All three document-wire, query-prefix-wire
and fresh-factory query L2 differences were **0**; observed norms ranged from
**1.0000000148492747** to **1.0000000247259895**. These are numerical/plumbing
controls, not multilingual semantic-quality evidence. Raw HTTP200 envelopes were
retained for the two direct controls; supported-adapter calls expose decoded vectors,
so unavailable raw envelopes/statuses are not invented. Internal server caching is unknown.

The earlier reusable-facade check was model-free composition equality, not a prior
live encoding run. The fresh reproduction establishes the reusable path separately.
Caller-private factory modules/configuration are **not bundled or installed product
support**, a new public factory API, or implicit model acquisition. Native factory
construction/role checks added **zero native forwards** and no B/E capability.

## Device evidence and lifecycle

The explicit GPU-max request, selected installed CUDA runtime and between-phase
VRAM/resource observations are retained. Per-instance device placement is null;
actual HTTP text-forward/kernel execution device remains **UNKNOWN**. Neither CPU
execution nor evaluated CUDA kernels are inferred. The independent native image
post-evaluation proof does not transfer to HTTP A.

Fresh usage was **6/8 requests, 14/20 controls, source GET0, local metadata GET1,
conflict wait0**. Failed pre-model observer setup and spent controls are retained;
the first-model-dispatch 30-minute deadline and hard limits were not reset. Four
final collections passed, the exact owned instance was unloaded, HTTP providers and
helpers were closed/waited/reaped, and initial unrelated loaded inventory/server /
selected-runtime state and pinned source/artifact bytes were preserved. No eviction,
forced app close, CPU alternative, download or global setting change was used.

## Existing runner boundary and remaining work

Actual public script help, malformed `inspect`/`run` refusal and configuration parsing
were checked; no real-corpus experiment/report was run. Factories are explicit
no-argument caller composition. The outer caller must preflight/load the exact owned
instance, enforce a new authorized hard ledger, close providers and unload that
instance in `finally`: the current script does **not** provide a provider-close finally.
An old closed proof ledger must never be reused or reset for another trial.

`RunConfig` requires A/B/C/D or A/B/C/D/E; C/D-only configuration is refused.
The B provider serves both prepared-text B and native C/D/optional E. At this HTTP-A
review cutoff, the selected native v1 text-document capability remained unsupported. Null policy returns
`BLOCKED_POLICY_MISSING`; incomplete stages return `BLOCKED_COVERAGE`. Supplying
independent gold/policy alone therefore does not make this composition a complete
fitness matrix. The required B-role source disposition was still open at that cutoff;
no role fabrication, matrix redesign or assertion weakening was accepted here. The later
explicit v2 qualification closes that functional role/composition blocker, while
matrix/policy/coverage and corpus-fitness obligations remain unchanged.

Next evidence must use independently frozen approved corpus/projections/queries /
judgments/groups and experiment/policy/cutoffs/quality-cost-latency criteria through
the [existing runner contract](native-corpus-v2.md). No data adjudication or threshold
selection follows from this functional result. [Catalog scaling](catalog-search-scaling-2026-10-23.md)
remains **MEASUREMENTS_ONLY**; real semantic/cost/latency fitness, W6/operational and
release obligations remain **OPEN**.
