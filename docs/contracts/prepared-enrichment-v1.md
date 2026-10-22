# Prepared enrichment and source reconciliation v1 (app 1.4.0rc2)

The prepared M03-06 component is independently accepted on Windows/CPython 3.14.2
at actual source/local-component and non-editable installed-package boundaries;
W4-R2 is closed. This is not live-runtime, quality/cost or release acceptance.
The independently accepted [ready OCR v1](ready-ocr-v1.md) entry points remain
available with their narrower grammar and policy.

## Ownership and entry points

`MDRackEngine.prepare_enrichment` captures the local snapshot, source generation,
explicit `(purpose, variant)` targets and immediate input revisions **before**
opening the selected artifact. It returns `PreparedEnrichmentOperation`.
`publish_enrichment` checks actual artifact availability again and publishes through
the existing neutral CAS port. `enrich_prepared` combines those phases. No OCR,
ASR, caption, embedding, model, binary decoding or HTTP operation is substituted.

Kinds are `ocr`, `text`, `transcript`, `frames`, and `video`. OCR requires an image;
transcript requires audio/video; frames/video require video. Text is a ready text
projection, not a claim of native image/audio/video inference. Transcript and video
reuse their existing provider-free `.prepare()` builders, never `.ingest()` as a
projector. Timed passages and individual frame captions retain real typed evidence;
extra whole-resource textual aggregates are not silently introduced by enrichment.

The registered CLI mirrors the options:

```text
mdrack --root ./collection resource enrichment-binding resource-id
mdrack --root ./collection resource enrich-prepared resource-id ./ready.json --kind transcript --purpose transcript:audio:whole --no-lexical --dense --artifact-store ./host-archive --require-durable
mdrack --root ./collection resource read-artifact artifact:sha256:DIGEST --artifact-store ./host-archive
```

`--variant default`, `--lexical/--no-lexical`, `--dense/--no-dense`,
`--max-retries 0..3` and optional `--inputs '[["purpose","variant"]]'` are explicit.
Purpose describes coverage/intent, not a producer/model version. A producer or
recipe change is not a reason to invent another independent variant. Query policy
includes explicitly published compatible variants within the existing shared family /
branch candidate limits; stored representations do not each get another allocation.

## Closed artifact envelope

The UTF-8 JSON file is a bounded regular, non-symlink file, at most 4 MiB. Duplicate
keys, extra/missing envelope fields, invalid typed artifacts and digest/binding
mismatches refuse without publishing. The exact fields are:

```json
{
  "contract": "mdrack.prepared-enrichment",
  "version": 1,
  "binding": {
    "resource_id": "logical-id",
    "lifecycle_id": "local-lifecycle",
    "source_namespace": "namespace",
    "source_locator": {"kind": "external_record", "payload": {"ref": "source"}},
    "source_revision": "explicit-generation"
  },
  "artifact_revision": "manual-revision-2",
  "producer": "manual-editor",
  "recipe": "prepared-recipe-v1",
  "manual": true,
  "artifact": "ready text",
  "artifact_sha256": "64-lowercase-hex",
  "dependencies": [],
  "dense": null
}
```

`binding` is exactly the object returned by `prepared_enrichment_binding`. It is
local source/lifecycle evidence, not portable creation authority. Missing legacy
source generations are not reconstructed from `content_hash`, prepared text, a
raw hash, a new snapshot or a rescan. The result explicitly says
`source_validation: catalog_binding_only`: this prepared-artifact API does not
claim to have reopened the original binary source.

`artifact` is text for OCR/text, `TranscriptArtifact.to_dict()` for transcript,
the existing `mdrack.frame-captions.v1` wire document for frames, or exactly
`{"transcript": TRANSCRIPT, "frames": FRAMES}` for video. The SHA-256 is over
`json.dumps(artifact, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
allow_nan=False).encode('utf-8')`, not over a guessed raw-media `content_hash`.

Each dependency is exactly `{"purpose": P, "variant": V, "revision": R}`.
It must match a declared `inputs` slot and its captured active revision. Public
full manifest export exposes the `enrichment_v1` metadata needed by a preparer;
a reduced inspection/count response is not substituted for that provenance.

When dense output is requested, `dense` is exactly:

```json
{
  "space": {
    "space_id": "actual-space", "dimensions": 2, "metric": "dot",
    "fingerprint": "actual-profile", "metadata": {}
  },
  "recipe": "actual-ready-embedding-recipe",
  "artifact_sha256": "same-artifact-digest",
  "vectors": [[0.25, 0.5]]
}
```

Vectors are actual caller-prepared values, ordered by projected component and unit
ordinal; video orders transcript before frames. Counts, dimensions, profiles and
value policies are checked through the existing domain/Core validator and app
canonicalization owner. No placeholder vector or unit is manufactured. Reusing a
non-video artifact revision for changed material/profile is refused: declare a new
revision. Enabling FTS on the same dense artifact does not change its vector.
Video stores distinct content/profile-derived component revisions (and retains
`caller_artifact_revision`): a frame correction alone does not invalidate a
transcript-only dependent.

## Four intents and dependency behavior

| Lexical | Dense | Search graph behavior |
|---|---|---|
| false | false | Real readable artifact only; no Core graph/token/FTS write |
| true | false | Real text, no new vector for this output |
| false | true | Text modality, representation/unit text `None`, actual vector |
| true | true | Real text and actual vector |

These flags describe the requested projection, not inference permissions. Other
native/independent projections, vectors, source metadata and facets survive.
Corrections replace their explicit slot and recursively remove only stale immediate
dependents, including their old FTS rows/vectors. A manual active output cannot be
automatically overwritten, even by a newly started automatic preparation.

Empty typed transcript/frame artifacts and whitespace-only text have `no_text`.
When publication is requested they remove their own old slots/dependents without
an empty Core batch. A remaining native/independent graph is published; removal of
the last projection uses the existing withdrawal port. Artifact-only intent never
depublishes an existing index as a hidden side effect. Availability/integrity errors
are errors, not `no_text`.

A conflict admits at most the requested bounded rebase. Expensive preparation is
not repeated. Source/lifecycle, target, or immediate input changes reject old work;
delete/recreate and withdrawal do not become implicit creation grants. Opaque staged
operations are bound to their owning writer context; reopen uses a new explicit
preparation from retained, still-valid material, not transferred handle authority.

## Real host-owned custody

With `artifact_store=Path(...)` / `--artifact-store`, `DurableArtifactStore` retains
validated bytes in an explicitly selected private archive and returns an opaque
`artifact:sha256:...` reference. Successful `host_durable` means the bytes were
actually copied, flushed, atomically published and read back. Staged publication
then uses the archive, not the caller file, so caller-file/cache removal and process
reopen do not destroy retained material or published FTS/vectors.

The archive has a distinct owner marker and immutable digest entry directories.
It reuses only the existing no-follow/root-anchored OS filesystem adapter, not cache
inventory, keys or eviction policy. Provision it with `initialize()` before sharing
an empty archive among workers. The engine rejects archive roots overlapping its
configured store/cache. The low-level host primitive has no application config;
the host must select a root outside that store. Marked cache descendants,
symlinks/reparse points, unmarked nonempty directories, corrupt/missing entries
and unavailable owners refuse. Original caller files are
never adopted, rewritten or deleted. Only a call's own staging directory is cleaned.
There is no automatic archive purge or cache-driven archival lifecycle.

Without an archive, publication verifies the actual caller file and reports
`caller_owned_verified`, not host durability. `require_durable=True` without a real
owner refuses. `read_enrichment_artifact` / `resource read-artifact` reads historical
material without opening/creating a Core catalog, publishing FTS or granting source
freshness. Missing/corrupt archives report unavailable; existing FTS/vectors are not
erased to simulate artifact availability. Durability covers process/reopen and cache
cleanup, not an unqualified power-loss, backup or external-host-deletion SLA.

## Conditional reindex, rescan, withdrawal and explicit import

`publication_binding`, `prepare_reindex_file`, `publish_reindex`, and
`reindex_resource_file` consume an explicit local envelope:

```json
{
  "contract": "mdrack.reindex-resource", "version": 1,
  "expected": {"resource_id": "logical-id", "lifecycle_id": "local-life", "state_token": "local-token", "status": "published"},
  "owner": "source",
  "batch": {"contract": "mdrack.prepared-resource", "version": 1}
}
```

`batch` must be a complete valid [manifest v1](prepared-resource-manifest-v1.md),
not the abbreviated illustration above. `expected` is the exact current local
publication binding captured by the preparer; it is not added to manifest v1.
The snapshot precedes file I/O. New external custody claims must use the common
artifact preparer; merely importing `retention: host_durable` is refused. Exact
existing artifact projections may be preserved, without reasserting availability.
Reindex preserves other purposes and independent metadata/facets. Source-owned outputs are annotated with their actual ready
material/profile revision. Textual whole-resource aggregates explicitly depend on
their transcript/frame components. Changed raw provenance or source binding without
a new explicit source generation refuses.

With a genuine source transition, old source-dependent projections disappear;
only explicitly source-independent material survives. Legacy unannotated records
are not guessed to be source-independent. The image producer checks real bounded
source bytes/signature and a guarded source check at publication. A changed source
with no new projection withdraws rather than attributing old native/OCR data to new
bytes. Withdrawal keeps the logical lifecycle but offers no invented new graph/source
generation; reentry requires a fresh explicit prepared base or real source producer.

Managed normal transcript/video ingestion captures before `.prepare()`/provider
work and uses this conditional reconciler. For an existing resource, ready typed
artifacts must carry the preparer's original `metadata.enrichment_binding` matching
the local source/lifecycle; it is never filled from a newer snapshot. Initial unbound
complete creation is marked `source_revision_kind: prepared_catalog_binding`, not a
claim of observed raw-media provenance. Unknown/withdrawn/deleted bindings refuse;
use explicit reindex/recreation instead. Explicit legacy injected v1 services keep
their previous boundary; no failed managed publication falls back to them.

CLI: `resource publication-binding ID`, `resource reindex ID FILE`, and
`resource withdraw ID`. Explicit full withdrawal removes all searchable projections
but keeps the lifecycle. Existing configured `resource import`, engine manifest
import, and neutral unconditional replace/delete retain their explicit whole-graph
semantics; they are not secretly changed into merges. This new conditional consumer
is the preserving reindex route. SQLite remains the sole SQL/FTS/state owner; Core,
media contracts, schema 0005 and the lock are unchanged.

## Compatibility and qualification

`DurableArtifactStore`, `PreparedEnrichmentOperation`, and `PreparedReindex` are
additive app exports. All previously accepted 37 names retain their exact order;
the closed app snapshot, installed import oracle and documented inventories contain
exactly those 40 names. No equality check is weakened to subset membership and no
neutral package/version is changed to disguise an inventory mismatch.

See [ADR-0016](../decisions/0016-prepared-enrichment-and-artifact-custody.md).
Component tests use declared synthetic ready artifacts/vectors, actual file SQLite,
FTS and vector reads, public engine/registered CLI and bounded real-connection CAS.
These completed component checks do not claim model capability, live decoding,
relevance, full W5/W6, deployment or release qualification. The existing Core
publication importer inventory is synchronized in the compatibility registry;
whole-repository static debt remains separate from the qualified affected boundary.
