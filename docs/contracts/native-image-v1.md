# Native image API v1 (app 1.4.0rc1)

This is the W2 declaration freeze before consumer wiring, not runtime acceptance.
The old text and image methods/envelopes remain unchanged. Core, media and SQLite
contracts and distributions do not change. No schema migration is needed.

Public exports from `mdrack.public_api`:
- `EncodingRole`: document, query, similarity.
- `TextInput(text, identity)` and `ImageInput(content, media_type, identity)`:
  immutable prepared payload, never a filesystem locator. Payload repr is hidden.
- `InputCapability(role, input_modality, candidate_modalities, max_input_bytes)`;
  `RouteCapabilities(inputs, reuse_document_vector=False)`.
- `EmbeddingResult(vector, space)`: `space` is the complete canonical core record.
- `PreparedEmbeddingProvider`: capabilities, space, async encode(input, role).
- `LegacyTextEmbeddingAdapter`: text-only, query uses embed_query, document uses
  embed; similarity is not implied by equal dimensions. LM Studio selected text
  HTTP transport is text-only. No image transport is fabricated.
- `MediaSearchRequest(query, scope=SearchScope(), target='resource', mode='hybrid',
  allow_partial=False, limit=20, candidate_budget=100, lexical_weight=1.0,
  vector_weight=1.0, evidence_limit=3)`; query is TextInput, ImageInput or
  `StoredUnitInput(unit_id)`. Scope is independent of query modality.
- `MediaSearchResult(target, requested_mode, effective_mode, results,
  degradations, space_id=None, profile_fingerprint=None, basis_unit_id=None)`.
  Space/profile identify an executed vector branch; basis is explicit for reuse.
  Closed serialized keys additionally include schema
  `mdrack.media-search.v1`, score_kind='rrf', total_count and degraded.
  Result items: resource_id, unit_id, rank, score, evidence. Evidence: resource_id,
  representation_id, unit_id, representation_kind, modality, text (nullable),
  locator, source_revision. No synthetic text, extraction or hidden fallback.
- Engine: `ingest_native_image(path, resource_id, source_namespace, source_ref,
  expected_revision=None, derived_text=(), derived_lexical=True, derived_dense=False)`
  and `search_media(request)` use one app use case. `derived_text` is caller-prepared
  `ExtractedImageText`, never permission to run OCR. `00` leaves it with the caller;
  `10` publishes FTS, `01` nullable text-modality dense units, `11` both. These
  switches do not affect ordinary note embeddings. No durable read-only artifact
  guarantee or merge with old representations is implied (W4).
  `prepared_embedding_provider=` is explicit injection; default wraps the selected
  text provider, refusing images before file/DB/provider side effects.
- CLI registered group `image native-ingest` and `image native-search` select
  `lmstudio` by default; unsupported images fail closed. An explicit `synthetic`
  provider is an offline test tool, never automatic production fallback. Explicit
  `--provider external --host-config CONFIG` instead composes a trusted caller-owned
  bounded encoder process, as specified below; no other/default route changes.

One vector and one lexical family bound weights regardless of representation
count. Candidate budget is per applicable family, not final K. Image hybrid has
no lexical branch and is vector-only without degradation. Planned branch failure
obeys strict/partial; no executable branches is an error, empty matches is success.
Space equality includes metadata; equal dimensions/fingerprint alone are not enough.
Stored-vector reuse requires an explicit similarity capability and uses one owned
read session. Providers run before owned read transactions; hydration is eager.

Source authority: selected Path, caller-prepared immutable bytes, or explicit host
mapping (namespace, portable source_ref). A DB locator, metadata path, URL or
Markdown link never grants permission. `SourceResolver` uses RawInputBudget,
signatures and snapshots and checks revision/range. Host retains durable mapping;
temporary snapshot paths are not persisted as reopen authority. Fresh reopen read
and search do not need original file access. Enrichment/CAS remains W3/W4.

## Native observations v1

`media.search.started/completed/failed` and `media.ingest.started/completed/failed`
are closed app events using the existing shared sanitizer and sink-I/O isolation.
There are two aggregate app records per operation, including DEBUG, never one per
input/unit/vector. Host handlers are not configured or retained by the library.
The version (`schema_version=1`), valid opaque request UUID, attempt count (1; no
hidden retry), input modality, encoding role, selected profile fingerprint, target,
requested/effective mode and terminal reason are observable without content.
Canonical SHA-256 profile identities retain their digest; non-SHA test profile
identities use the shared safe fingerprint wrapper. Source hashes, locators,
queries, texts, vectors, metadata and exception bodies are not event fields.

`source_bytes` measures captured raw file bytes for ingest; prepared queries have
unknown raw source size (`null`). `prepared_input_bytes` counts bytes actually
submitted to attempted encoder calls (encoded image bytes or UTF-8 text), including
failed calls, excluding transport framing/provider-internal instruction prefixes.
No encoder call means a real zero, not an estimate. Provider/extractor call counts,
caller-prepared artifact counts/status and publication status distinguish retained
artifacts from a committed graph. No new extraction occurs in this native route.
Publication is `not_requested` before the guarded indexing call, `failed` when that
call fails, and `published` after confirmed commit, even if a later source/cleanup
check fails. This does not implement W3 tokens or W4 enrichment.

Durations are monotonic milliseconds: initial source resolution, actual provider
awaits, the complete owned read session and guarded indexing/publication. Remaining
orchestration/graph preparation time is the exclusive residual; no fabricated
model/runtime/CPU costs. Non-applicable or not-yet-known sizes/stages/counts are
null/omitted. Search records show applicable families and successfully executed /
failed planned branches, final result/evidence unit/resource counts and degradation.
Core's correlated UUID events remain the owner of candidate/fusion counters; no
second execution or fusion observer is introduced. Unsupported route is a refusal,
not an empty or degraded successful search. Raw sources and provider awaits remain
outside owned read transactions.

The finite O04 oracle exercises 1 versus 32 caller-prepared text observations on
real fresh-v2 image graphs, then hybrid search with identical profile, families,
limits and attempt count. Event names/field sets/counts must be equal; actual
serialized-byte growth is bounded by finite numeric widths of that fixed schema,
not by payloads or vector count. This is a logging bound, not a throughput/SLA or
model-quality benchmark.

The original component tests and external-host CUDA canary below retain their own
cutoffs. A later normal product slice is qualified below; independent real-source
quality/cost/latency fitness and full release acceptance remain separate.

## LM Studio input qualification

The source contract above remains generic; the selected production adapter remains
text-only. A separate loaded-model test on LM Studio **0.4.26+4** with EmbeddingGemma2
Q8_0 returned HTTP200 and one finite normalized 768d vector for text. Actual typed PNG
content sent to `/v1/embeddings` returned HTTP400 at the wrapper's string/array-of-strings
input guard, after model selection but before `model.embed`. The installed worker and
inspected official SDK wire contract use `embedString(inputString)`.

This qualifies that concrete API/input refusal, not absence of model Vision, rejection
of generic prepared providers, or impossibility in future versions. Selected runtime
2.63.0 metadata is not a trace of the executed GPU kernel. The six subsequent repeat,
lossless/changed-pixel, shared-space and invalid-image discriminator cells in that HTTP
experiment were NOT_RUN; later external-host cells do not retroactively execute them.
Text200 is not image-vector or semantic-quality success. The later independent
[short-text A qualification](../evaluation/text-provider-functional-2026-10-24.md)
checks functional DOCUMENT/QUERY semantics, prefix and real caller-factory dispatch;
it does not repair typed PNG admission, qualify native text-document B/E or establish
HTTP text-kernel GPU execution. Textual/native complete spaces remain distinct.

For this HTTP route, supported typed image input and compatible text-query output
remain unmet prerequisites. Removing only its guard is insufficient while its worker /
backend remains string-only. No speculative payload, caption/base64-as-text substitution,
blind app/runtime update or alternative production default is established by that result.

## External-host CUDA qualification

The [independently accepted synthetic CUDA canary](../evaluation/native-image-cuda-canary-2026-10-23.md)
used existing installed LM Studio native components outside MDRack and its HTTP wrapper:
seven fresh cells, six learned 768d vectors, actual post-evaluation vision/backbone CUDA
readbacks and hash/decode-checked original opens. WIC PNG decoding and L2 normalization
remain CPU work. This does not make the normal HTTP adapter image-capable, register a
production provider or qualify all GPU operators, document role or legacy vector spaces.

Those next-product obligations at the canary cutoff are addressed by the separately
qualified implementation below, not retroactive expansion of the canary's evidence.

## Opt-in caller-host product composition

`mdrack.hosts.cuda_encoder.CUDAEncoderProvider(Path(CONFIG))` implements the existing
prepared port at the trusted OUTER composition edge. Engine injection and actual
`image native-ingest` / `image native-search --provider external --host-config CONFIG`
use the normal catalog/Core/public DTO path. Default LM Studio text HTTP and legacy
space remain unchanged; Core/application/default providers do not load weights/vendor
DLLs. See [ADR-0018](../decisions/0018-caller-owned-native-encoder.md) and the
[qualified caller recipe](../../examples/native_cuda/README.md).

The original explicit `mdrack-cuda-abi12-v1` protocol admits only DOCUMENT image/png (224×224 noninterlaced 8-bit RGB,
≤1MiB) and QUERY UTF-8 text for image candidates (≤65536 bytes including the host prefix).
There is no text-document/image-query/similarity or document-vector reuse capability.
Full canonical space binds exact producer/profile, decoder/prefix/pooling/L2/f32 codec;
equal dimensions do not permit old-space vector reuse. Configuration is caller-selected
trusted composition, not resource metadata or a certificate for an unqualified producer.

The separately qualified opt-in `mdrack-cuda-retrieval-v2` adds DOCUMENT UTF-8 text
and one QUERY text capability covering image/text candidates. Python adds exactly one
role prefix (`title: none | text: ` or `task: search result | query: `), preserving the
body and never using a filename as text. Formatted text is bounded to 65,536 bytes;
native tokenization includes prefix/special tokens and refuses more than 2,048 tokens
before forward. There is no truncation or long-context guarantee. V2 keeps the same
image bound, uses at most 120 seconds per native call and has a distinct complete space;
it does not add image-query, SIMILARITY, audio/video or reuse-document-vector roles.
Default/v1 identity and existing catalogs are not rewritten. See the
[independent text/image result](../evaluation/native-text-image-product-2026-10-24.md).

Every call drift-checks pins/config and uses exclusive private prepared files, a fresh
shell-free process, suspended→owned Job→resume, bounded timeout/output, actual evaluated
CUDA evidence and exact raw-to-normalized f32 validation. Wait/reap/temp cleanup also
apply to error/cancel. Optional bounded PRIVATE producer diagnostics are default-off,
not logs/CLI/events/support exports and never input/vector-file retention.

The [independent product result](../evaluation/native-image-product-2026-10-23.md)
qualifies real normal engine/catalog/source CLI/installed PE, public read/original open,
close/reopen and exact DTO/vector/full-space parity. Public read remains redacted:
join its logical identity/whole_image with PUBLIC search evidence locator/revision and
an explicit caller allowlist before opening/hash/decode of an original. A DB locator
or filename alone grants nothing. Same-resource manual lexical enrichment encoded zero,
retained the native projection and used existing CAS; later semantic query counted anew.
The [independent private-caption data review](../evaluation/native-corpus-review-2026-10-24.md)
accepts bounded frozen inputs/shared gold and limited pre-score policy, not image-model
fitness. The later [paired-query DATA companion](../evaluation/paired-query-companion-review-2026-10-24.md)
adds one concrete strict held-out matched-intent group while retaining the whole original
family and its harder cases. Four pooled unknowns, subcatalog-only negative gold and
lack of robust multilingual/abstention model evidence remain explicit. The separate
v2 qualification closes the named native B text-DOCUMENT functional blocker, not the
unchanged A–D/optional-E experiment requirements. Both data families remain separate;
DATA approval does not extend provider roles, compatible spaces, publication rights or
SLOs. Actual corpus quality/cost/latency, W6/operations and release remain OPEN.
