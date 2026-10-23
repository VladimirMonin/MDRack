# Catalog-open and public-search scaling — 2026-10-23

**Independently checked measurements only. No applicable declared latency SLO.**
Source revision: `37cb7d39f03c152c64ee9100560ee2fe19c7be75`. This report records
observations, not a performance repair, portable SLA or release acceptance.

## Workload and environment

- Windows 11 build 26200, x64 CPython 3.14.2, uv 0.11.15, SQLite 3.50.4;
  Ryzen 7 5700X, 16 logical processors, 51,462,053,888 visible RAM bytes.
  Before execution: 22,189,146,112 free RAM bytes. One worker at a time;
  no affinity change, GPU/model work or OS/page/device-cache flush.
- Four independent normal application v2 stores: 256 / 1,024 / 4,096 / 8,192 units,
  64 units/resource, one representation/resource and one dense 768d vector/unit.
  Codec `ieee754-f32-le-v1`, cosine, `BuiltinExactVectorBackend`; no ANN extension.
- Inputs are deterministic, normalized **synthetic non-model vectors** and prepared
  text chunks. All 8,192 largest-pool vectors were finite, nonzero and distinct;
  maximum L2 norm error after float32 canonicalization was below 1e-6. Increasing
  prefixes of the same immutable pool supply the tiers. Across stores there are
  212 resources/representations and 13,568 unit/vector/FTS-row copies, not 13,568
  independent source vectors or relevance judgments.
- Actual creation uses `open_application_catalog(create=True)`; public
  `MDRackEngine.import_resource_manifest()` uses the normal manifest codec,
  Core validation and SQLite publication. Every resource export matched its input.
  This is not Markdown parsing/chunking or ingestion-throughput evidence.
- Actual `MDRackEngine.search_media()` uses target `unit`, limit10, candidate
  budget100 and text/text_chunk scope. The workloads are selective lexical,
  shared-token broad lexical and stored-vector similarity. The last uses
  `StoredUnitInput` with an explicit reuse capability. Encoding raises if called;
  observed provider calls were **0**, not a subtracted estimate of model cost.

This is source-import library evidence, not an installed launcher, model latency,
semantic relevance, image-vector retrieval or concurrent-write benchmark. Fixtures,
stores, raw observations and helpers remain external; no source content, prompts,
vectors or private paths are part of this publication.

## The two read boundaries

**Already-open:** a fully verified readonly application catalog is supplied through
existing `CoreCompatibilityStorage(connection, catalog=...)` and explicit engine
injection. Each measured search opens **0** new SQLite connections. This controlled
composition is not a concurrency/snapshot-safety qualification or an unconditional
production recommendation.

**Normal default:** the uninjected engine uses `ApplicationSearchReadSessions.open()`
per operation. Every measured default search opens **1** new connection, even when
its engine object is reused. `open_application_catalog_readonly()` reaches
`SQLiteCatalog._open()` and `_verify_snapshot()`: schema/identity, integrity/FKs,
full graph/Core validation, exact FTS correspondence and backend verification precede
return. Engine construction is lazy and is not catalog open.

Keep the [operation-owned snapshot contract](../current-architecture/README.md#operation-owned-search-snapshots).
Nothing here authorizes removing verification or changing read-session policy.
Verification preflight and the later operation snapshot remain separate boundaries.

## Verified open and search distributions

All values below are **median / p95 milliseconds**. Fresh-process open has n=3;
already-open warm search n=15 per workload/tier; default repeated similarity n=3.
Percentiles use linear interpolation at `(n-1)*q`. These small descriptive series do
not establish a production tail bound.

| Units = vectors | Resources = reps | Closed DB bytes | Verified fresh open | Already-open selective lexical | Already-open broad lexical | Already-open stored-vector | Default repeated stored-vector |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 256 | 4 | 1,581,056 | 166.647 / 175.635 | 0.826 / 1.070 | 8.739 / 9.853 | 60.823 / 66.436 | 234.024 / 275.483 |
| 1,024 | 16 | 5,713,920 | 655.767 / 771.521 | 0.861 / 1.261 | 12.576 / 15.681 | 222.829 / 260.640 | 921.372 / 1116.891 |
| 4,096 | 64 | 21,909,504 | 2592.754 / 3091.437 | 0.896 / 1.264 | 17.643 / 28.331 | 855.681 / 1146.890 | 3449.304 / 3996.312 |
| 8,192 | 128 | 43,372,544 | 6748.172 / 6759.701 | 0.997 / 1.494 | 28.489 / 47.790 | 1977.530 / 2250.606 | 7098.785 / 8150.638 |

Separate setup observations (empty creation n=1; same-process reopen n=3):

| Units | Empty creation ms | Total public import ms | Same-process verified reopen median / p95 ms |
|---:|---:|---:|---:|
| 256 | 26.363 | 767.793 | 165.339 / 171.659 |
| 1,024 | 42.364 | 2618.420 | 654.474 / 772.742 |
| 4,096 | 35.160 | 10666.560 | 2588.753 / 3216.910 |
| 8,192 | 22.942 | 25154.236 | 5493.477 / 6691.795 |

First-per-kind and default repeated medians, n=3 each:

| Units | First selective / broad / vector ms | Default repeated selective / broad / vector ms |
|---:|---:|---:|
| 256 | 1.180 / 8.580 / 60.583 | 176.559 / 181.853 / 234.024 |
| 1,024 | 1.200 / 11.540 / 223.500 | 670.080 / 691.254 / 921.372 |
| 4,096 | 1.315 / 18.288 / 835.552 | 2599.174 / 2611.094 / 3449.304 |
| 8,192 | 1.657 / 35.893 / 1739.943 | 5547.531 / 5423.631 / 7098.785 |

At the largest tier, verified fresh-open median CPU time was **6703.125 ms** versus
6748.172 ms wall. Substantial CPU work is observed within this boundary; individual
validation stages were not separately profiled. Default-engine construction had a
0.205 ms median, while its repeated selective query took 5547.531 ms. Neither a cheap
constructor nor the approximately 1 ms already-open selective result represents the
normal per-operation path.

A 32× unit increase corresponded to fresh-open median growth 40.494×, warm vector
32.513×, broad lexical 3.260× and selective lexical 1.206×. These are observational
ratios, not a complexity proof, causal decomposition or historical regression.
Do not subtract unpaired medians to assign the cost of an internal stage.

## Method, checks and limits

The plan was frozen before execution. Three fresh read processes per tier each
measure verified open, first queries in fixed selective/broad/vector order, five
warm rounds, same-process close/reopen and a separate default-engine first/repeat
series. Later query kinds can benefit from earlier work. No result is OS-cold.

Wall and CPU timers cover the awaited public method. DTO projection/JSON serialization
is timed separately; query construction, imports/process startup, result hashing,
assertions, receipt writes and memory sampling are outside method timers. Default
WARNING logging and audit instrumentation remain present; this is not a persistent
logging-I/O benchmark. Small Windows CPU samples can quantize to zero, not zero cost.

All 21 measurement phases exited 0 without retries or missing tiers, in 462.095 s.
There were 312 successful public searches, including qualification: expected available
results were undegraded, repeatable across process/reopen and publicly readable.
104 total SQLite connections include creation/open/closure work; per-search deltas
are the separate 0/1 observations above. All four final stores were verified and
closed, with exactly one main catalog and no remaining WAL/SHM.

Maximum sampled worker working-set peak was 362,586,112 bytes; largest sampled single-
store state including sidecars 48,102,144 bytes. Maximum sampled fixture/store/receipt
bytes 218,644,753. These are sampled observations, not continuous process-tree bounds.
Cleanup-only memory was not sampled and remains unknown, not measured zero.

Independent read-only review recomputed all 184 reported distributions and ratios,
counts/percentiles/rendered tables, fixture shapes and identities; it did not rerun
searches or reopen the benchmark databases. Retained setup/preflight errors were
observer/provisioning issues, not product RED or additional measured cases.

No applicable operation-specific latency SLO was declared. Consequently this usable
measurement packet is **MEASUREMENTS_ONLY**, not performance acceptance. Real model
encoding cost, native-image admission/quality, held-out/RU/EN/negative retrieval quality,
concurrent-write behavior, other platforms and release qualification remain distinct.
The [acceptance criteria](../proposals/native-media/04-mdrack-acceptance-quality-and-observability.md)
require an explicit workload/profile budget before a later performance decision.
