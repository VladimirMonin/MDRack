# Native-image CUDA external-host canary — 2026-10-23

**Independently accepted bounded external-host canary, not MDRack integration or
real-corpus quality/latency acceptance.** Repository documentation baseline:
`4623abf4c86c647d3f096703c6d4b31ad2da8484`. No product source/tests changed.

## Actual execution boundary

A frozen standalone native executable used the existing installed LM Studio
llama.cpp CUDA12 AVX2 runtime 2.63.0 and existing EmbeddingGemma2 Q8_0 backbone /
F16 projector. No new runtime/model, server, vendor patch or CPU-vector substitution.
This bypasses the application's HTTP adapter; it does **not** repair the separately
qualified [HTTP input refusal](../contracts/native-image-v1.md#lm-studio-input-qualification).
The normal production default remains LM Studio HTTP, text-only.

The accepting reviewer inspected the exact source/ABI/runner and independently ran
seven fresh processes using the frozen executable, same inputs/criteria and recipe.
Author results were checked separately, not replayed as the fresh vectors. Device:
NVIDIA GeForce RTX 5060 Ti, driver 591.86; Windows x64, MSVC 14.44 release `/MD`.

The pinned vendor model-load/context-init supplement uses real release-MSVC
`std::string&` arguments whose lifetime spans native ownership and cleanup. This is
finite success-path qualification of these exact vendor bytes, **not** certification
of a generic upstream ABI, another compiler/STL or guessed pointer/blob arguments.

Inputs are two synthetic 224×224 RGB PNGs, one lossless re-encoding and two frozen
text queries. PNG signature/header/size are checked before bounded PNG-only Windows
WIC CPU decoding; independent CRC/zlib decoding verifies every native bitmap byte.
No STB/audio/video/FFmpeg fallback, caption or OCR proxy was used. The decoder is
part of this recipe, not a claim that an older decoder path ran.

Native MTMD vision output enters the real backbone batch; queries are tokenized
with their exact retained prefix. Recipe: context/batch/ubatch 2048, four threads,
non-causal attention, mean pooling, selected CUDA device, GPU offload requested,
flash attention and vision warmup disabled. Native pooled 768d raw float32 output
is normalized by the host in double precision, then stored as float32. All six
raw→normalized byte sequences and norms were independently checked (error ≤1e-4).
CPU decoding/normalization and host work are not GPU execution claims.

## What proves CUDA work

Callbacks are attached to the actual vision/context schedulers and active only
around real vision encode / backbone process calls. In the post-evaluation phase,
the callback accepts actual F32 `MUL_MAT` outputs only on the selected GPU buffer's
device, synchronously reads eight values and refuses nonfinite/all-zero samples.
It does not allocate a diagnostic tensor or manufacture a stage-only event.

The fresh run retained **80 sampled completed CUDA operation outputs**: **32 vision**
(four image forwards) and **48 backbone** (six forwards). Loaded CUDA libraries were
hash-checked after evaluation. This is stronger than layer-assignment/VRAM logs,
but proves neither every operator/layer nor an entirely GPU-resident pipeline.

## Fresh seven-cell result

| Frozen case | Observed result |
|---|---|
| First image | New finite nonzero 768d learned vector; vision + backbone CUDA |
| Identical repeat | New forward/vector; L2 distance 0 |
| Lossless same pixels | New forward/vector; L2 distance 0 |
| Changed pixels / second image | New forward/vector; L2 distance 0.616010296123697 |
| First text query | Correct image ranks first; correct-minus-crossed cosine 0.13899522571809486 |
| Second text query | Correct image ranks first; correct-minus-crossed cosine 0.16514866456277566 |
| Invalid PNG | Refused before model load; no vector or partial output |

All **7/7 native exits were 0**, producing six new vectors, not seven. Frozen limits
were unchanged: same-pixel distance ≤max(1e-5,10×repeat), changed-pixel distance
>max(1e-3,100×repeat), each crossed margin ≥0.01. Ranking was in memory over the two
native image vectors; both selected retained originals were actually opened/read,
hash-checked and independently decoded against their native bitmaps. This was not
catalog retrieval, public engine/read, installed CLI or a GUI-viewer test.

Actual owned timeout/cancellation helpers exited nonzero and were waited/reaped;
partial-success/stage-only receipts and byte drift were refused. Native child creation
was disabled; helpers were assigned suspended to owned kill-on-close Jobs before
resume. Four reserved final collections retained individual GPU/RAM/engine, app
inventory/server, saved-PID absence and byte-preservation observations before
aggregation. All owned jobs/helpers ended; app loaded/server state and runtime /
weight/input bytes were preserved. WDDM unknowns are not zero or global-idle proof.
Historical crashes and a missing historical observer exit remain history; current
absence does not fabricate that exit. Per-cell timings include loading/CPU work and
callback instrumentation, not isolated GPU latency or a declared SLO.

## Exact artifact identity

SHA-256 identities bind this result; binaries, models, private source/recipes,
inputs, queries, vectors and raw receipts are **not redistributed** here.

| Artifact | SHA-256 |
|---|---|
| Frozen host source | `6387ec635bd610de1ddf89643f9e0569f4f5898a9587558eaae3f2601cff85b7` |
| Executed host binary | `afa4e1d8d54c7e4326aaaed6ad6a881bbe60e1dab3bdaba0f672f1d3596dd48c` |
| Vendor ABI supplement | `906d46a380835c92076809e36dad4cf0704c5248e296ec8eccd4626429731a33` |
| Runtime backbone library | `8826344fd559aa85820dddc80a7b5a7bd5d5f8e0f845f93cea3ff0acdd0ceaf2` |
| Runtime media library | `adde27eda9830320c41dc079fc66a47692f3c0fb2e3eed9b3a5f4bdaeaf25e27` |
| Runtime CUDA backend | `5780714013b3171950a09a5ed31773a3c4477daa8e34ed4b3f1e9cba080d97cf` |
| Backbone GGUF | `6f1bd4ac6c5df7444f9cca7ca36cafe6cfa34cd6f49fefb1e0b4be8143aed8bc` |
| Projector GGUF | `74bf65c860d292a1eebafd7ca85b44532a2528f03017f9204fe1695916d2c66f` |

Dependency/CRT/device/recipe bindings remain in controlled evidence. Exact weight
bytes are known; weight upstream revision and exact arithmetic of every executed
kernel are not. Text-document role and compatibility with old CPU/HTTP vector
spaces are **not qualified**; equal 768 dimensions do not merge spaces.

## Product remainder at the canary cutoff

The later [normal product result](native-image-product-2026-10-23.md) qualifies the
actual integration/installed/enrichment slice. The list below preserves what this
canary itself had not executed; it is not the current remaining-work router.

The next slice at this cutoff is host-owned encoding through explicit existing
`PreparedEmbeddingProvider` / `MDRackEngine(prepared_embedding_provider=...)`
injection, with honest capabilities and a complete canonical space. Prove normal
catalog image ingest → text-query semantic search → public read → hash/decode of
the correct original → close/reopen. Unsupported roles/inputs and identity drift
must fail closed; no model loader belongs in Core/application/default providers.
The current CLI selects only `lmstudio` or `synthetic`; an actual explicit host
composition and installed-launcher parity remain work, not an available new flag.

Then prove same-resource lexical enrichment without native re-encoding, and use the
existing runner with independently judged RU/EN, similar distractors, negatives,
held-out materials and predeclared quality/cost/latency criteria. The separate
[catalog scaling measurements](catalog-search-scaling-2026-10-23.md) remain
MEASUREMENTS_ONLY. Existing software/OCR/full2411+6 acceptance is retained, not rerun
or promoted by this canary. Whole native-media/fitness/W6/release remains OPEN.
