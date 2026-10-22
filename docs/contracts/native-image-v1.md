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
  provider is an offline test tool, never automatic production fallback.

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

Native LM Studio pixels, real-source quality/cost and full release acceptance are
unverified/blocked separately from deterministic local component tests.
