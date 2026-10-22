# Ready OCR publication v1 (app 1.4.0rc2)

The first ready-OCR family is independently accepted on Windows/CPython 3.14.2
at source/local-component and non-editable installed-package boundaries.

This additive application contract publishes caller-prepared whole-image OCR into
an existing native image graph. It does not run OCR, an embedding provider, models,
or HTTP. It consumes the unchanged Core validator and conditional publication port;
SQLite owns graph/state/token/FTS atomicity. This is not full M03-06, live-media,
retrieval-quality or release acceptance.

## Public entry points

`MDRackEngine.ready_ocr_binding(resource_id)` and registered CLI
`resource ocr-binding RESOURCE_ID` return the current **local** resource/lifecycle,
namespace, portable locator, explicit source revision and purpose. No state token is
exported. The lifecycle is not portable authority for another store or recreation.
An old graph without an explicit `source_revision` remains readable but refuses
binding/publication; neither prepared `content_hash`, raw hash nor a fresh rescan
is substituted for missing source-generation provenance.

`MDRackEngine.enrich_ready_ocr(resource_id, artifact_path, variant='default',
lexical=True, dense=False, require_durable=False, max_retries=1, source_path=None)`
uses `ReadyOCRService`. CLI parity:

```text
mdrack --root ./collection resource ocr-binding image-id
mdrack --root ./collection resource enrich-ocr image-id ./ready.json --source-image ./image.png
```

`--variant` is explicit; `--lexical/--no-lexical`, `--dense/--no-dense`,
`--require-durable`, and `--max-retries 0..3` mirror this specialized API. Dense OCR
is refused here: no ready dense projector or model call is silently substituted.
The broader [prepared enrichment API](prepared-enrichment-v1.md) accepts explicit
ready vectors and a real host-owned archive with its own closed envelope.

For staged work, `prepare_ready_ocr` accepts the same preparation options and returns
`ReadyOCROperation`; `publish_ready_ocr(operation, max_retries=1)` publishes it without
re-preparing the artifact. `ReadyOCROperation` and `ReadyOCRError` are additive exports
from `mdrack.public_api`. Operations contain private caller file handles/paths and
are not manifests, durable jobs or serializable publication authority.

## Ready file grammar and custody

The UTF-8 JSON file is a regular, non-symlink, caller-owned retained original, at
most 4 MiB. Duplicate/unknown fields, malformed JSON, stale bindings and a text digest
mismatch refuse without publication. The exact required fields are:

```json
{
  "contract": "mdrack.ready-ocr",
  "version": 1,
  "reference": "caller-artifact-opaque-reference",
  "resource_id": "image-id",
  "lifecycle_id": "local-lifecycle-from-binding",
  "source_namespace": "collection",
  "source_locator": {"kind": "local_image", "payload": {"source_ref": "image.png"}},
  "source_revision": "explicit-source-generation",
  "purpose": "ocr:whole",
  "artifact_revision": "manual-revision-1",
  "producer": "manual-editor",
  "recipe": "ready-ocr-v1",
  "text_sha256": "64-lowercase-hex-digest-of-the-actual-UTF8-text",
  "text": "actual prepared OCR",
  "manual": true
}
```

The digest placeholder must be replaced by actual SHA256. The source/lifecycle
fields come from the explicit binding operation, not an inferred filesystem path.
The caller declares origin; the application validates its completeness, revision,
binding and text bytes, not an external producer signature or OCR quality.

The file is checked before preparation and again before publication; changed or
missing bytes refuse. The original file is not copied into ArtifactCache, removed,
or rewritten by this use case. `caller_owned_verified` means actual available bytes
with retained caller custody, not an archival promise. This specialized API has no
host archive option; `require_durable=True` / `--require-durable` explicitly refuses
rather than trusting a metadata pledge. No retention field is accepted in this file.
Use the separate prepared-enrichment contract for host-owned custody.

Snapshot/lifecycle/token/source binding and immediate input slots are captured
**before artifact or selected-source I/O**. `source_path` / `--source-image` supplies
explicit caller permission to inspect the original image. Existing bounded source
resolution validates its actual bytes against stored raw-source provenance, before
preparation and again before publication, and cleans temporary snapshots. It never
turns the raw hash into the source generation. Result `source_validation` is
`selected_source_and_catalog` in this mode. Without a selected source it is explicitly
`catalog_binding_only`: applicability to the versioned catalog graph is checked,
not freshness of an inaccessible external file. No path is reopened from a DB locator.
External filesystem changes are not locked; unobserved change-and-restore outside
the catalog is not a source-generation transaction.

## Projection and publication

Lexical-only creates genuine nonempty text units for SQLite FTS and no OCR vector.
Read-only (`lexical=False, dense=False`) returns an artifact reference and
`publication_status='not_requested'` with no graph/token mutation. Whitespace-only
text returns `artifact_status='no_text'`, no empty unit and no publication; this is
not withdrawal of an older active OCR slot.

Result keys: `resource_id`, `publication_status`, `artifact_status`,
`artifact_reference`, `retention`, `attempt_count`, `embedding_calls`, `ocr_calls`,
`source_validation`. Publication status is `published`, `already_published`,
`conflict`, or `not_requested`. Artifact status is `prepared`, `read_only`, or
`no_text`. Counters are zero for this ready-only use case, not estimates of earlier
artifact production. CLI success uses the existing envelope, including a computed
but unpublished `conflict`; refusals use exit 1, code `READY_OCR_ERROR`, fixed safe
messages and one JSON value, without text, paths or chained exception details.

Active slot is `(resource, purpose, explicit variant)`. `ocr:whole` declares whole
image coverage; range/track projectors are not invented. Producer, recipe or profile
change does not create a variant. `enrichment_v1` app metadata retains source and
artifact revisions, recipe/producer, artifact reference/digest/custody and immediate
artifact dependencies. A correction replaces its slot and transitively invalidates
only dependent projections. Independent representations, units, vector values,
facets, spaces and resource metadata are preserved; duplicate slot owners, cycles,
space redefinitions and invalid merged graphs refuse. Unannotated legacy records
are not guessed to be dependencies. The generic pure merger supports ready dependent
projections but this file adapter produces only source-dependent whole-image OCR.

A repeat checks current graph postconditions, not merely artifact availability.
Default retry is one remerge after failed CAS (maximum three configurable retries),
with no repeated preparation. Only unchanged lifecycle, source binding/generation,
immediate revisions and target permit remerge. Changed source/manual input and
delete/recreate reject old work even with identical raw hashes. A lost publication
response can be reconciled by reusing the operation and checking current graph.

## Normal image producer rescan and compatibility

Normal app image producers explicitly opt into publication capability; legacy
standalone image-service callers keep their previous capability boundary. Initial
new image publication records a source generation; same-source full preparation
carries a known generation, and an explicitly observed new source with new outputs
gets another generation. Missing legacy generation is never reconstructed.

`MDRackEngine.ingest_image` without a new extractor, prepared-text request or visual
provider is a genuine source-validated ready-projection rescan: it reads/signature-
checks the selected image, checks raw provenance and source binding, and republishes
the complete current graph through bounded guarded CAS. It retains OCR and native
and independent vectors exactly. The broader prepared M03-06 contract reconciles
explicitly prepared producer outputs rather than destructively replacing independent
projections. Observed changed source without new outputs removes stale source-bound
projections (withdrawal if none remain); unknown legacy independence is not guessed.
Explicit provider-driven native ingestion continues to execute that provider,
including its failure/cancellation checks; a
rescan optimization does not suppress explicitly requested model work. Markdown
`scan` remains Markdown-only and is not used as image-rescan evidence.

No old public exports, envelopes or text policies are removed, and no Core/SQLite
schema/API version, package pin or transport changes are required. The broader
[prepared enrichment contract](prepared-enrichment-v1.md) is independently qualified
for the other ready families, preserving reindex and real host custody, in the same
Windows/CPython 3.14.2 source/local and non-editable installed component boundaries.
That broader qualification does not widen this specialized API or imply live/runtime,
quality/cost or release acceptance. See
[ADR-0015](../decisions/0015-ready-ocr-publication.md) and
[ADR-0016](../decisions/0016-prepared-enrichment-and-artifact-custody.md).
