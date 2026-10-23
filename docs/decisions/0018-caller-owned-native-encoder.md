# ADR-0018: opt-in caller-owned native encoder process

Date: 2026-10-23. Status: independently accepted bounded Windows source/embedded and
installed product slice. Real-corpus quality/cost/latency fitness and release remain open.

## Decision

Keep default LM Studio text HTTP composition and legacy vector identity unchanged.
Explicit trusted OUTER host/CLI composition may instantiate
`mdrack.hosts.cuda_encoder.CUDAEncoderProvider` and inject the existing
`PreparedEmbeddingProvider` into normal `MDRackEngine`. Only `image native-ingest` /
`image native-search --provider external --host-config CONFIG` expose that CLI choice.
Core, application services and default providers never load model weights/vendor DLLs;
Python host code uses public Win32 process/job APIs, not vendor model calls.

A trusted caller selects configuration, executable/source, exact runtime/weight/CRT
pins, qualified recipe/device/profile and private work directory. Configuration is
never adopted from indexed/resource content. A profile is an identity declaration,
not a qualification certificate or a sandbox for an untrusted executable. No producer
binary, vendor library, model or private config is bundled or registered globally.

The finite supported protocol admits image documents (224×224 RGB PNG) and text queries
for image candidates. Text-document, image-query/similarity and old-space compatibility
are not qualified. Full canonical space binds the producer/profile, input/decoder,
query-prefix, pooling/normalization and float32 codec; equal dimensions are insufficient.
Provider work completes outside application read/write transactions; current source
snapshots, CAS, Core validation/ranking and one SQLite catalog keep their existing owners.

Each encoding is a fresh shell-free selected process in a private temporary directory.
Assign its suspended process to an owned kill-on-close Job before resume; bound input,
output and timeout, verify drift before/after, validate actual evaluated selected-GPU
outputs and raw→L2 float32 bytes, then wait/reap/remove temps on success/failure/cancel.
The qualified producer also prevents child creation. No CPU/old-vector/caption fallback.
Caller-private bounded diagnostics are explicit, default off, and never public logs /
CLI/events/support exports. Their retention is not vector admission.

## Observable product boundary

Normal public ingest/search/read, source CLI and actual installed PE use the same
canonical store and DTOs. To open an original, join public read logical identity and
`whole_image` with PUBLIC search evidence locator/revision, then use the caller's explicit
allowlist and hash/decode checks. Read-unit coordinates remain intentionally redacted;
a locator, filename or DB row alone never grants source access.

Manual same-resource lexical enrichment uses existing prepared-enrichment/CAS with
`lexical=True,dense=False`, retaining the native projection/vector/space/source revision
without encoding. A later semantic query is separately encoded. This does not qualify
a text-document role or request OCR/caption extraction.

## Qualification and limits

The [product result](../evaluation/native-image-product-2026-10-23.md) records fresh
learned CUDA execution across three normal catalogs, full DTO/f32/space parity, public
original opens, close/reopen, installed origins and zero-encode lexical gain. The prior
[external-host canary](../evaluation/native-image-cuda-canary-2026-10-23.md) remains a
separate frozen success-path ABI/GPU qualification, not generic upstream ABI support.
PNG decoding and host normalization remain CPU work; sampled CUDA outputs do not prove
all operators/layers. No independent RU/EN/near-distractor/negative/held-out quality,
model-cost/latency SLO, operational/W6/tag/package release follows from synthetic data.
