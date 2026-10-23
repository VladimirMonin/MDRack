# Normal native-image product qualification — 2026-10-23

**Independently accepted bounded synthetic product slice**, not real-corpus fitness,
latency SLO or release acceptance. Source baseline: `d5476653a4da80ab4082b47ce47a0e4ffc90c973`.
See [ADR-0018](../decisions/0018-caller-owned-native-encoder.md) and the
[native image contract](../contracts/native-image-v1.md).

## Implemented and actually exercised

`CUDAEncoderProvider` is an opt-in trusted outer composition of a pinned bounded
native process. No Python/vendor model loader, new server/model download, global
registration or default HTTP/legacy-space change. Actual
`image native-ingest` / `image native-search --provider external --host-config CONFIG`
use the same normal `MDRackEngine`, canonical SQLite, Core ranker and public read.
The [caller recipe](../../examples/native_cuda/README.md) describes explicit private
configuration; no executable, model, vendor DLL, profile or private input is bundled.

Fresh independent verification used the existing installed LM Studio CUDA12 AVX2
2.63.0 runtime and qualified EmbeddingGemma2 Q8_0/F16 pair on Windows x64 / RTX 5060 Ti,
driver 591.86, CPython 3.14.2 and pinned offline uv 0.11.15. Exact native producer bytes
and decoder/recipe were retained from the separately accepted canary. Source/runtime /
weight/config identity was checked; no old/CPU/ready-vector replay or replacement ranker.
Only image-document and text-query-for-image roles were admitted. PNG WIC decoding and
host L2 normalization are CPU work; no all-operator GPU claim.

| Fresh boundary | Native image forwards | Native text-query forwards | Observed product result |
|---|---:|---:|---|
| Embedded engine | 2 | 5 | Normal ingest/query/public read, close/fresh reopen, same-resource lexical enrichment, later semantic query |
| Source CLI | 2 | 2 | Normal canonical catalog, correct IDs, one-envelope/public read/default refusal/help |
| Actual installed PE | 2 | 2 | Installed-only module origins, same commands/DTOs/store semantics; normal startup, not Python `-S` |
| Installed SDK fresh reopen | 0 | 2 | Actual installed libraries reopen that catalog and query/read/open originals |
| **Total** | **6** | **11** | **17 fresh native processes; all native exits 0** |

The run retained **184 sampled post-evaluation CUDA outputs: 48 vision /136 backbone**.
Each image forward had both stages; each query had backbone work. Observations came
from actual evaluated selected-device F32 MUL_MAT outputs with synchronous finite
nonzero readback, not allocation/VRAM labels. All **17 raw→double-L2→float32 byte
sequences** were independently verified from passive private completed-output capture.
Capture did not alter provider code, inputs, vectors or admission; native temporary
files were removed after owned Job/process reap.

## Normal data and public results

Three independent normal clean-v2 catalogs each contain two native image resources /
vectors and one complete product space. Source/installed stored f32 vectors and full
space metadata are exact; complete semantic DTOs equal embedded results. Per-catalog
publication lifecycle/generation tokens remain independent, not falsely merged.
Correct-minus-crossed query cosine margins were **0.13899522571809486** and
**0.16514866456277566**, above the unchanged 0.01 canary bound. These easy synthetic
queries are not independent semantic-quality judgments.

Public search evidence supplies portable locator/revision. Public unit read verifies
logical identity, native null text and intentionally redacted `whole_image`; the caller
joins these and opens only its explicitly allowlisted original, checking hash and
independent PNG RGB decode. No read redaction or source authority was weakened.

On the SAME first resource, honest manual prepared text with lexical enabled and dense
disabled added a real FTS hit. Resource/source revision, native representations/units,
vectors and full space remained intact. **Enrichment plus lexical search encoded 0**;
the later semantic query encoded separately. Embedded final counts were 2 resources,
3 representations/units, 2 vectors, 1 space, 1 FTS row; other catalogs retained native-only
2/2/2/2/1/0. No caption/OCR extraction or text-document dense capability is implied.

## Software, installation and cleanup gates

- Actual complete original suite: **2428 collected, 2422 passed, 0 failed, six exact
  historical skips**, no deselection/xfail. The six original current-real-skill consumers
  passed under their narrow read-only grant; foreign bytes/mtime stayed unchanged and
  were excluded from delivery. Earlier protected-read RED remains historical, not waived.
- Whole strict **0/216**, Ruff and dependency/Core/media/SQLite boundary checks passed.
  Four ordinary isolated offline PEP517 builds and own installs bound **236 exact
  source/wheel/site payloads**. Nine actual installed PE commands had nonempty installed
  module-origin receipts. Source/SDK `-I/-B/-S` is distinct from normal PE startup.
- Original safety tests exercised unsupported/default, drift/partial, actual timeout /
  cancel and Job-before-resume ownership. Individual final GPU/RAM/engines/app/server,
  saved native/helper absence and source/runtime/input preservation completed before
  aggregation. App loaded/server state stayed unchanged; all owned contexts/jobs /
  stores ended, no eviction/settings/vendor patch. Unknown WDDM values are not zero.
- Observer setup failures and failed pre-model CLI root validation are retained.
  Conservative usage **25/96 units,18/32 controls** includes the failed dispatch
  reservation; completed embedded forwards were retained, not replayed. First-dispatch
  deadline never moved. This is not a latency benchmark or renewed execution allowance.

## Identity and remaining fitness work

Product space fingerprint:
`7df166009e9c9fd592111bf7b4a8bd2d517a9a76b25ac85b642dbf37c8a2c505`.
Encoder profile fingerprint:
`f4c30c99ca645862708233c8259fe18f305bea913bd5f989e5ed143a1500029a`.
Provider source SHA-256:
`2dfaa96bee5bb6b337e39c0909c69e5cbd9bff2cedf68ffa345bbc2d7201e45d`.
Native executable SHA-256:
`afa4e1d8d54c7e4326aaaed6ad6a881bbe60e1dab3bdaba0f672f1d3596dd48c`.
Private evidence retains full pins/receipts, not published bytes/paths/UUIDs/queries/vectors.

Next artifact is an approved, independently judged native-corpus-v2 successor with RU/EN,
near distractors, negatives, held-out/source groups, source/rights/allowlist provenance,
plus a frozen experiment/policy with cutoffs and quality/cost/latency criteria. Use the
existing runner and explicit no-argument factories, including a separately qualified
textual A. Admit only supported native/lexical routes; prepared-text B and dense-text E
remain honestly blocked until text-document role qualification. Do not invent labels,
a missing model, SLO or synthetic-to-real promotion. Existing
[catalog scaling](catalog-search-scaling-2026-10-23.md) remains MEASUREMENTS_ONLY;
whole real-corpus/fitness/W6/operational/release obligations remain OPEN.
