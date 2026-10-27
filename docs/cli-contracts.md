# MDRack CLI Contracts

## Native temporal/media v1 (1.4.0rc3 candidate)

`native ingest PATH --kind frames|audio|video --source-namespace NS --source-ref REF`
performs bounded actual CPU preparation and the same conditional native engine
publication. Range/window/hop/frame-step/unit/prepared-byte budgets are explicit;
canonical media resource ID is generated unless supplied. `native search PATH`
requires kind/start/end and one prepared query unit; candidate modality and target
are independent. `native query --text TEXT|--unit-id ID` reuses the existing planner;
`native capabilities` reports selected adapter declarations without probing a model.
Existing `read unit ID` preserves null native text and temporal locators.

Default production LM Studio embedding capability remains textual and rejects
native inputs before source/decoder/HTTP work. Explicit `--provider synthetic`
uses deterministic byte-hash vectors for real local-component/installed orchestration,
not native inference or relevance. Source and CLI use one standard JSON envelope;
errors are fixed payload-free codes/messages. Full parameters, coverage/precision,
source/recipe/CAS semantics, four pure media records and three builders:
[native temporal v1](contracts/native-temporal-v1.md).

## Explicit recognized app-v2 maintenance (1.4.0rc2)

`mdrack --root ./notes rebuild upgrade --backup ../catalog-backup.sqlite3 --maintenance`
requires ALL consumers stopped and handles closed, plus an absent backup path outside
store. It upgrades only exact completed clean v2/0004 to current 0005; normal open
never migrates. Success data is `{"upgraded": true}` (false for verified target),
refusal is safe `CATALOG_UPGRADE_FAILED`, exit1. No model/source extraction.
See [ADR-0014](decisions/0014-conditional-publication-and-v2-upgrade.md).
Resource deletion's existing boolean is true for a live lifecycle including no graph
after withdrawal, false for never-created/already-deleted. Keys remain unchanged.


## Native image v1 (1.4.0rc1 source candidate)

`image native-ingest PATH --resource-id ID --source-namespace NS --source-ref REF`
and `image native-search --text TEXT|--image-file PATH|--unit-id ID` call the same
engine use case. Candidate scope is independent (`--candidates image|text|all`),
as are target, strict/partial policy, final limit and candidate budget. Exact fields
and limitations: [native-image-v1](contracts/native-image-v1.md).
Default `--provider lmstudio` refuses unsupported native images before source/DB
side effects; `--provider synthetic` is explicit offline testing, not a model.
Only these image native commands additionally accept `--provider external --host-config CONFIG`:
trusted outer composition of a caller-owned bounded CUDA process with the normal engine /
catalog. Missing config or config with another provider refuses with the normal one-envelope
error; there is no implicit fallback/default or other-command factory change. The
original v1 protocol supports image-document/text-query-for-image. Explicit
`mdrack-cuda-retrieval-v2` additionally qualifies short UTF-8 text DOCUMENT and one
text QUERY capability over image/text candidates. **New ordinary text surfaces** are
`MDRackEngine.ingest_native_text(...)`, `native text-ingest PATH`, and
`native text-search --text TEXT`. They require an explicit prepared provider
(`--provider external --host-config CONFIG`, or explicit `--provider synthetic`
for offline orchestration only). `native text-ingest` reuses the canonical
Markdown parser/chunker, source revision and conditional publication; it does not
require the user to prepare a manifest, vector or private Python factory.
`--resource-id` is optional and deterministic when omitted.
`native text-search --candidates text|image|all` uses the existing media-search
planner and `mdrack.media-search.v1` result. Image QUERY, SIMILARITY, audio/video
inference and stored-document-vector reuse remain unsupported by this external
provider.
Exact private setup, lifecycle and limits: [caller recipe](../examples/native_cuda/README.md),
[ADR-0018](decisions/0018-caller-owned-native-encoder.md). Source and actual installed PE
product execution is qualified separately from independent real-corpus quality/SLO;
see the [latest opt-in text/image result](evaluation/native-text-image-product-2026-10-24.md).
No OCR, caption, filename-as-text or hidden provider substitution is performed.
Old `image ingest/search` and text search envelopes remain unchanged.

## Ready OCR publication v1 (1.4.0rc2 source candidate)

`resource ocr-binding RESOURCE_ID` exposes an explicit local source/lifecycle
binding. `resource enrich-ocr RESOURCE_ID READY_JSON --source-image IMAGE_PATH`
uses the shared ready-OCR engine service: lexical-only by default, no inference,
actual caller-owned file verification, bounded conditional merge and honest
`published`/`already_published`/`conflict`/`not_requested` outcomes. Read-only never
publishes FTS; unavailable requested host durability refuses. Changed OCR material
invalidates retained dependent projections even under unchanged source/artifact
revisions. Automatic lexical publication over an active manual slot refuses without
graph/FTS/vector/token mutation. Full grammar, API,
options, source-validation boundary, compatibility and remaining limits:
[ready OCR v1](contracts/ready-ocr-v1.md).

## Prepared enrichment/reindex v1 (1.4.0rc2)

This prepared component is independently qualified on Windows/CPython 3.14.2 in
source/local and non-editable installed boundaries, not live-runtime or release scope.

Registered `resource enrichment-binding`, `enrich-prepared`, `read-artifact`,
`publication-binding`, `reindex` and `withdraw` mirror the corresponding engine
operations. `enrich-prepared ID FILE --kind transcript --purpose PURPOSE` accepts
explicit lexical/dense flags, ready vector/artifact provenance, `--inputs`, bounded
`--max-retries` and optional `--artifact-store` / `--require-durable`. It never calls
models. Read-only does not index text; host durability requires actual retained
bytes outside cache/store. Reindex uses a separate local expected-state envelope
around unchanged manifest v1; explicit `resource import` remains whole-graph import.
Full fields, outcomes, source/lifecycle and archive boundaries:
[prepared enrichment v1](contracts/prepared-enrichment-v1.md).

## Native corpus evaluator search failures

The separate maintainer script `scripts/run_native_evaluation.py run` uses explicit
caller factories and the existing full A–D/optional-E configuration. It is not a new
Click command, a model-acquisition command or a resume-D interface. Its
[runner/input contract](evaluation/native-corpus-v2.md) remains unchanged.

A declared public Core `BranchExecutionError` from the per-query search path is
reported as a BLOCKED cell with constant reason `search_branch_failure`. The full
planned matrix/denominator remains; the query, branch ID and raw exception are not
serialized. With a policy, incomplete execution yields `BLOCKED_COVERAGE`; a missing
policy retains the earlier `BLOCKED_POLICY_MISSING` precedence. These safe usable
reports return exit2, not success from a blocked case. Unexpected programming errors
and cancellation are not swallowed by this runner boundary; the public script retains
its separate safe nonzero refusal boundary and the outer caller owns provider cleanup.

The shared SQLite helper recognizes uppercase FTS5 keywords as explicit syntax;
lower/mixed-case natural conjunctions with punctuation can use the existing literal
retry. Explicit operator/quote/parenthesis/star/column-filter safeguards remain strict.
The [software/partial-pilot report](evaluation/native-fts-repair-2026-10-24.md) separates
this accepted repair from real-data quality, protocol compliance and release fitness.

## Scope

This document is the current S2 contract for the normal local MDRack CLI. All
normal operations use exactly one SQLite catalog:

```text
<store>/catalog.sqlite3
```

`<store>` is the configured `paths.store` relative to `--root` unless it is an
absolute path. There is no normal-command option for selecting a different
catalog.

The first writable operation (`init` or `scan`) creates the fixed catalog. A
read-only operation does not create a catalog. If a pre-S1 store artifact is
present, normal startup fails safely; it neither opens, transfers, nor rewrites
that artifact.

The maintainer-only [one-store acceptance runner](one-store-acceptance.md) is a
Python script, not a Click command or an alternate catalog lifecycle. It creates
only temporary fixture stores and writes bounded evidence outside the checkout.

## JSON envelope

Successful commands write one JSON value to stdout:

```json
{
  "ok": true,
  "data": {},
  "meta": {"command": "command name"}
}
```

Command errors normally write one JSON value with `ok: false`, an error `message`,
a stable `code`, and the command name in `meta`. Completed scan-run statuses retain
the legacy envelope described below; `ok` alone does not prove full completion.
Error messages never include an absolute local path or a caller-provided logical identifier.

## Catalog lifecycle

### `mdrack init`

Creates or opens the fixed catalog without indexing source files.

```json
{
  "ok": true,
  "data": {
    "status": "initialized",
    "catalog_path": "catalog.sqlite3",
    "schema_version": "0005"
  },
  "meta": {"command": "init"}
}
```

The catalog creation is fail-closed. A failed first creation leaves no usable
SQLite database or journal sidecar at the catalog path. Existing non-database
files under the root are outside the catalog lifecycle and are not removed.

### `mdrack scan [--changed] [--provider lmstudio|fake]`

Checks the configured TEXT profile before constructing the external embedding
provider, then creates the fixed catalog if needed and indexes Markdown under
`--root`. A manually changed incompatible profile cannot bypass admission.
`--changed` is accepted but currently ignored: ordinary scans already use change
detection. The command never edits Markdown or fetches linked assets.

The response reports run counts (`files_seen`, `files_indexed`, `files_failed`,
`files_deleted`, `chunks_created`, and `errors_count`) and `data.status`:
`success`, `partial_success`, or `failed`. The legacy partial-result contract is
**`ok: true`, exit 0, `data.status: "partial_success"`**; callers requiring full
completion must inspect status, not just `ok` or absence of an exception. A
completed `failed` indexing run also retains `ok: true` with `data.status: "failed"`,
but exits 1. Existing specific scan errors such as `CORPUS_SCAN_FAILED` retain
their error envelopes. Successfully published resources survive a partial run.

### `mdrack model switch MODEL` and `mdrack rebuild embeddings`

`model switch` checks the requested TEXT identity before constructing a control
client or dispatching list/load/download/probe/unload. `rebuild embeddings` checks
before constructing its external provider. All switch rebuild modes
(`embeddings`, `full`, `none`), `--yes`, `--download`, `--no-load` and force indexing
retain the same compatibility rule. `--rebuild none` still requires `--yes`; that
confirmation never overrides an incompatible or unknown binding. Empty/unbound
TEXT and separate new collections pass this identity rule, subject to the separate
input qualification below; unrelated native/visual spaces do not make the catalog
a single-profile store.

A profile-incompatibility refusal exits 1 with `ok: false`, code
`EMBEDDING_ERROR`, message `Embedding profile is incompatible; use a new collection`,
and `error.details.reason_code: "incompatible_embedding_profile"`. It occurs before
source traversal, run creation, deletion/replacement or config activation. Full
identity, unknown immutable metadata and mixed-space rules are defined in
[TEXT profile admission](current-architecture/indexing-and-chunking.md#text-profile-admission).
A bound compatible switch pins the admitted exact model key/dimensions rather than
using alias discovery or a dimension probe as compatibility proof.

A **performed** switch rebuild adds `data.rebuild.status` on success and must have
status `success` before config is written/activated or the previous model is
unloaded. Partial/failed/missing status or an operation exception cannot activate
that config or unload the previous model. A returned partial/failed switch rebuild
result returns `EMBEDDING_ERROR` with fixed message `Embedding rebuild did not complete successfully`
and details `{ "reason_code": "embedding_rebuild_incomplete", "status": "partial_success" }`
(or `"failed"`). The target may already have been loaded; useful committed resources
are not deleted to manufacture rollback. Config-write failure also prevents the
subsequent previous-model unload. Best-effort provider/control cleanup does not
undo confirmed successful product state.

Standalone `rebuild embeddings` has a different non-success consumer: it exits 1
with an error envelope containing counts, `details.status` and `details.error_codes`,
not the scan command's partial-success envelope. A skipped switch rebuild reports
`performed: false` with its reason and does not invent a completed rebuild status.

### Unqualified LM Studio text recipe

After the existing TEXT-identity check, `scan`, `rebuild embeddings` and
`model switch` refuse **every explicit** `embedding.text_recipe` on the LM Studio
HTTP route. The refusal exits 1 with `ok: false`, `error.code: "EMBEDDING_ERROR"`,
message `Embedding input contract is unqualified for this runtime`, and
`error.details: {"reason_code": "embedding_input_contract_unqualified"}`.
It occurs before HTTP/control client dispatch, source traversal, run/catalog
creation or config activation in these CLI paths. For model switch it also
precedes control construction, including `--rebuild none --yes` and empty/new
collections. A prior profile mismatch still reports `incompatible_embedding_profile`
first. This does not change the scan partial-result envelope above.

There is no supported checked native producer on this ingress. Neither an empty
role string, a model name, a config flag nor fake/response metadata qualifies it;
there is no silent legacy fallback. The unchanged no-recipe HTTP route remains
available. Text-only search does not need an embedding provider; deterministic
fake vectors are offline controls, not native qualification. Configuration and
remaining runtime obligations: [text recipe admission](current-architecture/system-overview.md#lm-studio-text-recipe-and-input-admission).

Direct `LMStudioProvider` calls raise the content-free `LMStudioInputContractError`
subtype of `EmbeddingError`; whole-input byte/UTF-8 validation may refuse an invalid
input first. A directly injected provider in indexing retains the existing per-file
`failed` / `FILE_INDEX_ERROR` result, while `LegacyTextEmbeddingAdapter` retains
`embedding_provider_error`. These wrappers do not publish vectors or report native
success. Other consumers keep their existing error/degradation contracts rather
than acquiring a new success route. Source/offline and bounded installed-console
checks use scripted HTTP/local fixtures, not the selected native runtime.

### Embedded API

`MDRackEngine.scan()` and direct `IndexingService.scan()` use the same application
preflight before `start_run`, traversal or writes; `run_indexer` delegates to that
service. Incompatibility raises `IncompatibleEmbeddingProfileError`; completed scans
return the full `IndexingResult.status`, including partial/failed results. Injected
storage must provide the declared [authoritative metadata read](current-architecture/public-interfaces.md#injected-indexing-storage).
The engine has the same fixed-catalog creation rule. Engine reads
(`search_text`, `get_file`, and `get_chunk`) fail safely when the catalog is
absent and do not create one.

## Current command surface

All commands below share the catalog rule above; none accepts a normal
application catalog-path override.

| Command | Contract |
| --- | --- |
| `init` | Create/open the fixed catalog. |
| `scan` | Index Markdown into the fixed catalog. |
| `search` | Text, semantic, or hybrid retrieval from the fixed catalog. |
| `read chunk` | Read one text chunk by public logical ID. `--context neighbors` returns adjacent text chunks derived from the same core representation. |
| `read unit` | Read any persisted search unit by public logical ID, including timed transcript and frame-caption units, without treating it as a Markdown chunk. |
| `read file` | Read one document resource by public logical ID. |
| `read outline` | Read canonical document headings by file logical ID; heading identities are derived logical IDs, not SQLite or legacy section IDs. |
| `resource import\|export\|inspect\|delete` | Perform one prepared-resource lifecycle operation through the configured catalog; no `--catalog` override exists. |
| `eval retrieval` | Evaluate text, semantic, or hybrid retrieval against the configured catalog; output contains safe ordinal metrics only. |
| `files list` | List document resources in ascending relative-path order with `--page` and `--page-size`. |
| `files info` | Return one document resource by public logical ID. |
| `status`, `doctor` | Report fixed-catalog state and diagnostics. |
| `rebuild fts`, `rebuild embeddings` | Rebuild fixed-catalog derived search data. |
| `model`, `cache` | Manage configured local model/cache behavior without changing catalog selection. |
| `image`, `ingest`, `metadata`, `resources`, `similar`, `find-similar`, `facets` | Operate typed resources in the same fixed catalog. |
| `benchmark`, `storage-analyze` | Read privacy-safe diagnostics from the same fixed catalog. |

## 3f. Unified text search

`mdrack search QUERY --scope all|notes|audio|video|frames|images` uses the
fixed catalog. `--mode text|semantic|hybrid` selects a lexical, vector, or
fused branch; every result uses portable logical identities and source
locators. Current v0.3 preserves the legacy-compatible RRF-only behavior for
hybrid fusion. The command has no alternate-catalog option.

The raw typed filters `--kind`, `--media-type`, `--namespace`,
`--representation`, and `--unit-kind` are accepted only by the explicit
`--preset ... --target resource` path. On generic text, semantic, or hybrid
search they fail before provider or storage access with the payload-free
`VALIDATION_ERROR`; they are never silently ignored.

## 3g. Unified provider-free resource similarity

`mdrack find-similar RESOURCE_ID --scope all|notes|audio|video|images` compares
an existing whole-resource vector in the same fixed catalog. It is
provider-free: it does not create embeddings, load a model, or open another
catalog. The embedded equivalent is `MDRackEngine.find_similar_resource`.

### Document identity and file records

`files list`, `files info`, and `read file` return document records whose `id`
and `logical_id` are the same portable document resource ID. Records contain
only the public projection:

```json
{
  "id": "doc_<stable-id>",
  "logical_id": "doc_<stable-id>",
  "root_id": "default",
  "relative_path": "notes/example.md",
  "title": "Example",
  "source_hash": "<sha256-hex>",
  "indexed_at": "<ISO-8601 timestamp>",
  "status": "active",
  "parser_name": "markdown_it",
  "parser_version": "<parser version>",
  "chunk_strategy_name": "structural_blocks",
  "chunk_strategy_version": "2"
}
```

`files list` wraps the records as:

```json
{
  "files": [],
  "pagination": {
    "page": 0,
    "page_size": 20,
    "total": 0,
    "has_next": false
  }
}
```

`files info` and `read file` wrap one record as `{ "file": { ... } }`. A
missing record returns `NOT_FOUND` and a missing catalog returns `STORAGE_ERROR`.

### Chunk reads

`read chunk <logical-id>` returns `{ "chunk": { ... } }`. The public chunk
record contains its logical ID, text content, unit kind, ordinal,
`heading_path`, and a portable `source_locator`. It never exposes an SQLite
row identifier. With `--context neighbors`, the response additionally contains
`neighbors`, ordered from preceding to following ordinal within that text
representation.

A missing chunk returns `NOT_FOUND`; a missing catalog returns `STORAGE_ERROR`.
If the logical ID names a known timed, frame-caption, or other non-text unit,
`read chunk` returns the payload-free `VALIDATION_ERROR`; it does not project
that unit as a Markdown chunk and does not attempt neighbors.

### Generic unit reads

`read unit <logical-id>` and `MDRackEngine.read_unit(<logical-id>)` return the
same portable eight-field projection for any persisted search unit: logical
unit and resource IDs, representation ID, unit kind, ordinal, text, evidence
locator, and metadata. This is the public continuation for timed transcript
and frame-caption evidence returned by search. It exposes no SQLite row ID or
absolute source path. Unknown IDs return `NOT_FOUND`; a missing catalog returns
`STORAGE_ERROR`.

### Document outlines

`read outline <file-logical-id>` returns `{ "file_logical_id": ..., "headings": [...] }`.
Each heading carries a deterministic `heading_...` logical ID, its `heading_path`,
and optional source line bounds. It does not expose legacy section IDs or SQLite
record IDs. A missing file returns `NOT_FOUND`.

### Resource lifecycle and retrieval evaluation

`resource import`, `resource export`, `resource inspect`, and `resource delete`
operate only on the catalog selected by `--root` and configured `paths.store`.
They do not accept `--catalog`. Inspection exposes only aggregate counts, kinds,
and fingerprints. `eval retrieval --queries PATH` evaluates cases against that
same catalog; its result serializes only ordinal cases, aggregate metrics, and
counts—never query text, source text, file paths, or database IDs.

## Withdrawn command contracts

The following inherited product surfaces are not registered in S2 and therefore
have no normal-operation contract:

- `storage ...`
- `sections ...`
- `read section`

They are not compatibility aliases and must not be used as an alternate catalog
lifecycle. The normal CLI also has no `--catalog` option.

## Database topology

For a successfully initialized normal store, the only SQLite database file is
`<store>/catalog.sqlite3`. SQLite may use transient lock/WAL sidecars while the
catalog is open; those sidecars are not alternate stores. Normal operations do
not create a legacy database, generation directory, or active-pointer file.

## Explicit text ingestion

`mdrack --root ROOT ingest text SOURCE_PATH --source-ref PORTABLE_REF
--media-type text/plain|text/markdown` captures one strict UTF-8 source outside
the scan root and atomically replaces its deterministic `raw_text` resource in
the existing catalog. The command is provider-free and emits only logical IDs,
counts, and `persisted`; source paths, content, digests, and exceptions are not
returned. Search with `--scope all --mode text` exposes portable
`raw_local_source_span` evidence.

## Explicit image ingestion

`mdrack --root ROOT image ingest SOURCE_PATH --resource-id ID
--source-namespace NAMESPACE --source-ref PORTABLE_REF [--caption TEXT]
[--ocr TEXT] [--provider fake|lmstudio]` uses the existing direct-image
surface. `PORTABLE_REF` must be a normalized relative POSIX reference; it is
not a filesystem path. The source must have PNG, JPEG, GIF, or WEBP magic bytes.
The command uses the same conditional producer/reindex path as the engine, preserving
independent projections rather than replacing the whole enriched graph. Automatic
`--ocr` output refuses to overwrite an active manual OCR slot in the same source
generation; ordinary automatic creation/update remains available. The success
envelope remains `image ingest` with the existing result keys. Failures use the
payload-free `IMAGE_INGEST_ERROR` envelope.

## Explicit local WAVE audio ingestion

`mdrack --root ROOT ingest audio SOURCE_PATH --source-ref PORTABLE_REF
--allow-external-stt --stt-command EXECUTABLE [--language LANGUAGE]
[--producer PRODUCER_ID]` accepts only a parseable RIFF/WAVE file outside the
root. The command captures the bounded source privately, sends that snapshot
to the explicitly authorized executable on stdin (`shell=False`), and accepts
only strict `mdrack.timed-transcript.v1` JSON on bounded stdout. It never uses
an embedding provider or a network fallback. The result replaces one `audio`
resource through the existing atomic catalog path with `embeddings=False`.
The success envelope contains only logical IDs, verified media type, counts,
and `persisted`; failures use the payload-free `RAW_AUDIO_INGEST_ERROR`.

## Explicit local ISO-BMFF video ingestion

`mdrack --root ROOT ingest raw-video SOURCE_PATH --source-ref PORTABLE_REF
--allow-external-video-extractor --video-extractor-command EXECUTABLE
[--producer PRODUCER_ID]` accepts an ISO-BMFF file whose `ftyp` signature is
validated and requires the authorized shell-free executable to return strict
`mdrack.raw-video-extraction.v1` JSON with `media_type=video/mp4`, timed
transcript atoms, and 1..600 selected captions. The bounded snapshot is sent
only on stdin; no path, provider, network fallback, or embedding call is used.
The prepared transcript and generated frame artifact are composed once through
the existing video service and replace one `video` resource with
`embeddings=False`. Failures use `RAW_VIDEO_INGEST_ERROR` and never expose
source paths, extractor output, or private content.

## Agent operation over authorized human data

The public CLI is intended for an agent acting under a human operator's explicit
authority. An agent identifies its command boundary (`uv run mdrack` in a source
checkout or a separately installed `mdrack` command), requires an explicit root and
selected source, and starts with `guide`, `--help`, `status`, `doctor`, or text
search. It names `init`, `scan`, explicit ingestion, rebuild, deletion, and
provider/model lifecycle actions before execution because those actions can modify
derived state.

The complete input sequence is Markdown scanning under the selected root plus the
separate raw text/Markdown, direct-image, WAVE, and ISO-BMFF contracts above. The
audio and video adapters run only after their explicit authorization flags and use a
caller-selected local executable with shell-free stdin. Their contracts do not add
built-in transcription, decoding, visual/acoustic search, remote fallback, or a
provider-quality claim.

Use text search with an optional unified `--scope` to find public results. Follow
their logical IDs with the documented read/files commands, and retain portable
locators plus available line or time bounds rather than local paths or raw source
values. A later `status` invocation from a fresh process and the same root reopens
the fixed catalog and reports aggregate state. Source inputs are read-only: MDRack
must not rewrite them, and reports or commits must exclude source content, queries,
private paths, configuration secrets, raw adapter output, database state, and logs.

Automated fake/offline checks are evidence for their fixture and environment only.
A real-use acceptance claim additionally requires an agent to exercise the installed
public CLI or engine on separately authorized human-like private data while checking
source immutability and report privacy. This document does not claim that such a run
has occurred.
